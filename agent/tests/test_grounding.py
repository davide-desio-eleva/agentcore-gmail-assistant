import pytest

from gmail_assistant import grounding


@pytest.mark.parametrize(
    "message",
    ["Give me my digest", "Show me my unread emails", "Leggimi le email non lette", "Quali email ho ricevuto oggi?", "Mostrami la posta in arrivo", "Che email mi ha mandato Marco?"],
)
def test_questions_about_the_mail_are_recognised(message):
    assert grounding.asks_about_mail(message)


@pytest.mark.parametrize("message", ["Ciao, come stai?", "Che cartelle ho?", "Sì", "Ricordami domani di pagare", "4821", "Grazie!"])
def test_other_messages_are_not(message):
    assert not grounding.asks_about_mail(message)


def test_a_question_about_the_mail_needs_a_read_tool_in_the_turn():
    assert grounding.needs_reading("Give me my digest", {})
    assert grounding.needs_reading("Give me my digest", {"list_folders": {}})
    assert not grounding.needs_reading("Give me my digest", {"run_digest": {}})
    assert not grounding.needs_reading("Leggimi le email non lette", {"list_emails": {}})
    assert not grounding.needs_reading("Ciao", {})
