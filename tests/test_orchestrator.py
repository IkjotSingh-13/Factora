from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, Sequence

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.models import ClaimInput, Verdict
from app.pipeline.orchestrator import run_verified_claim_pipeline


AS_OF = date(2026, 10, 31)
client = TestClient(app)


def _claim() -> ClaimInput:
    return ClaimInput(
        id="claim_001",
        text="A synthetic checkable claim for an integration test.",
        type="factual",
    )


def _bundle() -> dict[str, Any]:
    claim = _claim().model_dump(mode="json")
    evidence = []
    for index, domain in enumerate(
        ("source-a.example", "source-b.example", "source-c.example"), start=1
    ):
        evidence.append(
            {
                "id": f"ev_{index}",
                "claim_id": "claim_001",
                "url": f"https://{domain}/test",
                "domain": domain,
                "title": f"Synthetic test source {index}",
                "snippet": f"Synthetic quote from source {index}.",
                "full_text": "",
                "published_at": "2025-10-01",
                "source_kind": "fact_check",
                "relevance": 0.9,
                "credibility": 0.9,
                "stance": "NEUTRAL",
                "quote": "",
                "quote_verified": False,
                "rationale": "",
                "context_score": 0.9,
                "context_gap": False,
            }
        )
    return {"claim": claim, "evidence": evidence}


def test_full_orchestrator_applies_analysis_before_scoring() -> None:
    async def fake_retrieve(claims: Sequence[Any]) -> list[dict[str, Any]]:
        assert len(claims) == 1
        return [_bundle()]

    async def fake_analyze(
        claims: Sequence[Any], bundles: Sequence[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        assert len(bundles) == 1
        return [
            {
                "evidence_id": item["id"],
                "stance": "SUPPORTS",
                "quote": item["snippet"],
                "quote_verified": True,
                "rationale": "Synthetic test analysis only.",
                "context_score": 0.9,
                "context_gap": False,
            }
            for item in bundles[0]["evidence"]
        ]

    report = asyncio.run(
        run_verified_claim_pipeline(
            [_claim()],
            analyze_fn=fake_analyze,
            retrieve_fn=fake_retrieve,
            input_meta={"title": "Synthetic integration test"},
            as_of=AS_OF,
        )
    )

    claim_report = report.claims[0]
    assert claim_report.verdict == Verdict.SUPPORTED
    assert claim_report.audit.n_retrieved == 3
    assert claim_report.audit.n_used == 3
    assert claim_report.audit.n_independent == 3
    assert all(item.quote_verified for item in claim_report.evidence)


def test_quote_guard_blocks_fabricated_analysis_quote() -> None:
    async def fake_retrieve(claims: Sequence[Any]) -> list[dict[str, Any]]:
        return [_bundle()]

    async def fake_analyze(
        claims: Sequence[Any], bundles: Sequence[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        return [
            {
                "evidence_id": item["id"],
                "stance": "SUPPORTS",
                "quote": "A made-up sentence not present in the source.",
                "quote_verified": True,
                "rationale": "Synthetic negative test.",
            }
            for item in bundles[0]["evidence"]
        ]

    report = asyncio.run(
        run_verified_claim_pipeline(
            [_claim()],
            analyze_fn=fake_analyze,
            retrieve_fn=fake_retrieve,
            as_of=AS_OF,
        )
    )

    claim_report = report.claims[0]
    assert claim_report.verdict == Verdict.INSUFFICIENT_EVIDENCE
    assert claim_report.audit.n_used == 0
    assert claim_report.evidence == []


def test_retrieval_preview_is_explicitly_not_a_verdict(monkeypatch) -> None:
    async def fake_retrieve(claims: Sequence[Any]) -> list[dict[str, Any]]:
        bundle = _bundle()
        return [{"claim": bundle["claim"], "evidence": bundle["evidence"][:1]}]

    monkeypatch.setattr(main_module, "aretrieve_for_claims", fake_retrieve)
    response = client.post(
        "/api/retrieve",
        json={
            "input": {"title": "Synthetic test"},
            "claims": [{"id": "claim_001", "text": "A test claim", "type": "factual"}],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["stage"] == "S2_RETRIEVAL_ONLY"
    assert "Do not treat this response as a claim verdict" in payload["note"]
    assert payload["retrieval_summary"]["evidence_items"] == 1
    assert payload["claims"][0]["evidence"][0]["quote_verified"] is False
