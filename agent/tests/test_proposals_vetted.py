from gmail_assistant import proposals, reader


class FakeMail:
    def read_message(self, message_id):
        if message_id == "gone":
            raise KeyError(message_id)
        return {"from": "x", "subject": "s", "body": message_id}


def items(*ids):
    return [{"message_id": i, "subject": i, "remind_at": "2026-11-10T09:00"} for i in ids]


def test_offers_newsletters_and_payments_are_not_reminders(monkeypatch):
    categories = {"bill": "invoice", "visit": "healthcare", "meeting": "personal_work", "offer": "offer", "news": "newsletter", "paid": "payment"}
    monkeypatch.setattr(proposals, "gmail", lambda: FakeMail())
    monkeypatch.setattr(reader, "summarize_body", lambda e: {"category": categories[e["body"]]})
    kept = proposals.vetted(items("bill", "visit", "meeting", "offer", "news", "paid"))
    assert [i["message_id"] for i in kept] == ["bill", "visit", "meeting"]


def test_a_proposal_that_cannot_be_checked_stays(monkeypatch):
    monkeypatch.setattr(proposals, "gmail", lambda: FakeMail())
    assert [i["message_id"] for i in proposals.vetted(items("gone"))] == ["gone"]
    assert proposals.vetted([]) == []
