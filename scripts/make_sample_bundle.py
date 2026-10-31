"""Produce a sample S2 bundle for the S3 handoff (Mansimran).

    python scripts/make_sample_bundle.py                # offline, deterministic (CI-safe)
    python scripts/make_sample_bundle.py --live         # real sources + full-text fetching
    python scripts/make_sample_bundle.py --live --claims-file my_claims.txt

Writes ``data/sample_s2_bundle.json``: exactly what ``aretrieve_for_claims``
returns, so S3 can develop against the real shape without a network or keys.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from s2_retrieval import RetrievalConfig, RetrievalEngine  # noqa: E402
from s2_service import retrieval_summary, retrieve_for_claims  # noqa: E402

DEFAULT_CLAIMS = [
    {"id": "claim_001", "text": "Vitamin C supplementation prevents the common cold in most adults",
     "entities": ["Vitamin C"], "claim_type": "factual"},
    {"id": "claim_002", "text": "The Great Wall of China is visible from space with the naked eye",
     "entities": ["Great Wall of China"], "claim_type": "factual"},
    {"id": "claim_003", "text": "The VerifyAI team invented the quantum banana protocol in 2026",
     "entities": ["VerifyAI"], "claim_type": "factual"},
]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="use real sources (default: offline corpus)")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--fetch-full-text", type=int, default=3)
    parser.add_argument("--claims-file")
    parser.add_argument("--out", default=str(ROOT / "data" / "sample_s2_bundle.json"))
    args = parser.parse_args(argv)

    claims = DEFAULT_CLAIMS
    if args.claims_file:
        claims = [
            {"id": f"claim_{i:03d}", "text": line.strip(), "entities": [], "claim_type": "factual"}
            for i, line in enumerate(
                Path(args.claims_file).read_text(encoding="utf-8").splitlines(), start=1
            )
            if line.strip() and not line.lstrip().startswith("#")
        ]

    config = RetrievalConfig(
        top_k=args.top_k,
        offline=not args.live,
        fetch_full_text=args.fetch_full_text if args.live else 0,
    )
    engine = RetrievalEngine(config=config)
    bundles = retrieve_for_claims(claims, engine=engine)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "live" if args.live else "offline",
        "note": (
            "Sample output of s2_service.retrieve_for_claims(). S2 is retrieval-only: "
            "every evidence item has stance=NEUTRAL and quote_verified=false. "
            "'quote_guard' says what a quote may be checked against per item."
        ),
        "retrieval_summary": retrieval_summary(bundles),
        "bundles": bundles,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # dates are python `date` objects (the lead's model expects them); stringify for the file
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    print(f"mode: {payload['mode']}   wrote {out}")
    print(f"summary: {payload['retrieval_summary']}")
    for bundle in bundles:
        scopes = {}
        for item in bundle["evidence"]:
            key = "full_text" if item.get("full_text") else "snippet"
            scopes[key] = scopes.get(key, 0) + 1
        print(f"  {bundle['claim']['id']} ({bundle['claim']['type']}): "
              f"{len(bundle['evidence'])} evidence, scopes={scopes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
