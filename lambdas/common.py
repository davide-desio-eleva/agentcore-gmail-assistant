"""Helpers shared by the webhook and worker Lambda functions."""

import hashlib
import html
import json
import os
import re
import time
import urllib.error
import urllib.request
from functools import cache

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

# Telegram rejects messages longer than 4096 characters.
TELEGRAM_MAX_CHARS = 4000
# Raw text per message, so the HTML tags added by to_telegram_html still fit under the limit.
TELEGRAM_CHUNK_CHARS = 3000


@cache
def get_parameter(name: str) -> str:
    return boto3.client("ssm").get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def telegram_call(method: str, payload: dict) -> dict:
    token = get_parameter(os.environ["TELEGRAM_BOT_TOKEN_PARAM"])
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def send_message(chat_id: str, text: str) -> None:
    for chunk in split_message(text, limit=TELEGRAM_CHUNK_CHARS):
        try:
            telegram_call("sendMessage", {"chat_id": chat_id, "text": to_telegram_html(chunk), "parse_mode": "HTML"})
        except urllib.error.HTTPError:
            # Telegram rejects HTML it cannot parse. Fall back to plain text rather than losing the reply.
            telegram_call("sendMessage", {"chat_id": chat_id, "text": chunk})


def to_telegram_html(markdown: str) -> str:
    """Convert the Markdown the model writes into the HTML subset Telegram supports."""
    text = html.escape(markdown, quote=False)
    blocks: list[str] = []

    def keep(match: re.Match, tag: str) -> str:
        blocks.append(f"<{tag}>{match.group(1)}</{tag}>")
        return f"\0{len(blocks) - 1}\0"

    # Code is protected first so that its contents are not reformatted.
    text = re.sub(r"```[a-zA-Z]*\n?(.*?)```", lambda m: keep(m, "pre"), text, flags=re.DOTALL)
    text = re.sub(r"`([^`\n]+)`", lambda m: keep(m, "code"), text)

    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
    text = re.sub(r"(?<![\w*])\*(?![\s*])([^*\n]+?)\*(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"\[([^\]\n]+)\]\((https?://[^\s)\"<>]+)\)", r'<a href="\2">\1</a>', text)

    return re.sub(r"\0(\d+)\0", lambda m: blocks[int(m.group(1))], text)


def split_message(text: str, limit: int = TELEGRAM_MAX_CHARS) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        # Slice lines that are longer than the limit on their own.
        for piece in (line[i : i + limit] for i in range(0, len(line), limit)):
            if len(current) + len(piece) > limit:
                chunks.append(current)
                current = ""
            current += piece
    if current.strip():
        chunks.append(current)
    return [chunk for chunk in chunks if chunk.strip()] or ["(empty reply)"]


def session_id(key: str) -> str:
    # AgentCore session ids must be at least 33 characters, so a hash of the key is used.
    return hashlib.sha256(key.encode()).hexdigest()


def invoke_agent(session_key: str, payload: dict) -> str:
    client = boto3.client(
        "bedrock-agentcore",
        config=Config(read_timeout=900, retries={"max_attempts": 1}),
    )
    response = client.invoke_agent_runtime(
        agentRuntimeArn=os.environ["AGENT_RUNTIME_ARN"],
        runtimeSessionId=session_id(session_key),
        payload=json.dumps(payload).encode(),
    )
    return json.loads(response["response"].read())["reply"]


def is_owner_message(message: dict, owner_id: str) -> bool:
    """True only for a private chat from the owner's own account, not from a bot."""
    sender = message.get("from") or {}
    chat = message.get("chat") or {}
    return (
        chat.get("type") == "private"
        and str(chat.get("id")) == owner_id
        and str(sender.get("id")) == owner_id
        and not sender.get("is_bot", False)
    )


def rejection_log(message: dict) -> str:
    """What to log about a rejected message. The text is left out on purpose."""
    sender = message.get("from") or {}
    chat = message.get("chat") or {}
    return json.dumps(
        {
            "event": "rejected_message",
            "from_id": sender.get("id"),
            "is_bot": sender.get("is_bot", False),
            "chat_id": chat.get("id"),
            "chat_type": chat.get("type"),
        }
    )


DEDUPE_TTL_SECONDS = 24 * 60 * 60


@cache
def _table():
    return boto3.resource("dynamodb").Table(os.environ["ACTION_LOG_TABLE"])


def claim_update(update_id: int, table=None) -> bool:
    """True the first time an update id is seen, False for a duplicate.

    Telegram sends an update again when the webhook is slow or fails, and a second run of the same message could
    change the mailbox twice. The write is conditional, so two requests that arrive together cannot both claim it.
    The item expires by itself after a day (the table has a TTL on `expires`).
    """
    try:
        (table or _table()).put_item(
            Item={"owner": "update", "ts": str(update_id), "expires": int(time.time()) + DEDUPE_TTL_SECONDS},
            ConditionExpression="attribute_not_exists(ts)",
        )
        return True
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise
