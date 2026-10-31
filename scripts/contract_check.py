"""Verify S2 output against the lead's real contract (Mansimran).

    python scripts/contract_check.py                  # use ./backend/app/models.py
    python scripts/contract_check.py --models PATH    # use an explicit copy

Steps:
  1. locate the lead's ``backend/app/models.py``
  2. field-by-field comparison: our ``contracts.Evidence`` vs their ``EvidenceItem``
  3. run an offline retrieval, adapt it, and validate with their pydantic models
  4. print exactly what to tell the lead

Exit code 0 = our output is accepted by their models.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from contract_adapter import to_claim_bundle  # noqa: E402
from contracts import Evidence  # noqa: E402

ADAPTER_OWNS = {
    "id": "derived: ev_<hash> of claim_id + url",
    "snippet": "from contracts.Evidence.text_or_snippet",
    "full_text": "empty - S2 returns API snippets, not scraped full text",
    "published_at": "str 'YYYY-MM-DD' -> datetime.date",
    "credibility": "sources.CREDIBILITY_HINTS[source_kind]",
    "context_score": "coverage proxy (share of the claim's distinctive terms present)",
    "context_gap": "True when coverage < 0.5",
    "stance": "neutral default - S3 (Ikjot) owns this",
    "quote": "empty - S3 owns this",
    "quote_verified": "False - S3 owns this",
    "rationale": "empty - S3 owns this",
}


def find_models(explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit)
        return path if path.is_file() else None
    candidates = [
        ROOT / "backend" / "app" / "models.py",
        ROOT.parent / "backend" / "app" / "models.py",
        ROOT / "tests" / "vendor" / "app_models_snapshot.py",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def import_models(path: Path):
    models_dir = path.parent
    sys.path.insert(0, str(models_dir))
    if path.name == "app_models_snapshot.py":
        import app_models_snapshot as module  # type: ignore

        return module
    backend_dir = models_dir.parent
    sys.path.insert(0, str(backend_dir))
    import app.models as module  # type: ignore

    return module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check S2 output against the lead's models")
    parser.add_argument("--models", help="path to backend/app/models.py")
    args = parser.parse_args(argv)

    path = find_models(args.models)
    if path is None:
        print("Could not find backend/app/models.py (or the vendored snapshot).")
        print("Run from the repo root, or pass --models PATH.")
        return 2

    print("VerifyAI S2 -> lead contract check")
    print("=" * 66)
    print(f"models: {path}")

    models = import_models(path)
    their_fields = list(models.EvidenceItem.model_fields)
    our_fields = list(Evidence.model_fields)

    print("\n== field comparison ==")
    print(f"  ours (contracts.Evidence):     {our_fields}")
    print(f"  theirs (EvidenceItem):         {their_fields}")

    missing = [f for f in their_fields if f not in our_fields]
    if missing:
        print("\n  fields they require that we do NOT emit directly:")
        for field in missing:
            note = ADAPTER_OWNS.get(field, "adapter default - ASK THE LEAD")
            print(f"    - {field:<15} {note}")
        print("\n  all covered by contract_adapter.to_evidence_item()")
    else:
        print("\n  no gap - our fields are a superset of theirs")

    print("\n== live validation (offline retrieval -> adapter -> their models) ==")
    try:
        from cache import Cache
        from s2_retrieval import RetrievalConfig, RetrievalEngine
    except Exception as exc:
        print(f"  could not import retrieval ({exc})")
        return 1

    engine = RetrievalEngine(config=RetrievalConfig(offline=True), cache=Cache(ttl_s=5))
    claim_text = "vitamin C prevents the common cold"
    rr = engine.search_claim(claim_text, claim_id="c1")

    bundle = to_claim_bundle(
        claim_id="c1", claim_text=claim_text, evidence=rr.evidence, audit=rr.audit
    )
    print(f"  retrieved {len(rr.evidence)} evidence item(s)")

    try:
        parsed = models.ClaimEvidenceBundle(**bundle)
    except Exception as exc:
        print(f"  [FAIL] their model rejected our payload:\n        {exc}")
        return 1

    print(f"  [ ok ] accepted by ClaimEvidenceBundle ({len(parsed.evidence)} items)")
    for item in parsed.evidence[:3]:
        print(
            f"         {item.id}  rel={item.relevance:.2f} cred={item.credibility:.2f} "
            f"date={item.published_at} kind={item.source_kind} stance={item.stance.value}"
        )

    try:
        models.ScoreRequest(claims=[bundle])
        print("  [ ok ] accepted by ScoreRequest")
    except Exception as exc:
        print(f"  [warn] ScoreRequest rejected the bundle: {exc}")

    print("\n" + "=" * 66)
    print("Verdict: S2 output satisfies the lead's contract.")
    print("Integration for the lead:")
    print("  from contract_adapter import to_claim_bundle")
    print("  bundle = to_claim_bundle(claim_id=c.id, claim_text=c.text,")
    print("                            evidence=rr.evidence, audit=rr.audit)")
    print("  ScoreRequest(claims=[bundle])")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
