"""What is written to AgentCore Memory.

The session manager stores every message of the chat as an event, and the long-term strategies learn from those events.
Email data does not belong there: it is out of date within minutes, and the strategies would keep it as if it were a
fact about the user. So before a message is stored, the results of the tools that read email, and the answers that show
email data, are replaced by a note. The messages that the agent keeps in the running session are not touched.
"""

import json
import re
from typing import Tuple

from bedrock_agentcore.memory.integrations.strands.bedrock_converter import AgentCoreMemoryConverter
from strands.types.session import SessionMessage

from .grounding import RETRY_NOTE
from .history import READ_TOOLS

NOT_STORED_RESULT = "(email data, not stored)"
NOT_STORED_ANSWER = "(an answer with email data, not stored)"
NOT_STORED_NOTE = "(a note from the system, not stored)"

_EMAIL_ADDRESS = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")

# The names of the tools called in the current turn, and whether one of them read email. The messages arrive in order,
# a tool call first and its result next, so this is enough to know what a result belongs to.
_tool_names: dict[str, str] = {}
_turn_read_email = False


def reset() -> None:
    """Forget the state of the turn. Used by tests."""
    global _turn_read_email
    _tool_names.clear()
    _turn_read_email = False


def redact_for_storage(message: dict) -> dict:
    """A copy of the message without email data. Roles, tool call ids and the user's own words stay."""
    global _turn_read_email
    role = message.get("role")
    blocks = [dict(block) if isinstance(block, dict) else block for block in message.get("content", [])]
    if role == "user" and any(isinstance(b, dict) and "text" in b for b in blocks):
        _turn_read_email = False  # a new message from the user starts a new turn
    for block in blocks:
        # The note that asks the model to read the mailbox is an instruction of mine, not a preference of the user.
        if role == "user" and isinstance(block, dict) and str(block.get("text", "")).strip() == RETRY_NOTE.strip():
            block["text"] = NOT_STORED_NOTE
    for block in blocks:
        use = block.get("toolUse") if isinstance(block, dict) else None
        if use:
            _tool_names[use.get("toolUseId")] = use.get("name")
            _turn_read_email = _turn_read_email or use.get("name") in READ_TOOLS
    for block in blocks:
        if not isinstance(block, dict):
            continue
        result = block.get("toolResult")
        if result is not None and _tool_names.get(result.get("toolUseId")) in READ_TOOLS:
            block["toolResult"] = {**result, "content": [{"text": NOT_STORED_RESULT}]}
        elif role == "assistant" and "text" in block:
            if _turn_read_email or _EMAIL_ADDRESS.search(str(block["text"])):
                block["text"] = NOT_STORED_ANSWER
    return {**message, "content": blocks}


class RedactingConverter(AgentCoreMemoryConverter):
    """The default converter, with the redaction before the message is turned into an event."""

    @staticmethod
    def message_to_payload(session_message: SessionMessage) -> list[Tuple[str, str]]:
        session_dict = session_message.to_dict()
        message = AgentCoreMemoryConverter._filter_empty_text(redact_for_storage(session_dict["message"]))
        if not message.get("content"):
            return []
        session_dict["message"] = message
        return [(json.dumps(session_dict), message["role"])]
