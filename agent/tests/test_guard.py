import time

import pytest

from gmail_assistant import guard


@pytest.fixture(autouse=True)
def clean_state():
    guard.reset()
    yield
    guard.reset()


def seen(*ids):
    guard.remember({i: f"Subject {i}" for i in ids})


def test_a_change_the_user_did_not_ask_for_is_refused():
    seen("a")
    guard.begin_turn("Mandami il digest")
    decision = guard.authorize("mark_as_read", ["a"])
    assert not decision.allowed and "does not ask" in decision.message


def test_a_small_requested_change_is_allowed():
    seen("a", "b")
    guard.begin_turn("Segna come lette le email 1 e 2")
    assert guard.authorize("mark_as_read", ["a", "b"]).allowed


@pytest.mark.parametrize(
    "action,message",
    [
        ("mark_as_read", "mark them as read"),
        ("mark_as_read", "segna come lette la prima"),
        ("star", "metti la stella alla seconda"),
        ("star", "togli la stella a quella email"),
        ("star", "star the first one, it is important"),
        ("move_to_folder", "sposta le newsletter in Newsletters"),
        ("move_to_folder", "move number 3 to the folder Invoices"),
        ("trash", "cestina la 4"),
        ("trash", "delete the second one"),
        ("undo", "annulla l'ultima azione"),
        ("undo", "undo that"),
    ],
)
def test_the_request_is_recognised_in_italian_and_english(action, message):
    seen("a")
    guard.begin_turn(message)
    decision = guard.authorize(action, ["a"] if action != "undo" else None, needs_confirmation=False)
    assert decision.allowed or "confirmation" in decision.message


def test_emails_the_user_has_not_seen_are_refused():
    guard.begin_turn("segna come lette tutte")
    decision = guard.authorize("mark_as_read", ["unknown"])
    assert not decision.allowed and "not listed" in decision.message


def test_the_trash_needs_a_confirmation_in_a_later_message():
    seen("a")
    guard.begin_turn("cestina la prima")
    first = guard.authorize("trash", ["a"], needs_confirmation=True)
    assert not first.allowed and "confirmation" in first.message
    # Same turn: the model cannot confirm its own proposal, and a second call keeps the first request and its code.
    code = guard.STATE.pending["code"]
    assert not guard.authorize("trash", ["a"], needs_confirmation=True).allowed
    assert guard.STATE.pending["code"] == code
    guard.begin_turn(code)
    assert guard.authorize("trash", ["a"], needs_confirmation=True).allowed


def test_a_confirmation_only_runs_what_was_proposed():
    seen("a", "b")
    guard.begin_turn("cestina la prima")
    guard.authorize("trash", ["a"], needs_confirmation=True)
    guard.begin_turn("sì")
    assert not guard.authorize("trash", ["a", "b"], needs_confirmation=True).allowed


def test_a_message_that_is_not_a_yes_does_not_confirm():
    seen("a")
    guard.begin_turn("cestina la prima")
    guard.authorize("trash", ["a"], needs_confirmation=True)
    guard.begin_turn("mandami il digest")
    assert not guard.authorize("trash", ["a"], needs_confirmation=True).allowed


def test_a_proposal_expires():
    seen("a")
    guard.begin_turn("cestina la prima")
    guard.authorize("trash", ["a"], needs_confirmation=True)
    guard.STATE.pending["at"] -= guard.CONFIRM_TTL_SECONDS + 1
    guard.begin_turn("sì")
    assert not guard.authorize("trash", ["a"], needs_confirmation=True).allowed


def test_a_change_to_many_emails_needs_a_confirmation():
    ids = [f"m{i}" for i in range(guard.BULK_THRESHOLD + 1)]
    seen(*ids)
    guard.begin_turn("segna come lette queste")
    assert not guard.authorize("mark_as_read", ids).allowed
    guard.begin_turn(f"ok {guard.STATE.pending['code']}")
    assert guard.authorize("mark_as_read", ids).allowed


def test_one_action_has_a_cap():
    ids = [f"m{i}" for i in range(guard.MAX_PER_ACTION + 1)]
    seen(*ids)
    guard.begin_turn("segna come lette tutte")
    assert "at most" in guard.authorize("mark_as_read", ids).message


def test_undo_needs_the_request():
    guard.begin_turn("annulla l'ultima azione")
    assert guard.authorize("undo").allowed
    guard.begin_turn("mandami il digest")
    assert not guard.authorize("undo").allowed


@pytest.mark.parametrize(
    "action, message",
    [
        ("mark_as_read", "show me the marketing emails I have not read"),
        ("move_to_folder", "ok move on, what else came in?"),
        ("move_to_folder", "cosa c'è nell'archivio?"),
        ("trash", "the unsubscribe button does not work"),
        ("trash", "what is in the bin?"),
        ("undo", "il mio volo è stato annullato"),
        ("star", "let's put this together"),
        ("set_reminder", "ti ricordi cosa ti ho detto?"),
    ],
)
def test_ordinary_words_are_not_a_request(action, message):
    seen("a")
    guard.begin_turn(message)
    assert not guard.authorize(action, ["a"] if action != "undo" else None).allowed


@pytest.mark.parametrize("message", ["sì", "Si!", "ok", "ok procedi", "sì, grazie", "yes please", "go ahead", "va bene 👍"])
def test_a_plain_yes_confirms(message):
    assert guard.is_affirmative(message)


@pytest.mark.parametrize("message", ["ok wait, no", "Okay, actually do not", "vai piano, no", "Si può fare domani?", "sì ma solo la prima"])
def test_a_yes_with_anything_else_does_not_confirm(message):
    assert not guard.is_affirmative(message)


def test_the_request_hash_ties_a_change_to_its_message():
    guard.begin_turn("segna come lette la prima")
    first = guard.request_hash()
    guard.begin_turn("segna come lette la seconda")
    assert first != guard.request_hash() and len(first) == 12


def test_the_change_note_is_written_by_code_in_the_users_language():
    guard.begin_turn("segna come lette 2 email")
    guard.executed("mark_as_read", 2)
    note = guard.describe_executed("Italian")
    assert "segnate come lette 2 email" in note and "annullare" in note
    assert "marked 2 email(s) as read" in guard.describe_executed("English")


def test_there_is_no_note_when_nothing_changed():
    guard.begin_turn("mandami il digest")
    assert guard.describe_executed("Italian") == ""


def test_an_undo_alone_does_not_suggest_another_undo():
    guard.begin_turn("annulla")
    guard.executed("undo", 3)
    assert "Puoi annullare" not in guard.describe_executed("Italian")


def test_a_heavy_change_needs_the_code_and_a_bare_yes_is_refused():
    seen("a")
    guard.begin_turn("cestina la prima")
    guard.authorize("trash", ["a"], needs_confirmation=True)
    code = guard.STATE.pending["code"]
    assert len(code) == guard.CODE_DIGITS
    guard.begin_turn("sì")
    refused = guard.authorize("trash", ["a"], needs_confirmation=True)
    assert not refused.allowed and "code" in refused.message
    assert guard.STATE.pending["code"] == code  # the same code, shown again
    assert "Per confermare scrivi: " + code in guard.describe_pending("Italian")
    wrong = "0000" if code != "0000" else "1111"
    guard.begin_turn(wrong)
    assert not guard.authorize("trash", ["a"], needs_confirmation=True).allowed
    guard.begin_turn(f"no, {code}")
    assert not guard.authorize("trash", ["a"], needs_confirmation=True).allowed
    guard.begin_turn(f"Sì, {code}")
    assert guard.authorize("trash", ["a"], needs_confirmation=True).allowed


def test_a_light_change_needs_only_a_yes_and_asks_for_it():
    guard.begin_turn("annulla l'ultima azione")
    guard.authorize("undo", ["a"], needs_confirmation=True, check_seen=False, shown="- a")
    assert guard.STATE.pending["code"] is None
    assert "rispondi sì" in guard.describe_pending("Italian") and "annullare l'ultima azione su 1 email" in guard.describe_pending("Italian")
    guard.begin_turn("sì")
    assert guard.authorize("undo", ["a"], needs_confirmation=True, check_seen=False, shown="- a").allowed


def test_the_request_is_written_by_code_from_what_the_guard_stored():
    seen("a", "b")
    guard.begin_turn("cestina le prime due")
    guard.authorize("trash", ["a", "b"], needs_confirmation=True)
    line = guard.describe_pending("English")
    assert "move 2 email(s) to the Trash" in line and guard.STATE.pending["code"] in line and "Subject a" in line
    guard.begin_turn("e le altre?")
    assert guard.describe_pending("English") == ""  # nothing is announced in a turn that did not ask


def test_the_code_alone_confirms_and_digits_inside_a_longer_number_do_not():
    assert guard.confirms("4821", "4821") and guard.confirms("ok 4821!", "4821") and guard.confirms("4821 sì", "4821")
    assert not guard.confirms("48210", "4821") and not guard.confirms("sì", "4821") and not guard.confirms("no 4821", "4821")


def test_a_yes_after_the_model_asked_first_still_goes_through_the_confirmation():
    seen("a")
    guard.begin_turn("cestina la prima")
    guard.begin_turn("sì")  # the model asked "are you sure?" itself, without calling the tool
    first = guard.authorize("trash", ["a"], needs_confirmation=True)
    assert not first.allowed and "NOT DONE YET" in first.message
    code = guard.STATE.pending["code"]
    guard.begin_turn(code)
    assert guard.authorize("trash", ["a"], needs_confirmation=True).allowed


def test_a_yes_does_not_borrow_a_request_that_was_not_about_this_action():
    seen("a")
    guard.begin_turn("che email ho?")
    guard.begin_turn("sì")
    assert "does not ask" in guard.authorize("trash", ["a"], needs_confirmation=True).message


def test_refusals_and_changes_are_written_to_the_audit_trail(monkeypatch):
    written = []
    monkeypatch.setattr(guard.audit, "write", lambda *a, **k: written.append(a))
    seen("a")
    guard.begin_turn("che email ho?")
    guard.authorize("trash", ["a"], needs_confirmation=True)  # nobody asked
    guard.authorize("star", ["zzz"])  # never listed
    guard.executed("mark_as_read", 2)
    assert [(w[0], w[1], w[4] if len(w) > 4 else "") for w in written] == [
        ("refused", "trash", "no_request"),
        ("refused", "star", "unseen"),
        ("executed", "mark_as_read", ""),
    ]
    assert all(len(w[3]) == 12 for w in written)  # only the hash of the request, never its text


def test_a_final_refusal_is_described_by_code_and_a_handled_one_is_not():
    seen("a")
    guard.begin_turn("che email ho?")
    guard.authorize("trash", ["a"], needs_confirmation=True)  # nobody asked
    line = guard.describe_refused("Italian")
    assert "Non eseguito (trash)" in line and "non chiede questa modifica" in line
    assert "Not done (trash): your message does not ask for this change" in guard.describe_refused("English")
    # An unlisted item that the model then lists and retries is not shown as refused.
    guard.begin_turn("cestina la prima")
    assert guard.authorize("trash", ["zzz"], needs_confirmation=True).message.startswith("NOT LISTED YET")
    seen("zzz")
    guard.authorize("trash", ["zzz"], needs_confirmation=True)
    assert guard.describe_refused("English") == ""
    assert guard.describe_pending("English") != ""
