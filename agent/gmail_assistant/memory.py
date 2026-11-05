"""Long-term memory: remembers the user's preferences across sessions.

The namespace below must match the strategy namespace created in infra/lib/gmail-assistant-stack.ts.
There is no episodic strategy: its records were never read back, and they kept a trace of what was said.
"""

import json
import os

import boto3
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig, RetrievalConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager

from .storage import RedactingConverter

# One mailbox, one owner: the actor is fixed so every session shares the same memories.
ACTOR_ID = "owner"
PREFERENCES_NAMESPACE = f"/users/{ACTOR_ID}/preferences"


def build_session_manager(session_id: str) -> AgentCoreMemorySessionManager | None:
    memory_id = os.environ.get("MEMORY_ID")
    if not memory_id:
        return None
    config = AgentCoreMemoryConfig(
        memory_id=memory_id,
        session_id=session_id,
        actor_id=ACTOR_ID,
        retrieval_config={
            PREFERENCES_NAMESPACE: RetrievalConfig(top_k=10, relevance_score=0.3),
        },
    )
    # The converter keeps email data out of what is stored, and so out of what the strategies learn from.
    return AgentCoreMemorySessionManager(config, region_name=os.environ.get("AWS_REGION"), converter=RedactingConverter)


MEMORY_NAMESPACES = [PREFERENCES_NAMESPACE]


def describe(text: str) -> str:
    """A short plain line for a record, which the store keeps as JSON."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return text
    return (data.get("preference") or text) if isinstance(data, dict) else text


def normalize_namespace(namespace: str) -> str:
    """The service requires exactly one leading and one trailing slash."""
    return "/" + namespace.strip("/") + "/"


def list_memories() -> list[dict]:
    """Remembered preferences, with the ids needed to forget them."""
    client = boto3.client("bedrock-agentcore", region_name=os.environ.get("AWS_REGION"))
    memories = []
    for namespace in MEMORY_NAMESPACES:
        response = client.list_memory_records(
            memoryId=os.environ["MEMORY_ID"],
            namespace=normalize_namespace(namespace),
            maxResults=50,
        )
        for record in response.get("memoryRecordSummaries", []):
            memories.append(
                {
                    "record_id": record["memoryRecordId"],
                    "namespace": normalize_namespace(namespace),
                    "text": record.get("content", {}).get("text", ""),
                }
            )
    return memories


def forget_memory(record_id: str, namespace: str) -> None:
    if namespace.strip("/") not in [ns.strip("/") for ns in MEMORY_NAMESPACES]:
        raise ValueError("Only preferences can be forgotten.")
    boto3.client("bedrock-agentcore", region_name=os.environ.get("AWS_REGION")).delete_memory_record(
        memoryId=os.environ["MEMORY_ID"],
        memoryRecordId=record_id,
        namespace=normalize_namespace(namespace),
    )
