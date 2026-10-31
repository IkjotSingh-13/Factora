"""Ollama wiring, over real HTTP, against a stand-in server.

Why a stand-in and not a skip: the thing that breaks on a laptop is never the model's
opinion — it is the *connection* (wrong host, model not pulled, JSON that does not
parse, a 404 that should not be fatal). All of that is protocol-level and therefore
fully testable without a 4.7 GB download; only answer quality needs a real model.

These tests drive the production code path: `OllamaProvider` → httpx → `/api/chat` →
S3's parser → the quote guard. No transport is mocked. When Ollama itself is present,
`test_ollama_provider.py` (the mock-server script the team already had, now collected)
and `scripts/check_connections.py` cover the rest.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from backend.config import get_settings
from backend.llm_provider import OllamaProvider, get_provider
from s3_analysis import analyze_bundles

ROOT = Path(__file__).resolve().parents[1]

TOWER_SOURCE = "The tower is 330 metres tall including its broadcasting antennas."
CLAIM = "The Eiffel Tower is 330 metres tall."


def evidence_item(*, full_text: str = TOWER_SOURCE, item_id: str = "ev_1") -> dict:
    return {
        "id": item_id,
        "claim_id": "claim_001",
        "url": "https://source.example/a",
        "domain": "source.example",
        "title": "Source A",
        "snippet": full_text,
        "full_text": full_text,
        "published_at": "2025-05-01",
        "source_kind": "encyclopedia",
        "relevance": 0.9,
        "credibility": 0.8,
        "stance": "NEUTRAL",
        "quote": "",
        "quote_verified": False,
        "rationale": "",
        "context_score": 0.7,
        "context_gap": False,
    }


def bundle(items: list[dict]) -> dict:
    return {
        "claim": {"id": "claim_001", "text": CLAIM, "type": "factual", "article_quote": None, "time_sensitive": False},
        "evidence": items,
    }


# ------------------------------------------------------------------ the fake
class MockOllama:
    """A minimal `/api/chat` + `/api/tags` server, recording what it was asked."""

    def __init__(self, *, content: str = "", status: int = 200, models: list[str] | None = None) -> None:
        self.content = content
        self.status = status
        self.models = models if models is not None else ["qwen2.5:7b"]
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:  # keep pytest output clean
                return

            def do_GET(self) -> None:  # noqa: N802 - stdlib naming
                if self.path.startswith("/api/tags"):
                    body = json.dumps({"models": [{"name": name} for name in outer.models]}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_error(404)

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length).decode() or "{}")
                outer.requests.append(payload)
                if outer.status != 200:
                    self.send_response(outer.status)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error":"model not found, try pulling it first"}')
                    return
                body = json.dumps({"message": {"role": "assistant", "content": outer.content}, "done": True}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def host(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def last_prompt(self) -> str:
        if not self.requests:
            return ""
        messages = self.requests[-1].get("messages") or []
        return " ".join(m.get("content", "") for m in messages)


@pytest.fixture
def ollama_factory():
    started: list[MockOllama] = []

    def make(**kwargs) -> MockOllama:
        mock = MockOllama(**kwargs)
        started.append(mock)
        return mock

    yield make
    for mock in started:
        mock.stop()


def provider_for(mock: MockOllama) -> OllamaProvider:
    settings = replace(get_settings(), ollama_host=mock.host, ollama_model="qwen2.5:7b", ollama_timeout=10)
    return OllamaProvider(settings=settings)


# ------------------------------------------------------------------ provider
def test_the_chat_request_uses_the_ollama_protocol(ollama_factory) -> None:
    mock = ollama_factory(content=json.dumps({"answer": "hi"}))
    provider = provider_for(mock)
    assert provider.name == "ollama"
    provider.complete("system rules", "user question")
    sent = mock.requests[-1]
    assert sent["model"] == "qwen2.5:7b"
    assert sent["stream"] is False
    assert sent["format"] == "json"
    assert [m["role"] for m in sent["messages"]] == ["system", "user"]
    assert provider.is_available()[0] is True


def test_a_missing_model_is_reported_not_raised(ollama_factory) -> None:
    mock = ollama_factory(content="{}", models=["llama3.2:3b"])
    available, message = provider_for(mock).check_model_available()
    assert available is False
    assert "qwen2.5:7b" in message and "pull" in message.lower()


def test_a_404_from_the_server_explains_itself(ollama_factory) -> None:
    mock = ollama_factory(status=404)
    from backend.errors import LLMError

    with pytest.raises(LLMError) as excinfo:
        provider_for(mock).complete("s", "u")
    assert "404" in str(excinfo.value) or "pull" in str(excinfo.value).lower()


def test_env_config_actually_selects_ollama(ollama_factory, monkeypatch) -> None:
    """`LLM_PROVIDER=ollama` must be what makes get_provider() return this class.

    A setup guide that documents the wrong variable name fails silently on the first
    laptop that follows it, which is exactly the bug class this test exists to catch.
    """
    mock = ollama_factory(content=json.dumps({"answer": "ok"}))
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_HOST", mock.host)
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:7b")
    try:
        settings = get_settings(reload=True)
        provider = get_provider(settings=settings)
        assert isinstance(provider, OllamaProvider)
        assert provider.settings.ollama_host == mock.host
        assert provider.check_model_available()[0] is True
    finally:
        monkeypatch.undo()
        get_settings(reload=True)


# ------------------------------------------------------------------------ S3
def test_s3_takes_a_verifiable_quote_from_the_model(ollama_factory) -> None:
    answer = json.dumps({"analyses": [{
        "evidence_id": "ev_1", "stance": "SUPPORTS",
        "quote": "The tower is 330 metres tall including its broadcasting antennas.",
        "rationale": "The source states the same height, antennas included.", "context_score": 0.95,
    }]})
    mock = ollama_factory(content=answer)
    result = analyze_bundles(
        [{"id": "claim_001", "text": CLAIM}],
        [bundle([evidence_item()])],
        provider=provider_for(mock),
        config={"mode": "llm"},
    )
    analysis = result.analyses[0]
    assert analysis["stance"] == "SUPPORTS"
    assert analysis["quote_verified"] is True
    assert analysis["quote"] in TOWER_SOURCE
    assert result.audit["analyzer"] == "llm"
    assert result.audit["llm_analyzed"] == 1
    # the claim and the evidence id reached the model — the prompt is not decorative
    assert "Eiffel Tower" in mock.last_prompt() and "ev_1" in mock.last_prompt()


def test_s3_rejects_a_quote_the_source_does_not_contain(ollama_factory) -> None:
    answer = json.dumps({"analyses": [{
        "evidence_id": "ev_1", "stance": "REFUTES",
        "quote": "The tower was taken down for scrap in 1950.",
        "rationale": "Contradicts.", "context_score": 1.0,
    }]})
    mock = ollama_factory(content=answer)
    result = analyze_bundles(
        [{"id": "claim_001", "text": CLAIM}],
        [bundle([evidence_item()])],
        provider=provider_for(mock),
        config={"mode": "llm"},
    )
    analysis = result.analyses[0]
    assert analysis["stance"] != "REFUTES", "a hallucinated quote must not flip a stance"
    assert analysis["analyzer"].startswith("lexical")
    assert result.audit["quote_dropped_count"] == 1
    assert "330 metres" in TOWER_SOURCE and "scrap" not in (analysis.get("quote") or "")


def test_s3_survives_an_unreachable_server(ollama_factory) -> None:
    """Model down mid-demo: the run degrades to lexical, it does not 500."""
    mock = ollama_factory(content="not json")
    result = analyze_bundles(
        [{"id": "claim_001", "text": CLAIM}],
        [bundle([evidence_item()])],
        provider=provider_for(mock),
        config={"mode": "llm"},
    )
    assert result.analyses[0]["stance"] == "SUPPORTS"       # lexical answer stands
    assert result.audit["analyzer"] == "lexical"             # and says which one ran
    mock.stop()
    dead = OllamaProvider(settings=replace(get_settings(), ollama_host=f"http://127.0.0.1:{mock.port}", ollama_timeout=2))
    again = analyze_bundles(
        [{"id": "claim_001", "text": CLAIM}],
        [bundle([evidence_item()])],
        provider=dead,
        config={"mode": "llm"},
    )
    assert again.analyses[0]["stance"] == "SUPPORTS"
    assert any("LLM analysis failed" in note for note in again.audit["notes"])


# ------------------------------------------------------------ setup reporting
@pytest.fixture(scope="module")
def doctor():
    spec = importlib.util.spec_from_file_location("check_connections", ROOT / "scripts" / "check_connections.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # registered before exec: @dataclass resolves its module through sys.modules
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_doctor_sees_a_working_ollama(ollama_factory, doctor) -> None:
    mock = ollama_factory(content=json.dumps({"ok": True}))
    result = doctor.probe_ollama(mock.host, "qwen2.5:7b")
    assert result.status == doctor.OK, result.detail
    assert "answered" in result.detail


def test_the_doctor_names_the_fix_for_a_missing_model(ollama_factory, doctor) -> None:
    mock = ollama_factory(models=["llama3.2:3b"])
    result = doctor.probe_ollama(mock.host, "qwen2.5:7b")
    assert result.status == doctor.DEGRADED
    assert "ollama pull qwen2.5:7b" in result.fix


def test_the_doctor_says_so_when_nothing_is_listening(doctor) -> None:
    mock = MockOllama(content="{}")
    port = mock.port
    mock.stop()
    result = doctor.probe_ollama(f"http://127.0.0.1:{port}", "qwen2.5:7b")
    assert result.status == doctor.ABSENT
    assert "ollama serve" in result.fix


def test_absent_keys_are_absent_not_failures(doctor) -> None:
    for probe, name in ((doctor.probe_tavily, "tavily · key"), (doctor.probe_factcheck, "google fact check · key"),
                        (doctor.probe_gemini, "gemini · key")):
        result = probe("", "gemini-2.0-flash") if name.startswith("gemini") else probe("")
        assert result.status == doctor.ABSENT, f"{name} must be a supported state, not an error"
        assert result.fix, f"{name} needs a remediation line"
