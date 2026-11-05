"""Reminder proposals found in the digest. The digest stores them, and the agent can turn one into a reminder."""

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from functools import cache

import boto3

from . import reader, screen
from .mail import gmail

PROPOSALS_LINE = "PROPOSALS:"
MAX_PROPOSALS = 5
SUSPICIOUS_SUBJECT = "(subject withheld: it looked suspicious)"

# The digest agent reads raw email text, so what it writes is not trusted. A proposal has to look like one.
_MESSAGE_ID = re.compile(r"[0-9a-f]{10,24}")
_REMIND_AT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")


@cache
def _table():
    return boto3.resource("dynamodb").Table(os.environ["ACTION_LOG_TABLE"])


def split_proposals(reply: str) -> tuple[str, list[dict]]:
    """Remove the PROPOSALS line from the digest and return the proposals it contains."""
    lines = reply.rstrip().split("\n")
    if not lines or not lines[-1].strip().startswith(PROPOSALS_LINE):
        return reply, []
    body = "\n".join(lines[:-1]).rstrip()
    try:
        items = json.loads(lines[-1].strip()[len(PROPOSALS_LINE):])
    except ValueError:
        return body, []
    valid = [
        {"message_id": str(item["message_id"]), "subject": reader.clean(item["subject"], 80), "remind_at": str(item["remind_at"])}
        for item in items
        if isinstance(item, dict)
        and _MESSAGE_ID.fullmatch(str(item.get("message_id", "")))
        and reader.clean(item.get("subject"), 80)
        and _REMIND_AT.fullmatch(str(item.get("remind_at", "")))
    ]
    return body, valid[:MAX_PROPOSALS]


# A reminder is for a bill or an appointment. What the reader calls an offer, a newsletter or a payment (a receipt, a
# refused charge) is not one, whatever the digest model proposed.
NOT_A_REMINDER = {"offer", "newsletter", "payment"}


def _category(item: dict) -> str | None:
    try:
        email = gmail().read_message(item["message_id"])
        return reader.summarize_body({"from": email["from"], "subject": email["subject"], "body": email["body"]})["category"]
    except Exception as error:
        print(f"proposal check failed: {type(error).__name__}")
        return None  # unknown: the proposal stays, it is only a suggestion


def vetted(items: list[dict]) -> list[dict]:
    """The proposals whose email is not an offer, a newsletter or a payment, as the reader sees it."""
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=len(items)) as pool:
        categories = list(pool.map(_category, items))
    return [item for item, category in zip(items, categories) if category not in NOT_A_REMINDER]


def screened(items: list[dict]) -> list[dict]:
    """The proposals, with the subject withheld where the guardrail flags it. A subject is chosen by a stranger."""
    flagged = screen.flagged_ids({str(n): item["subject"] for n, item in enumerate(items)})
    return [{**item, "subject": SUSPICIOUS_SUBJECT} if str(n) in flagged else item for n, item in enumerate(items)]


def save(items: list[dict]) -> None:
    _table().put_item(Item={"owner": "proposals", "ts": "latest", "items": screened(items)})


def load() -> list[dict]:
    item = _table().get_item(Key={"owner": "proposals", "ts": "latest"}).get("Item")
    return item["items"] if item else []


def render(items: list[dict]) -> str:
    if not items:
        return ""
    lines = ["", "Promemoria proposti:"]
    for number, item in enumerate(items, start=1):
        lines.append(f"{number}. {item['subject']}: ricordo il {item['remind_at'].replace('T', ' alle ')}")
    lines.append("Vuoi che ti ricordi una di queste? Dimmi il numero.")
    return "\n".join(lines)
