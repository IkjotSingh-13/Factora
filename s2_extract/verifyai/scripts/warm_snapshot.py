"""Capture a demo snapshot (Mansimran).

Runs real retrieval over your demo claims once, saves the results, and lets the
app replay them offline forever. Do this **before** the demo, on decent wifi.

    python scripts/warm_snapshot.py                        # default demo claims
    python scripts/warm_snapshot.py --claims-file demo.txt
    python scripts/warm_snapshot.py --offline              # capture from local corpus (no network)

Then verify the replay path with wifi off:

    python s2_retrieval.py --replay-snapshot data/demo_snapshot.json "vitamin C common cold"

The snapshot file is also a ready-made fixture for S3 stance analysis and for
Taranpreet's UI: pass `data/demo_snapshot.json` and they get real evidence with
zero setup.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from s2_retrieval import RetrievalConfig, RetrievalEngine  # noqa: E402
from snapshot import build_snapshot_entry, save_snapshot  # noqa: E402

DEFAULT_CLAIMS = [
    # supported
    "The Eiffel Tower is 330 metres tall.",
    # contested / context-sensitive
    "Vitamin C supplementation prevents the common cold in most adults.",
    # well-known myth
    "The Great Wall of China is visible from space with the naked eye.",
    # thin evidence (the honest "insufficient evidence" case)
    "The VerifyAI team invented the quantum banana protocol in 2026.",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture a retrieval snapshot for the demo")
    parser.add_argument("--claims-file", help="one claim per line (# comments allowed)")
    parser.add_argument("--out", default=str(ROOT / "data" / "demo_snapshot.json"))
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--offline", action="store_true", help="capture from the local corpus only")
    parser.add_argument("--mock", action="store_true", help="live sources + local corpus")
    args = parser.parse_args(argv)

    claims = DEFAULT_CLAIMS
    if args.claims_file:
        claims = [
            line.strip()
            for line in Path(args.claims_file).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

    engine = RetrievalEngine(
        RetrievalConfig(
            top_k=args.top_k,
            offline=args.offline,
            mock=args.mock,
            disk_cache_dir=str(ROOT / ".cache" / "retrieval"),
        )
    )

    entries = []
    print(f"\nCapturing {len(claims)} claims (top_k={args.top_k})...\n")
    for claim in claims:
        result = engine.search_claim(claim)
        entries.append(
            build_snapshot_entry(
                claim,
                queries=result.audit.queries,
                evidence=result.evidence,
                audit=result.audit,
            )
        )
        audit = result.audit
        flag = "OK " if result.evidence else "THIN"
        print(
            f"  [{flag}] {claim[:60]:<62} ev={audit.returned:<2} "
            f"domains={audit.distinct_domains:<2} {audit.duration_ms:>5} ms"
        )
        for status in audit.sources:
            if status.status in ("error", "timeout"):
                print(f"         ! {status.name}: {status.status}")

    mode = "offline" if args.offline else ("mock" if args.mock else "live")
    path = save_snapshot(args.out, entries, mode=mode, config=engine.config.to_dict())

    thin = [e for e in entries if not e["evidence"]]
    print(f"\nWrote {path}")
    print(f"  claims: {len(entries)}   with evidence: {len(entries) - len(thin)}   mode: {mode}")
    if thin:
        print(f"  no evidence captured for: {', '.join(e['claim'][:40] for e in thin)}")
        print("  (that is fine if it is your intentional insufficient-evidence example)")
    print("\nNow test the offline replay (turn wifi off if you want to be sure):")
    print(f'  python s2_retrieval.py --replay-snapshot "{args.out}" "vitamin C common cold"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
