"""Registers the Telegram webhook. Run it after `cdk deploy`, with the WebhookUrl stack output.

Usage:
    python scripts/set_telegram_webhook.py --url https://xxxx.lambda-url.eu-west-1.on.aws/
"""

import argparse
import json
import urllib.request

import boto3


def call(token: str, method: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", required=True)
    parser.add_argument("--region", default="eu-west-1")
    parser.add_argument("--bot-token-param", default="/gmail-assistant/telegram-bot-token")
    parser.add_argument("--webhook-secret-param", default="/gmail-assistant/telegram-webhook-secret")
    args = parser.parse_args()

    ssm = boto3.client("ssm", region_name=args.region)
    token = ssm.get_parameter(Name=args.bot_token_param, WithDecryption=True)["Parameter"]["Value"]
    webhook_secret = ssm.get_parameter(Name=args.webhook_secret_param, WithDecryption=True)["Parameter"]["Value"]

    print("Bot:", call(token, "getMe", {})["result"]["username"])
    result = call(
        token,
        "setWebhook",
        {
            "url": args.url,
            "secret_token": webhook_secret,
            "allowed_updates": ["message"],
            "drop_pending_updates": True,
        },
    )
    print("setWebhook:", result)


if __name__ == "__main__":
    main()
