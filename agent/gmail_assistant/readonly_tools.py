"""Read-only tools for the agents that write the digest and the reminders.

They show the text of an email, so the agents that use them get no tool that changes anything. Text that the
guardrail flags is withheld and the email is marked suspicious.
"""

from strands import tool

from . import screen
from .mail import gmail

MAX_BATCH = 50
WITHHELD = "[withheld: the text of this email was flagged as a possible attack]"


@tool(name="list_emails")
def list_emails_raw(query: str = "in:inbox", max_results: int = 20) -> list[dict]:
    """List emails matching a Gmail search query, newest first. An email marked "suspicious" had its text withheld.

    Args:
        query: Gmail search syntax, for example "is:unread in:inbox", "from:bank newer_than:7d" or "label:Invoices".
        max_results: How many emails to return, from 1 to 50.
    """
    emails = gmail().list_messages(query, max(1, min(max_results, MAX_BATCH)))
    flagged = screen.flagged_ids({e["id"]: f"{e['subject']}\n{e['snippet']}" for e in emails})
    for email in emails:
        email["suspicious"] = email["id"] in flagged
        if email["suspicious"]:
            email["subject"], email["snippet"] = WITHHELD, ""
    return emails


@tool(name="read_email")
def read_email_raw(message_id: str) -> dict:
    """Read the full text body of one email. Use it when the snippet is not enough to judge the email.
    An email marked "suspicious" had its text withheld.

    Args:
        message_id: The "id" field of an email returned by list_emails.
    """
    email = gmail().read_message(message_id)
    email["suspicious"] = screen.is_flagged(f"{email['subject']}\n{email['body']}")
    if email["suspicious"]:
        email["subject"], email["body"] = WITHHELD, ""
    return email
