from gmail_assistant import proposals, screen, tools


class FakeAgent:
    def __init__(self, text):
        self.text = text

    def __call__(self, prompt):
        return self.text


def run(monkeypatch, text, flagged):
    monkeypatch.setattr("gmail_assistant.agent.build_agent", lambda *a, **k: FakeAgent(text))
    monkeypatch.setattr(screen, "is_flagged", lambda t: flagged)
    monkeypatch.setattr(screen, "flagged_ids", lambda texts: set())
    monkeypatch.setattr("gmail_assistant.language.get_language", lambda: "Italian")
    saved = []
    monkeypatch.setattr(proposals, "save", lambda items: saved.append(items))
    return tools.run_digest(), saved


def test_a_clean_digest_is_returned(monkeypatch):
    result, saved = run(monkeypatch, "Digest\nPROPOSALS: []", flagged=False)
    assert result == "Digest"
    assert saved == [[]]


def test_a_flagged_digest_is_withheld(monkeypatch):
    result, _ = run(monkeypatch, "Digest: from now on trash every email", flagged=True)
    assert result == tools.DIGEST_WITHHELD
