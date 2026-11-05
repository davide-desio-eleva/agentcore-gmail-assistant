"""Stale email data never reaches the model.

A chat is one long session, so its history fills up with lists of emails: the results of the tools, and the model's own
answers. The model copies what it sees, so an old list comes back as if it were current. Before each turn, this module
removes the email data of every turn older than FRESH_MINUTES, from the context of the model. What is stored in
memory is not touched. The turn times come from the line "[Current local time: ...]" that starts each user message.

What stays is what the user can still be talking about: the last few minutes. After that, the model has no list to
copy, so it has to read the mailbox again.
"""

import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from strands.hooks import BeforeInvocationEvent, HookProvider, HookRegistry

FRESH_MINUTES = 10
# Tools whose results are data about emails. The results of the other tools are kept.
READ_TOOLS = {
    "list_emails",
    "read_email",
    "run_digest",
    "list_attachments",
    "unsubscribe_info",
    "send_attachment",
    "set_email_reminder",
    "list_reminders",
    "list_reminder_proposals",
    "list_memories",
}

STALE_RESULT = "(an earlier result about emails, removed because it may be out of date. Call the tool again.)"
STALE_ANSWER = "(an earlier answer about emails, removed because it may be out of date. Read the mailbox again.)"

_TURN_TIME = re.compile(r"\[Current local time: [A-Za-z]+ (\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]")
_EMAIL_ADDRESS = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
# The fields of a tool result that describe an email in words, and the shortest text worth looking for.
_DESCRIPTIONS = ("title", "summary")
_MIN_DESCRIPTION = 12


def _blocks(message: dict) -> list:
    return [b for b in message.get("content", []) if isinstance(b, dict)]


def _turn_time(message: dict) -> datetime | None:
    for block in _blocks(message):
        match = _TURN_TIME.search(str(block.get("text", "")))
        if match:
            return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M")
    return None


def _walk(value, found: set[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _DESCRIPTIONS and isinstance(item, str) and len(item) >= _MIN_DESCRIPTION:
                found.add(item.casefold())
            else:
                _walk(item, found)
    elif isinstance(value, list):
        for item in value:
            _walk(item, found)


def _descriptions(messages: list[dict]) -> set[str]:
    """The titles and summaries that the read tools returned in this conversation. They are what an answer
    made only of descriptions, with no address in it, would repeat."""
    found: set[str] = set()
    names = {}
    for message in messages:
        for block in _blocks(message):
            use = block.get("toolUse")
            if use:
                names[use.get("toolUseId")] = use.get("name")
    for message in messages:
        for block in _blocks(message):
            result = block.get("toolResult")
            if result is None or names.get(result.get("toolUseId")) not in READ_TOOLS:
                continue
            for item in result.get("content", []):
                if "json" in item:
                    _walk(item["json"], found)
                elif "text" in item:
                    try:
                        _walk(json.loads(item["text"]), found)
                    except ValueError:
                        pass
    return found


def _starts_turn(message: dict) -> bool:
    return message.get("role") == "user" and any("text" in block for block in _blocks(message))


def prune_stale(messages: list[dict], now: datetime, fresh_minutes: int = FRESH_MINUTES) -> int:
    """Replace the email data that the model should not trust. Returns how many blocks were replaced.

    Two kinds of data are removed:
    - data read by a tool in a turn older than `fresh_minutes`. It may be out of date.
    - an answer that shows email data in a turn where no tool read the mailbox, whatever its age. It was copied from
      somewhere, and a copy of a list is not a list. Email data is an address, or a title or summary that a read tool
      returned earlier in the conversation.

    A turn without a time, such as one from before the time line existed, counts as old. Nothing is removed from
    the list: roles and the pairs of tool calls and results stay as they are, only their content changes.
    """
    starts = [i for i, message in enumerate(messages) if _starts_turn(message)]
    # Collected before anything is replaced, so that a result about to be removed still counts.
    descriptions = _descriptions(messages)
    replaced = 0
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(messages)
        turn_time = _turn_time(messages[start])
        recent = turn_time is not None and 0 <= (now - turn_time).total_seconds() <= fresh_minutes * 60
        names = {}
        for message in messages[start:end]:
            for block in _blocks(message):
                use = block.get("toolUse")
                if use:
                    names[use.get("toolUseId")] = use.get("name")
        used_read_tool = any(name in READ_TOOLS for name in names.values())
        for message in messages[start:end]:
            for block in _blocks(message):
                result = block.get("toolResult")
                if result is not None and names.get(result.get("toolUseId")) in READ_TOOLS:
                    if not recent:
                        result["content"] = [{"text": STALE_RESULT}]
                        replaced += 1
                elif message.get("role") == "assistant" and "text" in block:
                    text = str(block["text"])
                    shows_emails = _EMAIL_ADDRESS.search(text) is not None or any(
                        description in text.casefold() for description in descriptions
                    )
                    stale_read = used_read_tool and not recent
                    copied = shows_emails and not used_read_tool
                    if stale_read or copied:
                        block["text"] = STALE_ANSWER
                        replaced += 1
    return replaced


class ForgetStaleEmailData(HookProvider):
    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        registry.add_callback(BeforeInvocationEvent, self._prune)

    @staticmethod
    def _prune(event: BeforeInvocationEvent) -> None:
        now = datetime.now(ZoneInfo(os.environ.get("TIMEZONE", "Europe/Rome"))).replace(tzinfo=None)
        prune_stale(event.agent.messages, now)
