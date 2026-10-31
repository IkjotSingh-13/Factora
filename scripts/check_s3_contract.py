"""S3 contract checker (Mansimran) — run this against Ikjot's analyzer.

The seam between S2 and S3 is where a demo quietly breaks: quotes that are not in
the retrieved text, too few independent domains, analyses that match no evidence.
This script runs a real analyzer against real (or snapshot) retrieval and reports
exactly what the scorer will do with its output.

    # offline, against the captured demo snapshot (no network, no keys)
    python scripts/check_s3_contract.py --analyze my_s3:analyze --replay data/demo_snapshot.json

    # live sources
    python scripts/check_s3_contract.py --analyze my_s3:analyze

    # without an analyzer: just show what the seam receives
    python scripts/check_s3_contract.py --replay data/demo_snapshot.json

`--analyze module:function` — the function is called as ``analyze(claims, bundles)``
and may be sync or async, exactly as the orchestrator calls it. It must return a
sequence of dicts/objects carrying ``evidence_id`` (or ``url``), ``stance``,
``quote``, ``quote_verified``, and optionally ``rationale``, ``context_score``,
``context_gap``.

Exit code 0 = the analyzer is compatible. 1 = at least one BLOCKER.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import json
import sys
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from contract_adapter import SCOPE_FULL_TEXT, SCOPE_SNIPPET
from s2_retrieval import RetrievalConfig, RetrievalEngine
from s2_service import (
    apply_analysis,
    retrieval_summary,
    retrieve_for_claims,
    verification_scopes,
)

BLOCK = "BLOCKER"
WARN = "warning"
OK = "ok  "

DEFAULT_CLAIMS = [
    {"id": "claim_001", "text": "Vitamin C supplementation prevents the common cold in most adults",
     "entities": ["Vitamin C"], "claim_type": "factual"},
    {"id": "claim_002", "text": "The Great Wall of China is visible from space with the naked eye",
     "entities": ["Great Wall of China"], "claim_type": "factual"},
]


def load_analyzer(spec: str):
    module_name, _, func_name = spec.partition(":")
    if not func_name:
        raise SystemExit("--analyze expects module:function, e.g. my_s3:analyze")
    module = importlib.import_module(module_name)
    func = getattr(module, func_name, None)
    if func is None:
        raise SystemExit(f"{module_name} has no attribute {func_name!r}")
    return func


def run_analyzer(func, claims, bundles):
    result = func(claims, bundles)
    if inspect.isawaitable(result):
        result = asyncio.run(result)
    return list(result or [])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate an S3 analyzer against the S2 seam")
    parser.add_argument("--analyze", help="module:function implementing S3")
    parser.add_argument("--replay", help="run retrieval from a snapshot file (offline)")
    parser.add_argument("--offline", action="store_true", help="use the local mock corpus")
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--claims-file", help="one claim per line")
    parser.add_argument("--min-domains", type=int, default=3,
                        help="independent domains needed for a decisive verdict (scorer shrinks below this)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    claims = DEFAULT_CLAIMS
    if args.claims_file:
        claims = [
            {"id": f"claim_{i:03d}", "text": line.strip(), "entities": [], "claim_type": "factual"}
            for i, line in enumerate(Path(args.claims_file).read_text(encoding="utf-8").splitlines(), 1)
            if line.strip() and not line.lstrip().startswith("#")
        ]

    engine = RetrievalEngine(
        RetrievalConfig(
            top_k=args.top_k,
            offline=args.offline,
            replay_snapshot=args.replay,
            fetch_full_text=0 if (args.offline or args.replay) else 3,
        )
    )
    bundles = retrieve_for_claims(claims, engine=engine)

    print("\nS3 ↔ S2 seam check")
    print("=" * 68)
    print(f"claims: {len(bundles)}   evidence: {sum(len(b['evidence']) for b in bundles)}")
    scopes = verification_scopes(bundles)
    print(f"verification scopes: {scopes[SCOPE_FULL_TEXT]} full_text, {scopes[SCOPE_SNIPPET]} snippet-only")

    problems: list[tuple[str, str]] = []

    # --- retrieval-side preconditions -------------------------------------
    for bundle in bundles:
        cid = bundle["claim"]["id"]
        domains = {e["domain"] for e in bundle["evidence"]}
        if not bundle["evidence"]:
            problems.append((WARN, f"{cid}: no evidence retrieved — verdict will be INSUFFICIENT"))
        elif len(domains) < args.min_domains:
            problems.append(
                (WARN, f"{cid}: only {len(domains)} independent domain(s); "
                       f"the scorer shrinks by n_independent/3, so a decisive verdict needs "
                       f"{args.min_domains}+")
            )

    if not args.analyze:
        print("\n(no --analyze given: showing the seam input only)")
        _print_bundles(bundles)
        return 0

    analyzer = load_analyzer(args.analyze)
    try:
        analyses = run_analyzer(analyzer, claims, bundles)
    except Exception as exc:
        print(f"\n{BLOCK} analyzer raised: {type(exc).__name__}: {exc}")
        return 1

    print(f"\nanalyzer returned {len(analyses)} analyses")

    # --- validate each analysis against the evidence it claims to describe --
    by_id = {e["id"]: e for b in bundles for e in b["evidence"]}
    by_url = {e["url"].rstrip("/"): e for b in bundles for e in b["evidence"]}
    matched: set[str] = set()
    unverified_claims = 0
    snippet_scope_verifications = 0
    fabricated = 0
    bad_stance: list[str] = []

    for analysis in analyses:
        eid = _get(analysis, "evidence_id", "id")
        url = _get(analysis, "url")
        item = by_id.get(str(eid)) if eid else None
        if item is None and url:
            item = by_url.get(str(url).rstrip("/"))
        if item is None:
            problems.append((BLOCK, f"analysis matches no evidence (evidence_id={eid!r} url={url!r})"))
            continue
        matched.add(item["id"])

        stance = str(_get(analysis, "stance") or "").upper()
        if stance not in {"SUPPORTS", "REFUTES", "NEUTRAL"}:
            bad_stance.append(f"{item['id']}:{stance or '<empty>'}")

        quote = str(_get(analysis, "quote") or "")
        claimed = bool(_get(analysis, "quote_verified"))
        region = str(item.get("full_text") or "")
        scope = SCOPE_FULL_TEXT if region else SCOPE_SNIPPET
        if not region:
            region = str(item.get("snippet") or "")

        if claimed:
            if not quote.strip():
                problems.append((BLOCK, f"{item['id']}: quote_verified=True but quote is empty"))
                unverified_claims += 1
            elif _norm(quote) not in _norm(region):
                problems.append(
                    (BLOCK, f"{item['id']}: quote not found in retrieved {scope} text "
                            f"(excerpt {quote[:60]!r}) — the guard will drop it")
                )
                fabricated += 1
            elif scope == SCOPE_SNIPPET:
                snippet_scope_verifications += 1

    # --- seam-level outcome -------------------------------------------------
    # deep copy without JSON (published_at may be a datetime.date)
    import copy

    merged = apply_analysis([copy.deepcopy(b) for b in bundles], analyses)
    verified_total = 0
    independent_total = 0
    per_claim: dict[str, Any] = {}
    for bundle in merged:
        cid = bundle["claim"]["id"]
        verified = [e for e in bundle["evidence"] if e["quote_verified"]]
        domains = {e["domain"] for e in verified}
        nonzero = {e["stance"] for e in verified if e["stance"] != "NEUTRAL"}
        verified_total += len(verified)
        independent_total += len(domains)
        per_claim[cid] = {
            "verified_items": len(verified),
            "independent_domains": len(domains),
            "stance_mix": sorted(nonzero) or ["NEUTRAL"],
            "decisive_possible": len(domains) >= args.min_domains,
        }
        if not verified:
            problems.append(
                (BLOCK, f"{cid}: zero quote-verified items — score_claim will return INSUFFICIENT EVIDENCE")
            )
        elif len(domains) < args.min_domains:
            problems.append(
                (WARN, f"{cid}: {len(domains)} verified domain(s) < {args.min_domains}; "
                       f"verdict will be MIXED / INSUFFICIENT, not decisive")
            )

    if bad_stance:
        problems.append((BLOCK, f"invalid stance values (mapped to NEUTRAL): {', '.join(bad_stance[:5])}"))
    if snippet_scope_verifications:
        problems.append(
            (WARN, f"{snippet_scope_verifications} quote(s) only checkable against a snippet: "
                   f"say 'verified against the retrieved snippet', not 'verified in the source'")
        )

    # --- report ------------------------------------------------------------
    print("\nper claim:")
    for cid, row in per_claim.items():
        flag = "DECISIVE" if row["decisive_possible"] else "shrink-limited"
        print(f"  {cid}: {row['verified_items']} verified item(s), "
              f"{row['independent_domains']} domain(s), stances={row['stance_mix']}  [{flag}]")

    print(f"\nquote guard: {fabricated} fabricated, {unverified_claims} empty-but-claimed, "
          f"{len(matched)}/{len(by_id)} evidence items analysed")

    print("\nfindings:")
    if not problems:
        print(f"  {OK} no issues — S3 output is compatible with the seam")
    else:
        for severity, message in problems:
            print(f"  [{severity}] {message}")

    blockers = sum(1 for s, _ in problems if s == BLOCK)
    print("\n" + "=" * 68)
    if blockers:
        print(f"Verdict: {blockers} blocker(s). The pipeline will run, but verdicts will not be decisive.")
    else:
        print("Verdict: S3 is compatible with the S2 seam.")

    print("\nWhat the scorer will do next:")
    print("  score_claim uses only evidence with quote_verified=True and a non-empty quote,")
    print("  then shrinks by n_independent/3. Reach the domain count above for a decisive verdict.")

    if args.json:
        print("\n" + json.dumps({"per_claim": per_claim, "summary": retrieval_summary(bundles),
                                 "findings": [{"severity": s, "message": m} for s, m in problems]},
                                indent=2))
    return 0 if blockers == 0 else 1


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def _get(obj: Any, *names: str) -> Any:
    if isinstance(obj, dict):
        for name in names:
            if name in obj:
                return obj[name]
        return None
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


def _print_bundles(bundles) -> None:
    for bundle in bundles:
        claim = bundle["claim"]
        print(f"\nclaim {claim['id']}  [{claim['type']}]  {claim['text'][:70]}")
        print(f"  quote_guard: {bundle.get('quote_guard')}")
        for item in bundle["evidence"][:4]:
            scope = "full_text" if item.get("full_text") else "snippet"
            print(f"    {item['id']}  {item['domain']:<26} {scope:<9} "
                  f"rel={item['relevance']:.2f} stance={item['stance']} verified={item['quote_verified']}")


if __name__ == "__main__":
    raise SystemExit(main())
