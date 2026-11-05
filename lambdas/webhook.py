"""Telegram webhook. Checks the secret token and the chat, then hands the message to the worker.

It answers Telegram right away, because Telegram gives up on slow webhooks and then retries them.
"""

import base64
import hmac
import json
import os

import boto3
from common import claim_update, get_parameter, is_owner_message, rejection_log, send_message

ALLOWED_CHAT_ID = os.environ["ALLOWED_CHAT_ID"]
WORKER_FUNCTION_NAME = os.environ["WORKER_FUNCTION_NAME"]
WEBHOOK_SECRET_PARAM = os.environ["WEBHOOK_SECRET_PARAM"]

_lambda = boto3.client("lambda")


def handler(event, context):
    headers = {key.lower(): value for key, value in (event.get("headers") or {}).items()}
    provided = headers.get("x-telegram-bot-api-secret-token", "")
    # Compared as bytes: a header with characters outside ASCII is refused, not an error.
    if not hmac.compare_digest(provided.encode(), get_parameter(WEBHOOK_SECRET_PARAM).encode()):
        return {"statusCode": 401, "body": "unauthorized"}

    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode()

    update = json.loads(body)
    message = update.get("message")
    if not message:
        return _ok()

    if not is_owner_message(message, ALLOWED_CHAT_ID):
        print(rejection_log(message))
        return _ok()

    update_id = update.get("update_id")
    if update_id is not None:
        try:
            if not claim_update(update_id):
                print(json.dumps({"event": "duplicate_update", "update_id": update_id}))
                return _ok()
        except Exception as error:
            # A failing store must not make the bot deaf, so the message is processed anyway.
            print(f"update dedupe failed, processing anyway: {type(error).__name__}")

    chat_id = str(message["chat"]["id"])

    text = message.get("text")
    if not text:
        send_message(chat_id, "I can only read text messages for now.")
        return _ok()

    _lambda.invoke(
        FunctionName=WORKER_FUNCTION_NAME,
        InvocationType="Event",
        Payload=json.dumps({"type": "chat", "chat_id": chat_id, "text": text}).encode(),
    )
    return _ok()


def _ok():
    return {"statusCode": 200, "body": "ok"}
