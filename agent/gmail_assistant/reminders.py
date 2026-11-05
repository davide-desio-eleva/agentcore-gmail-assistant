"""One-off reminders, scheduled with EventBridge Scheduler and delivered to Telegram by the worker."""

import json
import os
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import boto3


def schedule_expression(when_local: str, timezone: str, now: datetime) -> str:
    """Turn 'YYYY-MM-DDTHH:MM' in the given timezone into an EventBridge one-off expression.

    Raises ValueError when the time is not in the future, so a reminder is never silently dropped.
    """
    tz = ZoneInfo(timezone)
    when = datetime.fromisoformat(when_local).replace(tzinfo=tz)
    if when <= now.astimezone(tz):
        raise ValueError(f"{when_local} is in the past. Ask for a future time.")
    return f"at({when.strftime('%Y-%m-%dT%H:%M:%S')})"


def create_email_reminder(message_id: str, subject: str, when_local: str) -> str:
    """Schedule a reminder about one email. The worker checks the email when it fires."""
    timezone = os.environ["TIMEZONE"]
    expression = schedule_expression(when_local, timezone, datetime.now(ZoneInfo(timezone)))
    name = f"gmail-assistant-reminder-{uuid.uuid4().hex}"
    boto3.client("scheduler").create_schedule(
        Name=name,
        ScheduleExpression=expression,
        ScheduleExpressionTimezone=timezone,
        FlexibleTimeWindow={"Mode": "OFF"},
        ActionAfterCompletion="DELETE",
        Target={
            "Arn": os.environ["WORKER_FUNCTION_ARN"],
            "RoleArn": os.environ["SCHEDULER_ROLE_ARN"],
            "Input": json.dumps({"type": "email_reminder", "message_id": message_id, "text": subject}),
            "RetryPolicy": {"MaximumRetryAttempts": 2, "MaximumEventAgeInSeconds": 3600},
        },
    )
    return name


REMINDER_PREFIX = "gmail-assistant-reminder-"


def list_reminders() -> list[dict]:
    """Active reminders created by the assistant, with the text and the time they will fire."""
    client = boto3.client("scheduler")
    reminders = []
    paginator = client.get_paginator("list_schedules")
    for page in paginator.paginate(NamePrefix=REMINDER_PREFIX):
        for summary in page["Schedules"]:
            detail = client.get_schedule(Name=summary["Name"])
            text = json.loads(detail["Target"]["Input"]).get("text", "")
            reminders.append(
                {
                    "name": summary["Name"],
                    "when": detail["ScheduleExpression"],
                    "text": text,
                }
            )
    return reminders


def cancel_reminder(name: str) -> None:
    if not name.startswith(REMINDER_PREFIX):
        raise ValueError("Only reminders created by the assistant can be cancelled.")
    boto3.client("scheduler").delete_schedule(Name=name)
