"""Screening of untrusted text with Amazon Bedrock Guardrails.

The guardrail is not attached to the model. It is applied to the text of an email with the ApplyGuardrail API,
before that text goes anywhere else. A hit is a signal, not a verdict: the text is withheld and the email is
marked suspicious. The guardrail is probabilistic, so it is one layer among the others.
"""

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from functools import cache

import boto3

MAX_CHARS = 6000
WORKERS = 8

_cache: dict[str, bool] = {}


@cache
def _client():
    return boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION"))


def enabled() -> bool:
    return bool(os.environ.get("GUARDRAIL_ID"))


def is_flagged(text: str) -> bool:
    """True when the guardrail intervenes on the text. If it cannot be called, the text is not flagged."""
    if not enabled() or not text.strip():
        return False
    text = text[:MAX_CHARS]
    key = hashlib.sha256(text.encode()).hexdigest()
    if key in _cache:
        return _cache[key]
    try:
        response = _client().apply_guardrail(
            guardrailIdentifier=os.environ["GUARDRAIL_ID"],
            guardrailVersion=os.environ["GUARDRAIL_VERSION"],
            source="INPUT",
            content=[{"text": {"text": text}}],
        )
        flagged = response.get("action") == "GUARDRAIL_INTERVENED"
    except Exception as error:
        print(f"guardrail screen failed: {type(error).__name__}")
        return False
    _cache[key] = flagged
    return flagged


def flagged_ids(texts: dict[str, str]) -> set[str]:
    """The ids whose text is flagged. The texts are screened in parallel."""
    if not enabled() or not texts:
        return set()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = dict(zip(texts, pool.map(is_flagged, texts.values())))
    return {message_id for message_id, flagged in results.items() if flagged}
