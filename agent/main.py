"""AgentCore Runtime entrypoint for the Gmail assistant.

Each AgentCore session runs in its own microVM. The Agent is built once per session and keeps its
short-term conversation; long-term memory (preferences, past decisions) is shared across sessions.
"""

import os
import re
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from gmail_assistant import grounding, guard, proposals, screen
from gmail_assistant.agent import build_agent
from gmail_assistant.links import strip_links
from gmail_assistant.language import detect_language, get_language, remember_language
from gmail_assistant.prompts import PROMPT_REFUSAL, copies_prompt, digest_prompt, reminder_prompt

THINKING_CLOSED = re.compile(r"<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE)
# The model sometimes forgets the opening tag, or the closing one. Then the reasoning ends with its paragraph
# or with the closing tag.
THINKING_OPEN = re.compile(r"<thinking>.*?(?=\n\s*\n|\Z)", re.DOTALL | re.IGNORECASE)
THINKING_ORPHAN_CLOSE = re.compile(r"^.*?</thinking>", re.DOTALL | re.IGNORECASE)


def visible_text(text: str) -> str:
    """Remove the model's reasoning, which is not meant for the user, whatever form the tags take."""
    text = THINKING_CLOSED.sub("", text)
    text = THINKING_OPEN.sub("", text)
    text = THINKING_ORPHAN_CLOSE.sub("", text)
    return text.strip()

_FLAGGED_NOTICE = {
    "Italian": "⚠️ Il controllo ha segnalato questo testo: potrebbe contenere istruzioni nascoste in una email. Leggilo con attenzione.\n\n",
    "English": "⚠️ The check flagged this text: it may carry instructions hidden in an email. Read it with care.\n\n",
}


def with_notice(text: str, language: str) -> str:
    """The text of a digest or a reminder is screened once it is written. A flag adds a warning and never removes the
    text, so nobody can make me lose a digest by sending an email that looks like an attack."""
    if screen.is_flagged(text):
        return _FLAGGED_NOTICE.get(language, _FLAGGED_NOTICE["English"]) + text
    return text


app = BedrockAgentCoreApp()
_agents: dict = {}


def _automatic_agent(mode: str):
    """A fresh agent for digest and reminder runs. Their prompts are not stored as conversation or memory,
    and the agent only has tools that read."""
    return build_agent(uuid.uuid4().hex, remember=False, mode=mode)


def _agent_for(session_id: str):
    if session_id not in _agents:
        _agents[session_id] = build_agent(session_id)
    return _agents[session_id]


@app.entrypoint
def invoke(payload: dict, context) -> dict:
    kind = payload.get("kind", "chat")
    if kind == "reminder":
        agent = _automatic_agent("reminder")
        language = get_language()
        reply = strip_links(visible_text(str(agent(reminder_prompt(payload["message_id"], language)))))
        return {"reply": with_notice(reply, language)}
    if kind == "daily_digest":
        agent = _automatic_agent("digest")
        language = get_language()
        digest_text, found = proposals.split_proposals(visible_text(str(agent(digest_prompt(language)))))
        digest_text, found = with_notice(strip_links(digest_text), language), proposals.screened(proposals.vetted(found))
        try:
            proposals.save(found)
        except Exception:
            found = []  # the digest still goes out; only the proposals are lost
        return {"reply": digest_text + proposals.render(found)}
    language = detect_language(payload["prompt"])
    if language:
        remember_language(language)
    now = datetime.now(ZoneInfo(os.environ.get("TIMEZONE", "Europe/Rome")))
    prompt = f"[Current local time: {now:%A %Y-%m-%d %H:%M}]\n{payload['prompt']}"
    # The guard compares every change with the message the user wrote, so it needs the plain message.
    guard.begin_turn(payload["prompt"])
    agent = _agent_for(context.session_id)
    result = agent(prompt)
    if grounding.needs_reading(payload["prompt"], result.metrics.tool_metrics):
        # A question about the mail that was answered without reading it: ask once more, and distrust it if it persists.
        result = agent(grounding.RETRY_NOTE.strip())
        if grounding.needs_reading(payload["prompt"], result.metrics.tool_metrics):
            return {"reply": grounding.NOT_READ.get(get_language(), grounding.NOT_READ["English"])}
    # The model sometimes copies the internal PROPOSALS line into its answer. Users never see it.
    reply = "\n".join(line for line in visible_text(str(result)).split("\n") if not line.strip().startswith(proposals.PROPOSALS_LINE))
    if copies_prompt(reply):
        reply = PROMPT_REFUSAL.get(get_language(), PROMPT_REFUSAL["English"])
    if "run_digest" in result.metrics.tool_metrics:
        # The model may rewrite the digest and drop the proposals, so they are always added from the store.
        reply += proposals.render(proposals.load())
    # A fixed line, written by code, about what was changed in this turn.
    executed, pending, refused = guard.describe_executed(get_language()), guard.describe_pending(get_language()), guard.describe_refused(get_language())
    if refused and not executed and not pending:
        # The guard stopped the change and nothing else happened: what the model says about it is not needed, and it
        # sometimes gives a wrong reason. The line written by code is the answer.
        return {"reply": refused.strip()}
    return {"reply": reply + executed + pending + refused}


if __name__ == "__main__":
    app.run()
