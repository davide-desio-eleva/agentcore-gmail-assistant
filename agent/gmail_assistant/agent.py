import os

from strands import Agent
from strands.models import BedrockModel

from .history import ForgetStaleEmailData
from .memory import build_session_manager
from .prompts import READER_SYSTEM_PROMPT, SYSTEM_PROMPT
from .readonly_tools import list_emails_raw, read_email_raw
from .tools import (
    cancel_reminder,
    forget_memory,
    list_attachments,
    list_emails,
    list_folders,
    list_memories,
    list_reminder_proposals,
    list_reminders,
    mark_as_read,
    move_to_folder,
    read_email,
    run_digest,
    send_attachment,
    set_email_reminder,
    star_emails,
    trash_emails,
    undo_last_action,
    unsubscribe_info,
)

DEFAULT_MODEL_ID = "eu.amazon.nova-pro-v1:0"

# The agent of the chat can change the mailbox, so it never sees the raw text of an email: its list_emails and
# read_email go through the quarantined reader. The agents of the digest and of the reminders show the text
# of the emails, so they get no tool that changes anything.
CHAT_TOOLS = [
    list_emails,
    read_email,
    list_folders,
    mark_as_read,
    trash_emails,
    star_emails,
    move_to_folder,
    list_attachments,
    send_attachment,
    undo_last_action,
    list_memories,
    set_email_reminder,
    forget_memory,
    cancel_reminder,
    list_reminders,
    list_reminder_proposals,
    run_digest,
    unsubscribe_info,
]
DIGEST_TOOLS = [list_emails_raw, read_email_raw]
REMINDER_TOOLS = [read_email_raw]

_MODES = {
    "chat": (SYSTEM_PROMPT, CHAT_TOOLS),
    "digest": (READER_SYSTEM_PROMPT, DIGEST_TOOLS),
    "reminder": (READER_SYSTEM_PROMPT, REMINDER_TOOLS),
}


def build_agent(session_id: str, remember: bool = True, mode: str = "chat") -> Agent:
    """Build the agent for one of the modes above.

    Automatic runs (digest, reminders) pass remember=False: their prompts are instructions, and if they were
    stored as conversation, the memory would learn them as the user's preferences.
    """
    system_prompt, tools = _MODES[mode]
    model = BedrockModel(
        model_id=os.environ.get("MODEL_ID", DEFAULT_MODEL_ID),
        region_name=os.environ.get("AWS_REGION"),
    )
    return Agent(
        model=model,
        system_prompt=system_prompt,
        tools=tools,
        session_manager=build_session_manager(session_id) if remember else None,
        # The default handler prints the model's output to stdout, which AgentCore sends to CloudWatch Logs.
        callback_handler=None,
        # Only the chat has a history that comes back with the session.
        hooks=[ForgetStaleEmailData()] if mode == "chat" else [],
    )
