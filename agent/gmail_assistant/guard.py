"""Code-level guard in front of every tool that changes the mailbox.

A prompt asks the model to behave. This module makes the code check it. Four rules apply to a change:

1. The user's own message must ask for it (the message comes from the authenticated chat, an email does not).
2. It may only touch emails that appeared in a tool result of this conversation.
3. A change to the Trash, an undo, or a change that brings the emails changed in this turn above BULK_THRESHOLD
   needs a confirmation: the model proposes it, and it runs only if a later message from the user confirms it. The
   request for it is written by code (describe_pending), so the model cannot make it sound smaller than it is. A plain
   yes is enough for light changes. The heavy ones (the Trash, a forgotten memory, a bulk change) need the short code
   that the request shows, so the user has to read it.
4. One turn changes at most MAX_PER_ACTION emails, however many calls the model splits them into.

The state lives in the process. One session runs in one microVM, and a restart empties the state, which
fails closed: the model has to list the emails again before it can change them.
"""

import hashlib
import re
import secrets
import time
from dataclasses import dataclass, field

from . import audit

MAX_PER_ACTION = 10
CODE_DIGITS = 4
# These always need the code. A bulk change needs it as well, whatever the action.
CODE_ACTIONS = {"trash", "forget_memory"}
BULK_THRESHOLD = 5
CONFIRM_TTL_SECONDS = 600

# What the user's message has to contain, per action. Italian and English.
# The verbs are the forms used to ask for something, so that ordinary words ("marketing", "archivio", "button",
# "together", "il volo è annullato") do not count as a request.
INTENT = {
    "mark_as_read": re.compile(r"\b(segna\w*|metti\w*|imposta\w*|mark|set)\b.{0,60}\b(lett[aeio]|read)\b", re.I | re.S),
    "star": re.compile(
        r"\b(metti\w*|aggiungi\w*|togli\w*|rimuovi\w*|segna\w*|star|unstar|add|remove|mark)\b.{0,60}"
        r"\b(stell\w*|important\w*|starred|star)\b|\bunstar\b",
        re.I | re.S,
    ),
    "move_to_folder": re.compile(
        r"\b(sposta\w*|archivia\w*|move(?!\s+on\b)|archive)\b|\b(metti\w*|put)\b.{0,60}\b(cartella|folder|etichetta|label)\b",
        re.I | re.S,
    ),
    "trash": re.compile(r"\b(cestina\w*|elimina\w*|cancella\w*|butta\w*|trash|delete)\b", re.I),
    "undo": re.compile(r"\b(annulla(l[aeio])?|ripristina(l[aeio])?|undo|revert)\b", re.I),
    # The memory and the reminders. The ids here are record ids, reminder names and email ids.
    "forget_memory": re.compile(r"\b(dimentica\w*|cancella\w*|elimina\w*|rimuovi\w*|togli\w*|forget|delete|remove)\b", re.I),
    "cancel_reminder": re.compile(r"\b(annulla\w*|cancella\w*|elimina\w*|rimuovi\w*|togli\w*|cancel|delete|remove)\b", re.I),
    "set_reminder": re.compile(r"\b(ricorda(mi|melo|mela|rmi|rmelo)?|promemoria|remind\w*|reminder)\b", re.I),
    # The file has to be named: "mandami" alone is also how the user asks for the digest.
    "send_attachment": re.compile(r"\b(allegat\w*|attachment\w*|file|pdf|documento|document)\b", re.I),
}

# Actions that change emails. The emails they change in one turn are added up.
_MAILBOX = {"mark_as_read", "star", "move_to_folder", "trash"}

# A confirmation is a message that is only a yes: "sì", "ok procedi", "yes please". "Ok wait, no" or "Si può fare
# domani?" is not one.
_YES = r"(s[iì]|ok|okay|certo|certamente|confermo|conferma|procedi|vai|fallo|va bene|yes|yep|yeah|sure|confirm|go ahead|do it)"
_FILLER = r"(pure|grazie|thanks|please|per favore|perfetto|great)"
_AFFIRMATIVE = re.compile(rf"^\s*{_YES}(\s*[,.!]*\s*({_YES}|{_FILLER}))*\s*[.!👍]*\s*$", re.I)


@dataclass
class Decision:
    allowed: bool
    message: str = ""


@dataclass
class _State:
    turn: int = 0
    user_message: str = ""
    previous_message: str = ""
    seen: dict = field(default_factory=dict)  # message id -> label shown to the model
    pending: dict | None = None
    executed: list = field(default_factory=list)  # (action, count) done in this turn
    changed: set = field(default_factory=set)  # ids of the emails changed in this turn
    refused: list = field(default_factory=list)  # (action, reason) refused in this turn


STATE = _State()


def reset() -> None:
    """Empty the state. Used by tests."""
    STATE.turn = 0
    STATE.user_message = ""
    STATE.previous_message = ""
    STATE.seen = {}
    STATE.pending = None
    STATE.executed = []
    STATE.changed = set()
    STATE.refused = []


def begin_turn(message: str) -> None:
    STATE.turn += 1
    STATE.previous_message = STATE.user_message
    STATE.user_message = message
    STATE.executed = []
    STATE.changed = set()
    STATE.refused = []


def remember(labels: dict) -> None:
    """Record the emails the model has been shown in this conversation."""
    STATE.seen.update(labels)


def request_hash() -> str:
    """A short hash of the user's message, stored in the action log to tie a change to its request."""
    return hashlib.sha256(STATE.user_message.encode()).hexdigest()[:12]


def is_affirmative(message: str) -> bool:
    return bool(_AFFIRMATIVE.match(message))


def confirms(message: str, code: str | None) -> bool:
    """A yes, or, when a code is needed, the code alone or with a yes: "4821", "sì 4821". "No, 4821" is not one."""
    if not code:
        return is_affirmative(message)
    found = re.search(rf"(?<!\d){code}(?!\d)", message)
    if not found:
        return False
    rest = (message[: found.start()] + " " + message[found.end() :]).strip(" \t\n,.!:-")
    return not rest or is_affirmative(rest)


def authorize(
    action: str,
    ids: list[str] | None = None,
    *,
    needs_confirmation: bool = False,
    detail: str = "",
    check_seen: bool = True,
    shown: str = "",
) -> Decision:
    """Decide whether a change may run now. `shown` replaces the list of emails in the confirmation request, for
    changes whose items the model has not listed, such as an undo."""
    ids = list(dict.fromkeys(ids or []))
    in_turn = STATE.changed | set(ids) if action in _MAILBOX else set(ids)
    if len(in_turn) > MAX_PER_ACTION:
        _refused(action, len(in_turn), "cap")
        return Decision(
            False,
            f"NOT DONE. One request can change at most {MAX_PER_ACTION} emails, including the ones changed earlier in "
            "this turn. Ask the user to split it.",
        )

    if check_seen:
        unknown = [i for i in ids if i not in STATE.seen]
        if unknown:
            _refused(action, len(unknown), "unseen")
            return Decision(False, "NOT LISTED YET. Some of these items were not listed in this conversation. List them now with the matching tool (list_emails, list_memories or list_reminders), then call this tool again in this same turn. Do not ask the user to list them, and do not ask the user to confirm anything.")

    key = (action, tuple(sorted(ids)), detail)
    pending = STATE.pending
    live = bool(pending and pending["key"] == key and time.time() - pending["at"] <= CONFIRM_TTL_SECONDS)
    if live and pending["turn"] < STATE.turn:
        if confirms(STATE.user_message, pending["code"]):
            STATE.pending = None
            return _allow(action, ids)
        if pending["code"] and is_affirmative(STATE.user_message):
            # A bare yes is not enough here. The request is shown again, with the same code.
            pending["announce"] = STATE.turn
            return Decision(
                False,
                "NOT DONE YET. A plain yes is not enough for this change: the user has to write the code that the "
                "system shows with this reply. Say so in one short sentence, and do not write the code. "
                "Do not call the tool again until the user answers in a new message.",
            )

    # The model sometimes asks "are you sure?" before it calls the tool. The yes that answers it carries no request, so
    # the request is read from the message before it. It still needs the confirmation below, with its own code.
    asked = INTENT[action].search(STATE.user_message) or (
        is_affirmative(STATE.user_message) and INTENT[action].search(STATE.previous_message)
    )
    if not asked:
        _refused(action, len(ids), "no_request")
        return Decision(False, "NOT DONE. The user's message does not ask for this change. Do not retry it. Tell the user what you could do and let them ask.")

    if ids and (needs_confirmation or len(in_turn) > BULK_THRESHOLD):
        if not (live and pending["turn"] == STATE.turn):  # a second call in the same turn keeps the first request
            heavy = action in CODE_ACTIONS or len(in_turn) > BULK_THRESHOLD
            listed = shown or "\n".join(f"- {STATE.seen.get(i, i)}" for i in ids[:MAX_PER_ACTION])
            STATE.pending = {
                "key": key,
                "turn": STATE.turn,
                "at": time.time(),
                "code": f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}" if heavy else None,
                "action": action,
                "count": len(ids),
                "listed": listed,
                "announce": STATE.turn,
            }
        return Decision(
            False,
            "NOT DONE YET. This change needs the user's confirmation. The system adds the details and the way to confirm "
            "to your reply, so say in one short sentence that you need their confirmation, and do not write the list or "
            "a code yourself. Do not call the tool again until the user answers in a new message.",
        )
    return _allow(action, ids)


def _allow(action: str, ids: list[str]) -> Decision:
    if action in _MAILBOX:
        STATE.changed.update(ids)
    return Decision(True)


def _refused(action: str, count: int, reason: str) -> None:
    """A refusal is a signal: the agent tried something that nobody asked for, or that it was not allowed to do."""
    STATE.refused.append((action, reason))
    audit.write("refused", action, count, request_hash(), reason)


def executed(action: str, count: int) -> None:
    STATE.executed.append((action, count))
    audit.write("executed", action, count, request_hash())


_DONE = {
    "mark_as_read": ("segnate come lette {n} email", "marked {n} email(s) as read"),
    "star": ("messa la stella a {n} email", "starred {n} email(s)"),
    "unstar": ("tolta la stella a {n} email", "removed the star from {n} email(s)"),
    "move_to_folder": ("spostate {n} email", "moved {n} email(s)"),
    "trash": ("spostate nel cestino {n} email", "moved {n} email(s) to the Trash"),
    "undo": ("annullata l'ultima azione su {n} email", "undid the last action on {n} email(s)"),
    "forget_memory": ("dimenticato {n} ricordo", "forgot {n} memory item(s)"),
    "cancel_reminder": ("annullato {n} promemoria", "cancelled {n} reminder(s)"),
    "set_reminder": ("impostato {n} promemoria", "set {n} reminder(s)"),
}
# Only changes to the mailbox can be undone with undo_last_action.
_UNDOABLE = {"mark_as_read", "star", "unstar", "move_to_folder", "trash"}


def describe_executed(language: str) -> str:
    """A fixed line about the changes made in this turn. Code writes it, so it does not depend on the model."""
    if not STATE.executed:
        return ""
    italian = language == "Italian"
    parts = [_DONE[action][0 if italian else 1].format(n=count) for action, count in STATE.executed]
    changed_mail = any(action in _UNDOABLE for action, _ in STATE.executed)
    if italian:
        hint = " Puoi annullare chiedendomi di annullare l'ultima azione." if changed_mail else ""
        return f"\n\n✅ Fatto: {'; '.join(parts)}.{hint}"
    hint = " You can undo it by asking me to undo the last action." if changed_mail else ""
    return f"\n\n✅ Done: {'; '.join(parts)}.{hint}"


_ASK = {
    "mark_as_read": ("segnare come lette {n} email", "mark {n} email(s) as read"),
    "star": ("cambiare la stella a {n} email", "change the star on {n} email(s)"),
    "move_to_folder": ("spostare {n} email", "move {n} email(s)"),
    "trash": ("spostare nel cestino {n} email", "move {n} email(s) to the Trash"),
    "undo": ("annullare l'ultima azione su {n} email", "undo the last action on {n} email(s)"),
    "forget_memory": ("dimenticare {n} ricordo", "forget {n} memory item(s)"),
    "cancel_reminder": ("annullare {n} promemoria", "cancel {n} reminder(s)"),
}


def describe_pending(language: str) -> str:
    """A fixed request for confirmation, for a change that is waiting. Code writes it, from what the guard stored, so
    the model cannot make it sound smaller than it is."""
    pending = STATE.pending
    if not pending or pending.get("announce") != STATE.turn:
        return ""
    italian = language == "Italian"
    action = _ASK.get(pending["action"], ("fare questa modifica", "make this change"))[0 if italian else 1].format(n=pending["count"])
    code = pending["code"]
    if italian:
        how = f"Per confermare scrivi: {code}" if code else "Per confermare rispondi sì."
        return f"\n\n⚠️ In attesa della tua conferma: {action}.\n{pending['listed']}\n{how}"
    how = f"To confirm, write: {code}" if code else "To confirm, reply yes."
    return f"\n\n⚠️ Waiting for your confirmation: {action}.\n{pending['listed']}\n{how}"


_REFUSED = {
    "no_request": ("il tuo messaggio non chiede questa modifica", "your message does not ask for this change"),
    "cap": (f"una richiesta può cambiare al massimo {MAX_PER_ACTION} email: dividila", f"one request can change at most {MAX_PER_ACTION} emails: split it"),
    "unseen": ("non ho potuto verificare quegli elementi in questa conversazione: chiedimi di elencarli e riprova", "I could not check those items in this conversation: ask me to list them and try again"),
}


def describe_refused(language: str) -> str:
    """A fixed line for each change that the guard refused and that was not done in the end, so the reason is the
    guard's and not the model's. A refusal that was followed by a confirmation request or by the change itself is not
    shown: the change went on."""
    pending = STATE.pending if STATE.pending and STATE.pending.get("announce") == STATE.turn else None
    handled = {action for action, _ in STATE.executed} | ({pending["action"]} if pending else set())
    final = list(dict.fromkeys((action, reason) for action, reason in STATE.refused if action not in handled))
    if not final:
        return ""
    italian = language == "Italian"
    lines = [f"⚠️ {'Non eseguito' if italian else 'Not done'} ({action}): {_REFUSED[reason][0 if italian else 1]}." for action, reason in final]
    return "\n\n" + "\n".join(lines)
