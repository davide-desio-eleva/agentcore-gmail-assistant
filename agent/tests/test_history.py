from datetime import datetime

from gmail_assistant.history import STALE_ANSWER, STALE_RESULT, prune_stale

NOW = datetime(2026, 11, 2, 15, 0)


def user(text, time):
    return {"role": "user", "content": [{"text": f"[Current local time: Monday {time}]\n{text}"}]}


def call(tool_id, name):
    return {"role": "assistant", "content": [{"toolUse": {"toolUseId": tool_id, "name": name, "input": {}}}]}


def result(tool_id, text):
    return {"role": "user", "content": [{"toolResult": {"toolUseId": tool_id, "status": "success", "content": [{"text": text}]}}]}


def answer(text):
    return {"role": "assistant", "content": [{"text": text}]}


def conversation():
    return [
        user("Leggimi le email non lette", "2026-11-02 09:00"),
        call("t1", "list_emails"),
        result("t1", '[{"id": "a1", "title": "Bolletta"}]'),
        answer("Ecco le email: 1. Bolletta, da boss@corp.example"),
        user("Rileggimele", "2026-11-02 09:30"),
        answer("Sono le stesse: 1. Bolletta da boss@corp.example"),
        user("Che cartelle ho?", "2026-11-02 14:20"),
        call("t2", "list_folders"),
        result("t2", '["r1"]'),
        answer("Hai una cartella"),
        user("Leggimi le email non lette", "2026-11-02 14:55"),
        call("t3", "list_emails"),
        result("t3", '[{"id": "b2", "title": "Nuova mail"}]'),
        answer("Ecco: Nuova mail da nuova@sender.example"),
        user("Cestina la prima", "2026-11-02 14:58"),
    ]


def test_old_email_data_is_removed_from_results_and_answers():
    messages = conversation()
    prune_stale(messages, NOW)
    assert messages[2]["content"][0]["toolResult"]["content"] == [{"text": STALE_RESULT}]
    assert messages[3]["content"][0]["text"] == STALE_ANSWER
    assert messages[5]["content"][0]["text"] == STALE_ANSWER  # a copy of a list, with no tool call in its turn
    assert "boss@corp.example" not in str(messages) and "Bolletta" not in str(messages)


def test_recent_data_stays_so_that_the_next_message_can_refer_to_it():
    messages = conversation()
    prune_stale(messages, NOW)
    assert "b2" in str(messages[12]) and "nuova@sender.example" in str(messages[13])


def test_old_results_of_other_tools_are_kept():
    messages = conversation()
    prune_stale(messages, NOW, fresh_minutes=3)
    assert "r1" in str(messages[8]) and messages[9]["content"][0]["text"] == "Hai una cartella"


def test_the_structure_of_the_conversation_does_not_change():
    messages = conversation()
    shape = [(m["role"], len(m["content"])) for m in messages]
    prune_stale(messages, NOW)
    assert [(m["role"], len(m["content"])) for m in messages] == shape
    assert messages[2]["content"][0]["toolResult"]["toolUseId"] == "t1"
    assert messages[1]["content"][0]["toolUse"]["name"] == "list_emails"


def test_a_turn_without_a_time_counts_as_old():
    messages = [
        {"role": "user", "content": [{"text": "Leggimi le email"}]},
        answer("Ecco: 1. Offerta da shop@store.example"),
        user("Ciao", "2026-11-02 14:59"),
    ]
    prune_stale(messages, NOW)
    assert messages[1]["content"][0]["text"] == STALE_ANSWER


def test_an_answer_without_email_data_is_kept_even_when_old():
    messages = [user("Grazie", "2026-11-02 09:00"), answer("Prego, a disposizione."), user("Ciao", "2026-11-02 14:59")]
    assert prune_stale(messages, NOW) == 0


def test_a_recent_answer_with_email_data_but_no_tool_call_is_removed():
    # The answer is two minutes old, and it still has to go: nothing read the mailbox in that turn.
    messages = [
        user("Leggimi le email non lette", "2026-11-02 14:58"),
        answer("Ecco le tue email: 1. Ordine da ecommerce@nexi.it"),
        user("Cestina la prima", "2026-11-02 14:59"),
    ]
    assert prune_stale(messages, NOW) == 1
    assert messages[1]["content"][0]["text"] == STALE_ANSWER


def test_a_recent_answer_that_follows_a_tool_call_is_kept():
    messages = [
        user("Leggimi le email non lette", "2026-11-02 14:58"),
        call("t9", "list_emails"),
        result("t9", '[{"id": "c3"}]'),
        answer("Ecco le tue email: 1. Ordine da ecommerce@nexi.it"),
        user("Cestina la prima", "2026-11-02 14:59"),
    ]
    assert prune_stale(messages, NOW) == 0


def test_an_answer_made_only_of_descriptions_is_a_copy():
    messages = [
        user("Leggimi le email non lette", "2026-11-02 14:50"),
        call("t1", "list_emails"),
        result("t1", '[{"id": "a1", "title": "Bolletta della luce in scadenza"}]'),
        answer("Hai una mail"),
        user("Cosa dicevi prima?", "2026-11-02 14:55"),
        answer("Dicevo che c'era la Bolletta della luce in scadenza."),
        user("Grazie", "2026-11-02 14:56"),
    ]
    prune_stale(messages, NOW)
    assert messages[5]["content"][0]["text"] == STALE_ANSWER
    assert messages[3]["content"][0]["text"] == "Hai una mail"


def test_a_short_title_does_not_flag_an_ordinary_answer():
    messages = [
        user("Leggimi le email", "2026-11-02 14:50"),
        call("t1", "list_emails"),
        result("t1", '[{"id": "a1", "title": "Altro"}]'),
        answer("Una mail"),
        user("Ok", "2026-11-02 14:55"),
        answer("Altro non c'è."),
    ]
    prune_stale(messages, NOW)
    assert messages[5]["content"][0]["text"] == "Altro non c'è."
