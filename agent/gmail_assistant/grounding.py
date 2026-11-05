"""A question about the mailbox has to be answered from a tool, in the same turn.

The prompt says it, and a model that is in a hurry, or that has remembered "answer in one line, do not list anything",
can still write a plausible digest from nothing. So code checks it: when the user's message asks about their mail and
no tool that reads the mailbox ran in the turn, the reply is not trusted.
"""

import re

READ_TOOLS = {"run_digest", "list_emails", "read_email"}

_ASKS_ABOUT_MAIL = re.compile(
    r"digest|riassunt\w*|\bnon lett[ei]\b|\bunread\b|nuov[ei] (email|mail|messaggi)|new (emails?|mail|messages)|"
    r"ultim[ei] (email|mail|messaggi)|latest (emails?|mail|messages)|(mie|my) (email|mail|posta|inbox)|la mia posta|"
    r"\bleggimi\b|\bread (me )?(my|the)\b|show me my|mostrami|(che|quali) (email|mail)|(what|which) emails|"
    r"\binbox\b|posta in arrivo|ho ricevuto|did i get|(email|mail) (da|from) ",
    re.IGNORECASE,
)

NOT_READ = {
    "Italian": "⚠️ In questo turno non ho letto la tua casella, quindi non ti do un elenco che potrebbe essere inventato. Riprova a chiedermelo.",
    "English": "⚠️ I did not read your mailbox in this turn, so I am not giving you a list that could be made up. Please ask again.",
}
RETRY_NOTE = (
    "\n[Note from the system: you answered without reading the mailbox. Call run_digest, list_emails or read_email now, "
    "and answer only from its result. A remembered preference does not allow you to skip reading the mailbox.]"
)


def asks_about_mail(message: str) -> bool:
    return bool(_ASKS_ABOUT_MAIL.search(message))


def read_the_mailbox(tool_metrics) -> bool:
    return bool(READ_TOOLS & set(tool_metrics))


def needs_reading(message: str, tool_metrics) -> bool:
    """True when the message asks about the mail and nothing read it."""
    return asks_about_mail(message) and not read_the_mailbox(tool_metrics)
