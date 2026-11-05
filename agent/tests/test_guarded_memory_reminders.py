import pytest

from gmail_assistant import guard, memory, reader, tools


@pytest.fixture
def world(monkeypatch):
    guard.reset()
    calls = []
    monkeypatch.setattr(tools.memory_store, "list_memories", lambda: [
        {"record_id": "rec-1", "namespace": "/users/owner/preferences", "text": '{"preference": "Vuole leggere le email non lette"}'}])
    monkeypatch.setattr(tools.memory_store, "forget_memory", lambda rid, ns: calls.append(("forget", rid, ns)))
    monkeypatch.setattr(tools.reminder_store, "list_reminders", lambda: [
        {"name": "gmail-assistant-reminder-a", "when": "at(2026-11-03T09:00:00)", "text": "Bolletta luce"}])
    monkeypatch.setattr(tools.reminder_store, "cancel_reminder", lambda name: calls.append(("cancel", name)))
    monkeypatch.setattr(tools, "create_email_reminder", lambda mid, subject, when: calls.append(("set", mid, subject, when)))

    class Gmail:
        def read_message(self, mid):
            return {"id": mid, "subject": "Bolletta luce", "body": "x"}

    monkeypatch.setattr(tools, "_gmail", lambda: Gmail())
    monkeypatch.setattr(tools.screen, "is_flagged", lambda text: False)
    yield calls
    guard.reset()


def test_forgetting_a_memory_needs_a_request_and_a_confirmation(world):
    tools.list_memories()
    guard.begin_turn("Cosa ricordi di me?")
    assert "does not ask" in tools.forget_memory("rec-1", "/users/owner/preferences")
    guard.begin_turn("Dimentica la preferenza sulle email")
    first = tools.forget_memory("rec-1", "/users/owner/preferences")
    assert "NOT DONE YET" in first
    assert "Vuole leggere le email non lette" in guard.describe_pending("Italian")
    assert world == []
    guard.begin_turn("sì")
    assert "code" in tools.forget_memory("rec-1", "/users/owner/preferences")
    assert world == []
    guard.begin_turn(guard.STATE.pending["code"])
    assert tools.forget_memory("rec-1", "/users/owner/preferences") == "Forgotten."
    assert world == [("forget", "rec-1", "/users/owner/preferences")]


def test_a_memory_that_was_not_listed_cannot_be_forgotten(world):
    guard.begin_turn("dimentica tutto")
    assert "not listed" in tools.forget_memory("rec-9", "/users/owner/preferences")
    assert world == []


def test_cancelling_a_reminder_needs_a_request_and_a_confirmation(world):
    tools.list_reminders()
    guard.begin_turn("Mandami il digest")
    assert "does not ask" in tools.cancel_reminder("gmail-assistant-reminder-a")
    guard.begin_turn("Annulla il promemoria")
    assert "NOT DONE YET" in tools.cancel_reminder("gmail-assistant-reminder-a")
    guard.begin_turn("ok")
    assert tools.cancel_reminder("gmail-assistant-reminder-a") == "Reminder cancelled."
    assert world == [("cancel", "gmail-assistant-reminder-a")]


def test_a_reminder_is_set_only_on_request_for_an_email_that_was_seen(world):
    guard.remember({"m1": "Bolletta"})
    guard.begin_turn("Mandami il digest")
    assert "does not ask" in tools.set_email_reminder("m1", "2026-11-03T09:00")
    guard.begin_turn("Ricordamelo domani alle 9")
    assert "NOT LISTED YET" in tools.set_email_reminder("unknown", "2026-11-03T09:00")
    assert tools.set_email_reminder("m1", "2026-11-03T09:00").startswith("Reminder set")
    assert world == [("set", "m1", "Bolletta luce", "2026-11-03T09:00")]
    assert guard.STATE.executed == [("set_reminder", 1)]


def test_the_note_after_a_memory_change_does_not_offer_an_undo():
    guard.reset()
    guard.begin_turn("dimentica")
    guard.executed("forget_memory", 1)
    note = guard.describe_executed("Italian")
    assert "dimenticato 1 ricordo" in note and "annullare" not in note


def test_a_memory_record_is_described_in_plain_words():
    assert memory.describe('{"preference": "Vuole le offerte in italiano", "categories": ["x"]}') == "Vuole le offerte in italiano"
    assert memory.describe("testo semplice") == "testo semplice"
    assert reader.clean(memory.describe('{"preference": "a\\u200bb"}'), 80) == "ab"
