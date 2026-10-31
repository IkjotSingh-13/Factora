from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_endpoint() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_score_endpoint_returns_transparent_report() -> None:
    payload = {
        "input": {"url": "https://example.org/article", "title": "Sample article"},
        "as_of": "2026-10-31",
        "claims": [
            {
                "claim": {"id": "c1", "text": "A sample claim that can be checked."},
                "evidence": [
                    {
                        "id": "e1",
                        "claim_id": "c1",
                        "url": "https://who.int/example",
                        "domain": "who.int",
                        "title": "Source A",
                        "published_at": "2025-10-01",
                        "relevance": 0.9,
                        "credibility": 0.9,
                        "stance": "SUPPORTS",
                        "quote": "This exact sentence is present in the source.",
                        "quote_verified": True,
                        "context_score": 0.9,
                    },
                    {
                        "id": "e2",
                        "claim_id": "c1",
                        "url": "https://gov.example/example",
                        "domain": "gov.example",
                        "title": "Source B",
                        "published_at": "2025-10-02",
                        "relevance": 0.9,
                        "credibility": 0.9,
                        "stance": "SUPPORTS",
                        "quote": "This exact sentence is present in the source.",
                        "quote_verified": True,
                        "context_score": 0.9,
                    },
                ],
            }
        ],
    }

    response = client.post("/api/score", json=payload)
    assert response.status_code == 200
    data = response.json()
    claim_report = data["claims"][0]
    assert claim_report["verdict"] == "SUPPORTED"
    assert claim_report["stance_score"] > 0
    assert "C" in claim_report["scores"]
    assert claim_report["audit"]["n_used"] == 2
