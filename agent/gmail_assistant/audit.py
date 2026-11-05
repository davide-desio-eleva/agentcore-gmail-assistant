"""An audit trail of what the guard decided, in a log group of its own.

The runtime can add lines to it and cannot change or delete them: its role has CreateLogStream and PutLogEvents on that
group and nothing else. Each line holds the event, the action, a count and the hash of the user's request, and never
the text of an email. Without AUDIT_LOG_GROUP, nothing is written. A failing log never blocks the guard.
"""

import json
import os
import time
from functools import cache

import boto3


@cache
def _client():
    return boto3.client("logs", region_name=os.environ.get("AWS_REGION"))


_streams: set[str] = set()


def write(event: str, action: str, count: int, request: str, reason: str = "") -> None:
    group = os.environ.get("AUDIT_LOG_GROUP")
    if not group:
        return
    now = time.time()
    stream = time.strftime("%Y-%m-%d", time.gmtime(now))
    line = {"event": event, "action": action, "count": count, "request": request}
    if reason:
        line["reason"] = reason
    try:
        if stream not in _streams:
            try:
                _client().create_log_stream(logGroupName=group, logStreamName=stream)
            except _client().exceptions.ResourceAlreadyExistsException:
                pass
            _streams.add(stream)
        _client().put_log_events(
            logGroupName=group,
            logStreamName=stream,
            logEvents=[{"timestamp": int(now * 1000), "message": json.dumps(line)}],
        )
    except Exception as error:
        print(f"audit write failed: {type(error).__name__}")
