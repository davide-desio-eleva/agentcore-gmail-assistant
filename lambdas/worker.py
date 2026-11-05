"""Runs one agent turn and sends the answer to Telegram.

Triggered asynchronously by the webhook (chat messages) or by EventBridge Scheduler (daily digest and reminders).
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from common import invoke_agent, send_message, telegram_call

ALLOWED_CHAT_ID = os.environ["ALLOWED_CHAT_ID"]
TIMEZONE = os.environ["TIMEZONE"]


def handler(event, context):
    kind = event["type"]
    if kind == "daily_digest":
        chat_id = ALLOWED_CHAT_ID
        today = datetime.now(ZoneInfo(TIMEZONE)).date().isoformat()
        reply = invoke_agent(f"digest:{today}", {"kind": "daily_digest"})
    elif kind == "email_reminder":
        # The agent reads the email and always sends the reminder, noting if it is already read.
        reply = invoke_agent(f"reminder:{event['message_id']}", {"kind": "reminder", "message_id": event["message_id"]})
        send_message(ALLOWED_CHAT_ID, reply)
        return {"ok": True}
    elif kind == "chat":
        # The answer goes only to the owner's chat, whatever the event says.
        chat_id = ALLOWED_CHAT_ID
        telegram_call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
        reply = invoke_agent(f"chat:{chat_id}", {"kind": "chat", "prompt": event["text"]})
    else:
        raise ValueError(f"Unknown event type: {kind}")

    send_message(chat_id, reply)
    return {"ok": True}
