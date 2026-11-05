"""Deletes everything the assistant created in AWS and checks that nothing is left.

Steps:
1. cdk destroy GmailAssistantStack (Lambdas, runtime, scheduler, IAM, webhook URL, own log groups).
2. Delete the SSM parameters under /gmail-assistant (SSM deletes them immediately).
3. Delete the Secrets Manager secrets from the earlier version, if they still exist.
4. Verify that no parameter or secret with the prefix remains.

Usage:
    python scripts/teardown.py
"""

import argparse
import subprocess
from pathlib import Path

import boto3

PREFIX = "/gmail-assistant"
LEGACY_SECRET_PREFIX = "gmail-assistant/"
INFRA_DIR = Path(__file__).resolve().parent.parent / "infra"


def list_parameters(ssm) -> list[str]:
    names = []
    paginator = ssm.get_paginator("describe_parameters")
    for page in paginator.paginate(
        ParameterFilters=[{"Key": "Name", "Option": "BeginsWith", "Values": [PREFIX]}]
    ):
        names += [p["Name"] for p in page["Parameters"]]
    return names


def list_legacy_secrets(sm) -> list[str]:
    names = []
    paginator = sm.get_paginator("list_secrets")
    for page in paginator.paginate():
        names += [s["Name"] for s in page["SecretList"] if s["Name"].startswith(LEGACY_SECRET_PREFIX)]
    return names


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--region", default="eu-west-1")
    parser.add_argument("--yes", action="store_true", help="Do not ask for confirmation")
    args = parser.parse_args()

    if not args.yes:
        answer = input("This deletes the stack and all Gmail assistant parameters. Type DELETE to continue: ")
        if answer.strip() != "DELETE":
            raise SystemExit("Aborted.")

    ssm = boto3.client("ssm", region_name=args.region)
    sm = boto3.client("secretsmanager", region_name=args.region)

    # cdk destroy synthesizes the app, which requires the context value. It is not used for deletion.
    subprocess.run(
        ["npx", "cdk", "destroy", "--force", "GmailAssistantStack", "-c", "allowedChatId=0"],
        cwd=INFRA_DIR,
        check=True,
    )

    names = list_parameters(ssm)
    for i in range(0, len(names), 10):
        ssm.delete_parameters(Names=names[i : i + 10])
    print(f"Deleted {len(names)} SSM parameter(s): {names}")

    legacy = list_legacy_secrets(sm)
    for name in legacy:
        sm.delete_secret(SecretId=name, ForceDeleteWithoutRecovery=True)
    print(f"Deleted {len(legacy)} legacy Secrets Manager secret(s): {legacy}")

    remaining_params = list_parameters(ssm)
    remaining_secrets = list_legacy_secrets(sm)
    if remaining_params or remaining_secrets:
        raise SystemExit(f"Still present: {remaining_params + remaining_secrets}")
    print("Verified: no Gmail assistant parameters or secrets remain.")


if __name__ == "__main__":
    main()
