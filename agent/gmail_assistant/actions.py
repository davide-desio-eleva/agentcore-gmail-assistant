"""Log of the actions that change the mailbox, so the last one can be undone."""

import os
import time
import uuid
from functools import cache

import boto3
from boto3.dynamodb.conditions import Key

OWNER = "owner"


@cache
def _table():
    return boto3.resource("dynamodb").Table(os.environ["ACTION_LOG_TABLE"])


def record(action: str, message_ids: list[str], undo: dict, request: str = "") -> None:
    """Store how to reverse an action. `undo` lists the Gmail labels to add and to remove."""
    _table().put_item(
        Item={
            "owner": OWNER,
            # Zero-padded nanoseconds, so a descending query returns the newest action first.
            "ts": f"{time.time_ns():020d}",
            "id": uuid.uuid4().hex,
            "action": action,
            "message_ids": message_ids,
            "undo": undo,
            # Hash of the user message that asked for the change, so it can be traced to its request.
            "request": request,
        }
    )


def last() -> dict | None:
    response = _table().query(
        KeyConditionExpression=Key("owner").eq(OWNER),
        ScanIndexForward=False,
        Limit=1,
    )
    items = response["Items"]
    return items[0] if items else None


def forget(item: dict) -> None:
    _table().delete_item(Key={"owner": item["owner"], "ts": item["ts"]})
