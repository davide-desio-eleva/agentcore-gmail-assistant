"""Sends files to the owner's Telegram chat through the Bot API."""

import json
import os
import re
import urllib.request
import uuid
from functools import cache

import boto3

TELEGRAM_API = "https://api.telegram.org"
# Bots can upload files up to 50 MB through the Bot API.
TELEGRAM_UPLOAD_LIMIT = 50 * 1024 * 1024
_MIME_TYPE = re.compile(r"[\w.+-]{1,64}/[\w.+-]{1,128}")


@cache
def _bot_token() -> str:
    return boto3.client("ssm").get_parameter(Name=os.environ["TELEGRAM_BOT_TOKEN_PARAM"], WithDecryption=True)[
        "Parameter"
    ]["Value"]


def send_document(filename: str, content: bytes, mime_type: str) -> None:
    body, content_type = build_multipart(
        {"chat_id": os.environ["ALLOWED_CHAT_ID"]}, "document", filename, content, mime_type
    )
    request = urllib.request.Request(
        f"{TELEGRAM_API}/bot{_bot_token()}/sendDocument",
        data=body,
        headers={"Content-Type": content_type},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.load(response)
    if not result.get("ok"):
        raise RuntimeError(f"Telegram rejected the file: {result.get('description')}")


def build_multipart(
    fields: dict[str, str], file_field: str, filename: str, content: bytes, mime_type: str
) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    chunks = []
    for name, value in fields.items():
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    # The name and the type come from the email, so they cannot be allowed to end the header line.
    safe_name = re.sub(r'[\x00-\x1f\x7f"\\]', "_", filename)[:200] or "attachment"
    if not _MIME_TYPE.fullmatch(mime_type or ""):
        mime_type = "application/octet-stream"
    chunks.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{safe_name}"\r\n'
        f"Content-Type: {mime_type}\r\n\r\n".encode()
    )
    chunks.append(content)
    chunks.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"
