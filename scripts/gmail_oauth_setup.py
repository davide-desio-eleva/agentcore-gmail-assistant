"""One-time Gmail authorization. Runs the consent flow in your browser and stores the credentials as a SecureString in SSM Parameter Store.

Usage:
    python scripts/gmail_oauth_setup.py --client-secrets ~/Downloads/client_secret.json
"""

import argparse
import json

import boto3
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--client-secrets", required=True, help="OAuth client JSON (Desktop app) from Google Cloud")
    parser.add_argument("--param-name", default="/gmail-assistant/gmail-oauth")
    parser.add_argument("--region", default="eu-west-1")
    args = parser.parse_args()

    flow = InstalledAppFlow.from_client_secrets_file(args.client_secrets, SCOPES)
    # prompt=consent makes Google return a refresh token even if the app was authorized before.
    credentials = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    if not credentials.refresh_token:
        raise SystemExit("Google did not return a refresh token. Revoke the app's access and try again.")

    with open(args.client_secrets) as file:
        installed = json.load(file)["installed"]
    secret = {
        "client_id": installed["client_id"],
        "client_secret": installed["client_secret"],
        "refresh_token": credentials.refresh_token,
    }
    boto3.client("ssm", region_name=args.region).put_parameter(
        Name=args.param_name,
        Value=json.dumps(secret),
        Type="SecureString",
        Overwrite=True,
    )
    print(f"Stored Gmail OAuth credentials in {args.param_name}")


if __name__ == "__main__":
    main()
