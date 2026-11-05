import os
from functools import cache

from .gmail import GmailClient


@cache
def gmail() -> GmailClient:
    return GmailClient.from_parameter(os.environ["GMAIL_PARAM_NAME"])
