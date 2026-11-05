"""Stops the assistant in seconds, and starts it again. Nothing is deleted.

pause:  sets the reserved concurrency of the webhook and the worker to 0, so no message reaches the agent and no
        agent turn starts, and disables the daily digest. Reminders that fire during the pause are dropped.
resume: puts the concurrency back to what the stack sets, and enables the digest.
status: shows the current state and changes nothing.

Usage:
    python scripts/pause.py pause
    python scripts/pause.py resume
    python scripts/pause.py status
"""

import argparse

import boto3

STACK_NAME = "GmailAssistantStack"
WORKER_NAME = "gmail-assistant-worker"
DIGEST_SCHEDULE = "gmail-assistant-daily-digest"
# The values in infra/lib/gmail-assistant-stack.ts. A cdk deploy sets them again.
WORKER_CONCURRENCY = 1
WEBHOOK_CONCURRENCY = 5


def webhook_name(region: str) -> str:
    resources = boto3.client("cloudformation", region_name=region).describe_stack_resources(StackName=STACK_NAME)
    for resource in resources["StackResources"]:
        if resource["ResourceType"] == "AWS::Lambda::Function" and resource["LogicalResourceId"].startswith("WebhookFunction"):
            return resource["PhysicalResourceId"]
    raise SystemExit("The webhook function was not found in the stack.")


def set_digest(region: str, enabled: bool) -> None:
    client = boto3.client("scheduler", region_name=region)
    current = client.get_schedule(Name=DIGEST_SCHEDULE)
    client.update_schedule(
        Name=DIGEST_SCHEDULE,
        ScheduleExpression=current["ScheduleExpression"],
        ScheduleExpressionTimezone=current["ScheduleExpressionTimezone"],
        FlexibleTimeWindow=current["FlexibleTimeWindow"],
        Target=current["Target"],
        State="ENABLED" if enabled else "DISABLED",
    )


def show(region: str, webhook: str) -> None:
    client = boto3.client("lambda", region_name=region)
    for name in (webhook, WORKER_NAME):
        limit = client.get_function_concurrency(FunctionName=name).get("ReservedConcurrentExecutions")
        print(f"{name}: reserved concurrency {limit}")
    print(f"{DIGEST_SCHEDULE}: {boto3.client('scheduler', region_name=region).get_schedule(Name=DIGEST_SCHEDULE)['State']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["pause", "resume", "status"])
    parser.add_argument("--region", default="eu-west-1")
    args = parser.parse_args()

    webhook = webhook_name(args.region)
    client = boto3.client("lambda", region_name=args.region)
    if args.action != "status":
        paused = args.action == "pause"
        client.put_function_concurrency(FunctionName=webhook, ReservedConcurrentExecutions=0 if paused else WEBHOOK_CONCURRENCY)
        client.put_function_concurrency(FunctionName=WORKER_NAME, ReservedConcurrentExecutions=0 if paused else WORKER_CONCURRENCY)
        set_digest(args.region, enabled=not paused)
        print("Paused." if paused else "Resumed.")
    show(args.region, webhook)


if __name__ == "__main__":
    main()
