"""Creates or updates the Telegram SecureString parameters in SSM Parameter Store.

- Telegram bot token: asked on the terminal, so it never ends up in shell history.
- Telegram webhook secret: generated the first time, then kept as is.

Gmail credentials are stored by gmail_oauth_setup.py.

Usage:
    python scripts/put_secrets.py
"""

import argparse
import getpass
import secrets

import boto3

PREFIX = "/gmail-assistant"
BOT_TOKEN_PARAM = f"{PREFIX}/telegram-bot-token"
WEBHOOK_SECRET_PARAM = f"{PREFIX}/telegram-webhook-secret"


def parameter_exists(ssm, name: str) -> bool:
    try:
        ssm.get_parameter(Name=name)
        return True
    except ssm.exceptions.ParameterNotFound:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--region", default="eu-west-1")
    args = parser.parse_args()
    ssm = boto3.client("ssm", region_name=args.region)

    token = getpass.getpass("Telegram bot token (input hidden): ").strip()
    if not token:
        raise SystemExit("Empty token, nothing stored.")
    ssm.put_parameter(Name=BOT_TOKEN_PARAM, Value=token, Type="SecureString", Overwrite=True)
    print(f"Stored {BOT_TOKEN_PARAM}")

    if parameter_exists(ssm, WEBHOOK_SECRET_PARAM):
        print(f"Kept existing {WEBHOOK_SECRET_PARAM}")
    else:
        # Telegram accepts A-Z, a-z, 0-9, _ and - in the secret token, which token_urlsafe produces.
        ssm.put_parameter(
            Name=WEBHOOK_SECRET_PARAM,
            Value=secrets.token_urlsafe(48),
            Type="SecureString",
        )
        print(f"Generated {WEBHOOK_SECRET_PARAM}")


if __name__ == "__main__":
    main()
