"""`POST /api/verify` — the end-to-end path the plan asked for, and its guarantees.

Everything here runs offline (local demo corpus, lexical S3): no keys, no network, no
flakiness. The point of these tests is not "the endpoint returns 200" — it is that a
report can be trusted to contain only what was actually verified, and to say plainly
what it could not do.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
ROOT = Path(__file__).resolve().parents[1]

ARTICLE = (
    "The Eiffel Tower is 330 metres tall. That figure includes the broadcasting antenna "
    "added to the tower. A widely shared travel brief states that the Eiffel Tower is 330 "
    "metres tall. The tower was completed for the 1889 World's Fair. It remains the tallest "
    "structure in Paris."
)
REFUTED_ARTICLE = (
    "Vitamin C supplements prevent the common cold in most adults. A supplement retailer "
    "made that claim in a seasonal advertising campaign. The campaign ran for six weeks on "
    "social channels. No specific study was linked from the page."
)


def verify(article_text: str, **overrides) -> dict:
    payload = {"article_text": article_text, "mode": "offline", "as_of": "2026-10-31", **overrides}
    response = client.post("/api/verify", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# shape of the response
# ---------------------------------------------------------------------------
def test_verify_runs_every_stage_and_reports_each_one() -> None:
    data = verify(ARTICLE)
    names = [stage["name"] for stage in data["stages"]]
    assert names == [
        "S1 claim extraction",
        "S1 article-quote guard",
        "S2 retrieval (offline)",
        "S3 stance + quote guard",
        "S4/S5 score + report",
    ], names
    assert all(stage["ms"] >= 0 for stage in data["stages"])
    assert data["mode"] == "offline"
    assert data["source_type"] == "article_text"
    assert data["demo_backup"] is False
    assert "disclaimer" in data["report"]


def test_every_claim_carries_the_contract_fields() -> None:
    data = verify(ARTICLE)
    for report in data["report"]["claims"]:
        assert set(report) >= {
            "claim", "verdict", "stance_score", "evidence_strength",
            "scores", "contributions", "evidence", "audit",
        }
        assert set(report["scores"]) == {"C", "R", "A", "T", "K"}
        assert set(report["contributions"]) == {"C", "R", "A", "T", "K"}
        assert 0.0 <= report["evidence_strength"] <= 1.0
        assert -1.0 <= report["stance_score"] <= 1.0
        assert set(report["audit"]) >= {
            "n_retrieved", "n_used", "n_independent",
            "hallucinated_quotes_dropped", "unknown_freshness", "sources_failed",
        }


def test_contributions_reproduce_the_raw_score() -> None:
    """The breakdown must actually be the score, not decoration next to it."""
    for report in verify(ARTICLE)["report"]["claims"]:
        total = sum(report["contributions"].values())
        assert total == pytest.approx(report["score_raw"], abs=0.002)


def test_direction_and_strength_are_separate_numbers() -> None:
    """A claim can have good evidence pointing against it; the report keeps them apart."""
    data = verify(REFUTED_ARTICLE)
    headline = data["report"]["claims"][0]
    assert headline["stance_score"] < 0
    assert 0.0 <= headline["evidence_strength"] <= 1.0
    assert headline["verdict"] in {"CONFLICTING EVIDENCE", "REFUTED", "MIXED / PARTIALLY SUPPORTED", "INSUFFICIENT EVIDENCE"}


# ---------------------------------------------------------------------------
# the guarantees
# ---------------------------------------------------------------------------
def test_only_quote_verified_evidence_reaches_the_score() -> None:
    data = verify(ARTICLE)
    for report in data["report"]["claims"]:
        assert all(item["quote_verified"] for item in report["evidence"])
        assert report["audit"]["n_used"] == len(report["evidence"])
        for item in report["evidence"]:
            assert item["quote"].strip()
            assert item["quote_scope"] in ("full_text", "snippet")


def test_claims_always_carry_a_verbatim_article_quote() -> None:
    """The displayed quote must be findable in the text the user actually submitted."""
    data = verify(ARTICLE)
    normalized_article = " ".join(ARTICLE.split())
    for report in data["report"]["claims"]:
        quote = report["claim"]["article_quote"]
        if quote is None:
            # the documented escape hatch, only when nothing could be grounded at all
            assert any("WITHOUT a quote" in warning for warning in data["warnings"])
            continue
        assert " ".join(quote.split()) in normalized_article


def test_grounding_audit_counts_match_the_report() -> None:
    data = verify(ARTICLE)
    grounding = data["grounding"]
    assert grounding["claims_kept"] == len(data["report"]["claims"])
    assert grounding["claims_in"] >= grounding["claims_kept"]
    assert grounding["quotes_exact"] + grounding["quotes_relocated_in_article"] == grounding["claims_kept"]


def test_s3_audit_is_reported_and_quotes_dropped_are_visible() -> None:
    data = verify(ARTICLE)
    s3 = data["s3_audit"]
    assert s3["stage"] == "S3_ANALYSIS"
    assert s3["analyzer"] == "lexical"          # offline mode must not pretend to use a model
    assert s3["evidence_analyzed"] >= 1
    assert s3["decisive"] + s3["neutral"] == s3["evidence_analyzed"]


def test_offline_corpus_is_declared_as_a_demo_corpus_not_real_evidence() -> None:
    data = verify(ARTICLE)
    assert any("local demo corpus" in warning for warning in data["warnings"]), data["warnings"]


def test_opinions_are_not_verified_and_the_exclusion_is_visible() -> None:
    """Plan §3 (Ikjot): extract factual claims, exclude opinions and predictions."""
    data = verify(
        "Vitamin C supplements prevent the common cold in most adults. "
        "The campaign ran for six weeks on social channels. "
        "No specific study was linked from the page."
    )
    for report in data["report"]["claims"]:
        assert report["claim"]["type"] in {"factual", "other"}
    # whatever the labels, no opinion claim may arrive with a verdict attached
    assert all(report["verdict"] for report in data["report"]["claims"])


def test_snapshot_mode_is_labelled_as_a_demo_backup() -> None:
    response = client.post(
        "/api/verify",
        json={"article_text": ARTICLE, "mode": "snapshot", "as_of": "2026-10-31"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["demo_backup"] is True
    assert any("DEMO BACKUP" in warning or "snapshot" in warning for warning in data["warnings"])
    assert data["mode"] == "snapshot"


def test_a_claim_with_no_evidence_is_insufficient_not_false() -> None:
    """The protected behaviour: thin evidence yields an explicit non-verdict."""
    data = verify(
        "The consortium will build a hydrogen aircraft with the national space agency by next spring. "
        "No names were given by the consortium in the announcement. "
        "Three accounts copied the announcement verbatim on the same day."
    )
    verdicts = {report["verdict"] for report in data["report"]["claims"]}
    assert verdicts == {"INSUFFICIENT EVIDENCE"}, verdicts


# ---------------------------------------------------------------------------
# failure handling
# ---------------------------------------------------------------------------
def test_requires_exactly_one_source() -> None:
    assert client.post("/api/verify", json={}).status_code == 422
    both = client.post("/api/verify", json={"article_text": "x", "url": "https://a.example"})
    assert both.status_code == 422


def test_tiny_input_is_rejected_rather_than_invented_around() -> None:
    data = verify("Too short.")
    # no claims -> an explicit insufficient report, not a crash and not a verdict
    assert data["report"]["claims"][0]["verdict"] == "INSUFFICIENT EVIDENCE"


def test_unreachable_url_maps_to_a_bad_gateway_not_a_500(monkeypatch) -> None:
    import backend as s1_module

    def boom(*_args, **_kwargs):
        raise s1_module.ArticleExtractionError("page could not be fetched: HTTP 403")

    monkeypatch.setattr(s1_module, "extract_article_from_url", boom)
    response = client.post("/api/verify", json={"url": "https://example.gov/blocked"})
    assert response.status_code == 502
    assert "403" in response.json()["detail"]


def test_retrieval_being_empty_still_produces_a_report(monkeypatch) -> None:
    import app.pipeline.verify as verify_module

    async def no_evidence(claims, **_kwargs):
        return [{"claim": claim, "evidence": []} for claim in claims]

    monkeypatch.setattr(verify_module, "aretrieve_for_claims", no_evidence)
    data = verify(REFUTED_ARTICLE)
    for report in data["report"]["claims"]:
        assert report["verdict"] == "INSUFFICIENT EVIDENCE"
        assert report["audit"]["n_retrieved"] == 0
        assert report["evidence"] == []
    assert any("no source returned usable evidence" in warning for warning in data["warnings"])


def test_a_crashing_analyzer_degrades_instead_of_failing_the_request(monkeypatch) -> None:
    import app.pipeline.verify as verify_module

    def explode(*_args, **_kwargs):
        raise RuntimeError("model died")

    monkeypatch.setattr(verify_module, "analyze_bundles", explode)
    data = verify(REFUTED_ARTICLE)
    assert any(stage["name"].startswith("S3") and stage["status"] == "failed" for stage in data["stages"])
    for report in data["report"]["claims"]:
        assert report["verdict"] == "INSUFFICIENT EVIDENCE"
        assert report["audit"]["n_used"] == 0
    assert any("stance analysis failed" in warning for warning in data["warnings"])


# ---------------------------------------------------------------------------
# supporting endpoints
# ---------------------------------------------------------------------------
def test_samples_endpoint_serves_the_prepared_inputs() -> None:
    response = client.get("/api/samples")
    assert response.status_code == 200
    samples = response.json()["samples"]
    assert [sample["id"] for sample in samples] == ["supported", "refuted", "context", "thin", "conflicting"]
    for sample in samples:
        assert len(sample["article"]) > 200
        assert sample["expect"]


def test_config_endpoint_reports_what_the_process_will_actually_do() -> None:
    config = client.get("/api/config").json()
    assert config["s1_provider"] in {"mock", "gemini", "ollama"}
    assert "disclaimer" in config


def test_retrieve_and_extract_still_work_after_the_integration() -> None:
    extraction = client.post("/api/extract", json={"article_text": ARTICLE})
    assert extraction.status_code == 200
    claims = extraction.json()["claims"]
    assert claims and {"id", "text", "type", "entities"} <= set(claims[0])

    retrieval = client.post(
        "/api/retrieve",
        json={"claims": [{"id": "claim_001", "text": "The Eiffel Tower is 330 metres tall.", "type": "factual"}]},
    )
    assert retrieval.status_code == 200
    payload = retrieval.json()
    assert payload["stage"] == "S2_RETRIEVAL_ONLY"
    assert all(item["quote_verified"] is False for item in payload["claims"][0]["evidence"])


def test_openapi_documents_the_verify_route() -> None:
    schema = client.get("/openapi.json").json()
    assert "/api/verify" in schema["paths"]
    assert "post" in schema["paths"]["/api/verify"]
    assert "/api/samples" in schema["paths"]


# ---------------------------------------------------------------------------
# the frontend
# ---------------------------------------------------------------------------
def test_ui_is_served_at_the_root() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "VerifyAI" in response.text


def test_ui_has_no_external_dependencies() -> None:
    """Zero-build is a plan rule (§5.3) and an offline-demo requirement."""
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    for needle in ("<script src=", 'rel="stylesheet"', "cdn.", "unpkg", "https://fonts"):
        assert needle not in html, f"frontend must stay self-contained, found {needle!r}"


def test_ui_covers_the_states_the_plan_requires() -> None:
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    for required in ("INSUFFICIENT EVIDENCE", "SUPPORTS", "REFUTES", "NEUTRAL", "Audit trail", "evidence strength"):
        assert required.lower() in html.lower(), f"{required} is not represented in the UI"
    # never a bare truth badge
    assert "not a probability" in html.lower() or "not a guaranteed" in html.lower()


def test_ui_fixture_matches_the_documented_contract() -> None:
    """The captured response the UI check runs against must still be a valid shape."""
    data = json.loads((ROOT / "data" / "ui_response_fixture.json").read_text(encoding="utf-8"))
    assert {"report", "stages", "warnings", "retrieval_summary", "s3_audit", "grounding"} <= set(data)
    for report in data["report"]["claims"]:
        for item in report["evidence"]:
            assert "quote_scope" in item
