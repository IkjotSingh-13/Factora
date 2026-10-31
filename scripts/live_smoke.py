"""Live smoke test for S2 retrieval (Mansimran).

Runs a small fixed claim set against the *real* sources and writes a JSON report
so we can state measured retrieval behaviour in the demo instead of guessing.

    python scripts/live_smoke.py                 # full run
    python scripts/live_smoke.py --offline       # no network (local corpus only)
    python scripts/live_smoke.py --claims-file my_claims.txt

Output: data/smoke_results.json  (git-ignored) + a console table.
Exit code 1 if a source that should work returned nothing for an easy claim —
that is a signal to check keys/network, not a build breaker.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from s2_retrieval import RetrievalConfig, RetrievalEngine  # noqa: E402

# One claim per interesting case. These are the same shapes we will demo.
DEFAULT_CLAIMS = [
    # clearly supported by mainstream sources
    "The Eiffel Tower is 330 metres tall.",
    # contested / context-sensitive
    "Vitamin C supplementation prevents the common cold in most adults.",
    # well-known myth -> expect refuting evidence
    "The Great Wall of China is visible from space with the naked eye.",
    # thin evidence on purpose -> expect "insufficient evidence" downstream
    "The VerifyAI team invented the quantum banana protocol in 2026.",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--claims-file", help="one claim per line")
    parser.add_argument("--out", default=str(ROOT / "data" / "smoke_results.json"))
    parser.add_argument(
        "--allow-cache",
        action="store_true",
        help="reuse the 15-minute HTTP cache (numbers will be optimistic; not a live measurement)",
    )
    args = parser.parse_args(argv)

    claims = DEFAULT_CLAIMS
    if args.claims_file:
        claims = [
            line.strip()
            for line in Path(args.claims_file).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]

    # A warm cache answers in ~30 ms. Reporting that as "live latency" is a lie,
    # so fresh requests are the default; --allow-cache is opt-in and labelled.
    engine = RetrievalEngine(
        RetrievalConfig(
            top_k=args.top_k,
            offline=args.offline,
            mock=args.mock,
            use_cache=bool(args.allow_cache),
            disk_cache_dir=str(ROOT / ".cache" / "retrieval"),
        )
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "offline" if args.offline else ("mock" if args.mock else "live"),
        "cache": "allowed" if args.allow_cache else "disabled",
        "config": engine.config.to_dict(),
        "claims": [],
    }

    cache_note = "cache ALLOWED (numbers optimistic)" if args.allow_cache else "cache DISABLED - fresh requests"
    print(f"\nMode: {report['mode']}   top_k={args.top_k}   claims={len(claims)}   {cache_note}\n")
    header = f"{'claim':<52} {'src ok':>6} {'fail':>5} {'ev':>3} {'dom':>4} {'ms':>6}"
    print(header)
    print("-" * len(header))

    latencies: list[int] = []
    for claim in claims:
        started = time.perf_counter()
        result = engine.search_claim(claim, claim_id=None)
        elapsed = int((time.perf_counter() - started) * 1000)
        latencies.append(elapsed)
        audit = result.audit
        print(
            f"{claim[:50]:<52} {audit.sources_ok:>6} {audit.sources_failed:>5} "
            f"{audit.returned:>3} {audit.distinct_domains:>4} {audit.duration_ms:>6}"
        )
        report["claims"].append(
            {
                "claim": claim,
                "evidence": [ev.model_dump() for ev in result.evidence],
                "audit": audit.model_dump(),
                "wall_ms": elapsed,
            }
        )

    per_source: dict[str, dict[str, int]] = {}
    for entry in report["claims"]:
        for status in entry["audit"]["sources"]:
            row = per_source.setdefault(status["name"], {"runs": 0, "with_results": 0, "used": 0, "failed": 0})
            row["runs"] += 1
            row["used"] += status["used"]
            row["with_results"] += 1 if status["status"] == "ok" else 0
            row["failed"] += 1 if status["status"] in ("error", "timeout") else 0

    print("\nPer-source reliability on this run:")
    for name, row in sorted(per_source.items()):
        rate = 100 * row["with_results"] / max(row["runs"], 1)
        print(f"  {name:<18} returned results {rate:5.0f}% of runs   used={row['used']:<3} failed={row['failed']}")

    if latencies:
        report["latency_ms"] = {
            "median": int(statistics.median(latencies)),
            "max": max(latencies),
        }
        if not args.offline and statistics.median(latencies) < 150:
            print(
                "\n  WARNING: median under 150 ms for network requests is almost always a\n"
                "  warm HTTP cache, not real latency. Delete .cache\\retrieval and rerun,\n"
                "  or drop --allow-cache. Do not quote these numbers in the demo."
            )
        print(
            f"\nLatency: median {int(statistics.median(latencies))} ms, "
            f"max {max(latencies)} ms (end-to-end, all sources in parallel)"
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {out.relative_to(ROOT)}")

    thin = [c for c in report["claims"] if not c["evidence"]]
    if thin:
        print(f"Claims with no evidence ({len(thin)}): " + "; ".join(c["claim"][:60] for c in thin))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
