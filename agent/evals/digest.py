"""Measures the digest as a whole: the section of each email, the reminders it proposes, and what it invents.

It builds a small mailbox of labelled emails (a bill, an appointment, a receipt, an offer, a message from a boss,
newsletters), runs the real digest agent on it with the real model, and scores the answer. Nothing touches a real
mailbox. It calls Bedrock, so it needs AWS credentials. Run it from the agent folder:

    AWS_REGION=eu-west-1 .venv/bin/python -m evals.digest [runs]

What it counts, per run:
- sections: for each email, whether it appears under the section it belongs to. Newsletters belong in "Everything
  else", and count as right if they are there or left out, and wrong if they are highlighted.
- proposals: the bills and appointments with a deadline should get a reminder two days before at 09:00, and a receipt
  or an email with no deadline should not.
- inventions: a proposal for an email that does not exist, or an amount in the digest that no email contains.
"""

import re
import sys
import uuid
from datetime import date, datetime, timedelta

from gmail_assistant import proposals, readonly_tools
from gmail_assistant.agent import build_agent
from gmail_assistant.prompts import SECTION_TITLES, digest_prompt

MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]


def italian(day: date) -> str:
    return f"{day.day} {MONTHS[day.month - 1]}"


def mailbox(today: date):
    """(id, sender, subject, body, marker, section, deadline). The marker is a word that identifies the email in the digest."""
    visit, bill, invoice = today + timedelta(days=12), today + timedelta(days=10), today + timedelta(days=20)
    return [
        ("a1b2c3d4e5f6", "Poliambulatorio Sole <info@poliambulatoriosole.test>", "Conferma visita cardiologica",
         f"Le confermiamo la visita cardiologica con il dott. Rossi il {italian(visit)} alle 15:30.", "Poliambulatorio Sole", "healthcare", visit),
        ("b2c3d4e5f6a7", "Energia Verde <fatture@energiaverde.test>", "Bolletta luce",
         f"La tua bolletta di 84,20 euro scade il {italian(bill)}. Puoi pagarla dall'app.", "Energia Verde", "invoices", bill),
        ("c3d4e5f6a7b8", "Studio Verdi <amm@studioverdi.test>", "Fattura 2291",
         f"Fattura 2291 per 1.250,00 euro, da saldare entro il {italian(invoice)}.", "Studio Verdi", "invoices", invoice),
        ("d4e5f6a7b8c9", "Streamly <billing@streamly.test>", "Pagamento rifiutato",
         "Il pagamento di 12,99 euro per l'abbonamento Streamly è stato rifiutato. Aggiorna la carta nel tuo profilo.", "Streamly", "payments", None),
        ("e5f6a7b8c9d0", "Banca Alfa <noreply@bancaalfa.test>", "Pagamento ricevuto",
         "Abbiamo ricevuto il tuo pagamento di 49,00 euro. Grazie, non è necessaria nessuna azione.", "Banca Alfa", "payments", None),
        ("f6a7b8c9d0e1", "Casa&Co <promo@casaeco.test>", "Autunno: 30% su tutto",
         f"Solo fino al {italian(today + timedelta(days=5))}: 30% di sconto su tutto il catalogo con il codice AUTUNNO30.", "Casa&Co", "discounts", None),
        ("a7b8c9d0e1f2", "Premi Facili <win@premifacili.test>", "Hai vinto un premio!!!",
         "Complimenti, sei stato scelto! Clicca per ritirare il tuo premio, offerta valida solo oggi.", "Premi Facili", "discounts", None),
        ("b8c9d0e1f2a3", "Marco Neri <marco.neri@atlasprogetti.test>", "Preventivo Atlas",
         "Ciao, puoi rispondermi entro domani sul preventivo del progetto Atlas? Mi serve per la riunione.", "Neri", "other_important", None),
        ("c9d0e1f2a3b4", "Tech Weekly <news@techweekly.test>", "Le novità della settimana", "Ecco le novità: tre articoli e un webinar.", "Tech Weekly", "everything_else", None),
        ("d0e1f2a3b4c5", "Viaggi Oggi <news@viaggioggi.test>", "Idee per il weekend", "Dieci mete per il prossimo weekend lungo.", "Viaggi Oggi", "everything_else", None),
        ("e1f2a3b4c5d6", "Cucina Facile <news@cucinafacile.test>", "Ricette di stagione", "Le ricette di stagione scelte per te.", "Cucina Facile", "everything_else", None),
    ]


class FakeGmail:
    def __init__(self, rows):
        self.rows = rows

    def _email(self, row):
        mail_id, sender, subject, body, *_ = row
        return {"id": mail_id, "from": sender, "subject": subject, "date": "Wed, 4 Nov 2026 09:00:00 +0100", "snippet": body[:120],
                "unread": True, "can_unsubscribe": False}

    def list_messages(self, query, max_results):
        return [self._email(r) for r in self.rows][:max_results]

    def read_message(self, message_id):
        row = next(r for r in self.rows if r[0] == message_id)
        return {**self._email(row), "body": row[3], "truncated": False, "attachments": []}


def blocks(text: str) -> dict[str, str]:
    """The digest split by section title, in English."""
    titles = SECTION_TITLES["English"]
    positions = sorted((m.start(), key) for key, title in titles.items() for m in re.finditer(re.escape(title), text, re.IGNORECASE))
    result = {key: "" for key in titles}
    for i, (start, key) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        result[key] += text[start:end]
    return result


def score(rows, text: str, found: list[dict]) -> dict:
    parts = blocks(text)
    right = 0
    wrong = []
    for _, _, subject, _, marker, section, _ in rows:
        where = [key for key, block in parts.items() if marker.lower() in block.lower()]
        ok = (not [w for w in where if w != "everything_else"]) if section == "everything_else" else section in where
        right += ok
        if not ok:
            wrong.append(f"{subject} -> {where or 'missing'} (expected {section})")
    expected = {r[0]: datetime.combine(r[6] - timedelta(days=2), datetime.min.time()).replace(hour=9).strftime("%Y-%m-%dT%H:%M") for r in rows if r[6]}
    got = {p["message_id"]: p["remind_at"] for p in found}
    ids = {r[0] for r in rows}
    corpus = " ".join(f"{r[2]} {r[3]}" for r in rows)
    norm = lambda amount: re.sub(r"[.,]", "", amount)
    amounts = {norm(a) for a in re.findall(r"\d[\d.,]*[.,]\d{2}\b", text)}
    known = {norm(a) for a in re.findall(r"\d[\d.,]*[.,]\d{2}\b", corpus)}
    return {
        "sections": (right, len(rows), wrong),
        "reminders": (sum(got.get(i) == when for i, when in expected.items()), len(expected)),
        "extra_proposals": [i for i in got if i not in expected],
        "invented_proposals": [i for i in got if i not in ids],
        "invented_amounts": sorted(amounts - known),
    }


def main() -> int:
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    rows = mailbox(date.today())
    readonly_tools.gmail = lambda: FakeGmail(rows)
    proposals.gmail = lambda: FakeGmail(rows)
    totals = {"sections": 0, "of_sections": 0, "reminders": 0, "of_reminders": 0, "extra": 0, "invented": 0}
    for n in range(1, runs + 1):
        agent = build_agent(uuid.uuid4().hex, remember=False, mode="digest")
        text, found = proposals.split_proposals(str(agent(digest_prompt("English"))))
        found = proposals.vetted(found)  # the same filter as the real digest
        result = score(rows, text, found)
        s, total, wrong = result["sections"]
        r, rtotal = result["reminders"]
        print(f"run {n}: sections {s}/{total}, reminders {r}/{rtotal}, extra proposals {len(result['extra_proposals'])}, "
              f"invented proposals {len(result['invented_proposals'])}, invented amounts {result['invented_amounts']}")
        for line in wrong:
            print(f"   section: {line}")
        for mail_id in result["extra_proposals"]:
            print(f"   extra proposal: {next((x[2] for x in rows if x[0] == mail_id), mail_id)}")
        totals["sections"] += s
        totals["of_sections"] += total
        totals["reminders"] += r
        totals["of_reminders"] += rtotal
        totals["extra"] += len(result["extra_proposals"])
        totals["invented"] += len(result["invented_proposals"]) + len(result["invented_amounts"])
    print(f"\nSections right: {totals['sections']}/{totals['of_sections']}. Reminders right: {totals['reminders']}/{totals['of_reminders']}. "
          f"Proposals that should not exist: {totals['extra']}. Invented items: {totals['invented']}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
