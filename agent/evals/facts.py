"""Measures whether the reader gets the facts of an ordinary email right.

Each email has the amounts, dates and names that a summary must keep. The check is crude on purpose: a fact is kept
when its digits or words appear in the summary, the facts or the asks. It calls Bedrock and never touches a mailbox:

    AWS_REGION=eu-west-1 .venv/bin/python -m evals.facts
"""

import json
import re
import sys

from gmail_assistant import reader

EMAILS = [
    ("Bolletta luce", "La tua bolletta di 84,20 euro scade il 10 novembre. Puoi pagarla dall'app o con il bollettino.", ["84,20", "10 novembre"]),
    ("Visita cardiologica", "Le confermiamo la visita cardiologica con il dott. Rossi giovedì 14 novembre alle 15:30 presso il Poliambulatorio Sole.", ["Rossi", "14 novembre", "15:30"]),
    ("Pagamento rifiutato", "Il pagamento di 12,99 euro per l'abbonamento Streamly è stato rifiutato il 3 novembre. Aggiorna la carta entro il 6 novembre.", ["12,99", "6 novembre"]),
    ("Sconto", "Solo fino al 20 novembre: 30% di sconto su tutto il catalogo Casa&Co con il codice AUTUNNO30.", ["30%", "20 novembre", "AUTUNNO30"]),
    ("Riunione", "Ciao, ci vediamo venerdì 8 novembre alle 11 in sala Blu per il progetto Atlas. Porto io le slide.", ["8 novembre", "Atlas"]),
    ("Fattura 2291", "Fattura 2291 di Studio Verdi per 1.250,00 euro, da saldare entro il 30 novembre sull'IBAN indicato.", ["2291", "1.250,00", "30 novembre"]),
]


MONTHS = {
    "gennaio january": 1, "febbraio february": 2, "marzo march": 3, "aprile april": 4, "maggio may": 5,
    "giugno june": 6, "luglio july": 7, "agosto august": 8, "settembre september": 9, "ottobre october": 10,
    "novembre november": 11, "dicembre december": 12,
}


def _numeric(value: str) -> str:
    """Month names, in Italian or English, become numbers, so '10 novembre' and 'November 10' look the same."""
    value = value.lower()
    for names, number in MONTHS.items():
        for name in names.split():
            value = value.replace(name, f"#{number}")
    return value


def _date(value: str):
    day = re.search(r"\b(\d{1,2})\b", _numeric(value))
    month = re.search(r"#(\d{1,2})", _numeric(value))
    return (day.group(1), month.group(1)) if day and month else None


def kept(fact: str, text: str) -> bool:
    date = _date(fact)
    if date:
        day, month = date
        return bool(re.search(rf"(?<!\d){day}(?!\d)\D{{0,3}}#{month}\b|#{month}\b\D{{0,3}}(?<!\d){day}(?!\d)", _numeric(text)))
    simple = lambda value: re.sub(r"[\s.,:]", "", value.lower())
    return simple(fact) in simple(text)


def main() -> int:
    total = missed = 0
    for subject, body, facts in EMAILS:
        summary = reader.summarize_body({"from": "Sender <sender@sender.test>", "subject": subject, "body": body})
        text = json.dumps(summary, ensure_ascii=False)
        lost = [fact for fact in facts if not kept(fact, text)]
        total, missed = total + len(facts), missed + len(lost)
        print(f"{subject:22} {'ok' if not lost else 'missing: ' + ', '.join(lost)}")
    print(f"\nFacts kept: {total - missed}/{total}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
