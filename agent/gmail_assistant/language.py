"""The language the user writes in, so the scheduled digest can use the same one."""

import os
import re
from functools import cache

import boto3

DEFAULT_LANGUAGE = "Italian"
_SETTINGS_KEY = {"owner": "settings", "ts": "language"}

# Small word lists are enough to tell apart short chat messages in the two languages.
_WORDS = {
    "Italian": {"il", "la", "che", "di", "non", "per", "sono", "mi", "ti", "una", "ciao", "quali", "come",
                "cosa", "ricordami", "dimmi", "leggi", "mostra", "questo", "questa", "ho", "hai", "della", "sul"},
    "English": {"the", "and", "to", "is", "you", "what", "for", "of", "how", "me", "my", "show", "read",
                "remind", "do", "are", "this", "that", "with", "please", "can", "tell", "list"},
}


@cache
def _table():
    return boto3.resource("dynamodb").Table(os.environ["ACTION_LOG_TABLE"])


def detect_language(text: str) -> str | None:
    """Return the language of a short message, or None when it is unclear."""
    words = re.findall(r"[a-zàèéìòù']+", text.lower())
    scores = {language: sum(word in vocabulary for word in words) for language, vocabulary in _WORDS.items()}
    best = max(scores, key=scores.get)
    if scores[best] == 0 or list(scores.values()).count(scores[best]) > 1:
        return None
    return best


def get_language() -> str:
    item = _table().get_item(Key=_SETTINGS_KEY).get("Item")
    return item["value"] if item else DEFAULT_LANGUAGE


def remember_language(language: str) -> None:
    _table().put_item(Item={**_SETTINGS_KEY, "value": language})
