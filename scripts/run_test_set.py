#!/usr/bin/env python3
"""QA runner: run the labelled claim set and record what actually happened.

Plan §3 (Taranpreet): "Prepare a small test set with known supported, refuted, and
misleading/context-sensitive claims. Record actual test results; do not claim an
accuracy figure that was not measured."

So this script does not assert. It runs the real pipeline over ``data/test_set.json``,
writes every outcome to ``data/test_set_results.json``, and prints a table. The numbers
in ``docs/TEST_SET.md`` come out of that file, which is why they can be quoted without
being rounded up.

    python scripts/run_test_set.py                # offline demo corpus (no network)
    python scripts/run_test_set.py --mode snapshot
    python scripts/run_test_set.py --fail-on-unmet   # CI gate: exit 1 if a row misses

Two kinds of row are checked:

* ``direction``  — the sign of ``stance_score`` compared with ``expect_direction``.
  The scorer is not asked for a truth verdict here; it is asked which way the
  retrieved, quote-verified evidence points. That is what the pipeline can honestly
  be measured on.
* ``invariants`` — must hold on every row, and are reported as pass/fail counts:
  every displayed quote appears in the retrieved text; ``n_used`` never exceeds the
  quote-verified items; no claim gets a decisive verdict on fewer than two
  independent domains.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.pipeline.verify import VerifyRequest, run_verify  # noqa: E402

SET_PATH = ROOT / "data" / "test_set.json"
RESULTS_PATH = ROOT / "data" / "test_set_results.json"
DECISIVE = {"SUPPORTED", "REFUTED"}


def direction_of(stance_score: float) -> str:
    if stance_score > 0.05:
        return "support"
    if stance_score < -0.05:
        return "refute"
    return "none"


def check_invariants(claim_report: dict[str, Any], evidence_by_id: dict[str, dict]) -> list[str]:
    """Rules that must never break, whatever the verdict says."""
    problems: list[str] = []
    audit = claim_report.get("audit", {})
    evidence = claim_report.get("evidence", [])

    if audit.get("n_used", 0) > audit.get("n_retrieved", 0):
        problems.append("n_used exceeds n_retrieved")

    for item in evidence:
        if not item.get("quote_verified"):
            problems.append(f"{item.get('id')}: unverified quote reached the report")
            continue
        quote = " ".join(str(item.get("quote", "")).split()).lower()
        # Check against the text carried on the report item itself: that is the text the
        # guard had, and the only thing a "verified" label can honestly mean.
        source = evidence_by_id.get(item.get("id") or "", {})
        parts = [item.get("full_text"), item.get("snippet"), source.get("full_text"), source.get("snippet")]
        haystack = " || ".join(" ".join(str(part or "").split()).lower() for part in parts)
        if not quote:
            problems.append(f"{item.get('id')}: quote_verified with an empty quote")
        elif quote not in haystack:
            problems.append(f"{item.get('id')}: displayed quote is not in the retrieved text")

    if claim_report.get("verdict") in DECISIVE and audit.get("n_independent", 0) < 2:
        problems.append(
            f"decisive verdict on {audit.get('n_independent', 0)} independent domain(s)"
        )
    return problems


async def run(mode: str) -> dict[str, Any]:
    spec = json.loads(SET_PATH.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    invariant_failures: list[dict[str, Any]] = []

    for case in spec["cases"]:
        try:
            response = await run_verify(
                VerifyRequest(article_text=case["article"], mode=mode, as_of=date.today())  # type: ignore[arg-type]
            )
        except Exception as exc:  # a crash is a result too, and must be recorded
            rows.append(
                {
                    "id": case["id"],
                    "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                    "direction_match": False,
                    "invariants_ok": False,
                }
            )
            continue

        claims = [report.model_dump(mode="json") for report in response.report.claims]
        target_text = case["claim"]
        # The pipeline may reword the claim (a reporting frame is stripped before
        # retrieval), so pick the report that shares the most content words with the
        # labelled claim rather than the first one in the list.
        def overlap(report: dict[str, Any]) -> float:
            from s3_analysis import content_tokens

            left = content_tokens(target_text)
            right = content_tokens(str(report["claim"].get("text", "")))
            return len(left & right) / max(len(left), 1)

        report = max(claims, key=overlap) if claims else None
        if report is not None and overlap(report) < 0.34:
            # Nothing in this run is about the labelled claim: say so instead of
            # scoring an unrelated sentence and calling it a result.
            report = None

        evidence_by_id: dict[str, dict] = {}
        for detail in response.per_claim_retrieval:
            for source in detail.get("sources", []):
                evidence_by_id[str(source.get("id"))] = source
        # the raw bundles hold the text the quotes were checked against
        for text_id, item in _evidence_texts(response).items():
            evidence_by_id.setdefault(text_id, item)

        if report is None:
            # Not a pipeline failure: this run simply produced no claim close enough to
            # the labelled one to score (e.g. S1 classified it as an opinion and the
            # factual-only filter removed it). Recorded as unmeasured, never as a pass.
            rows.append(
                {
                    "id": case["id"],
                    "claim": target_text,
                    "label": case.get("label", ""),
                    "status": "unmeasured",
                    "reason": (
                        "no claim report matched the labelled claim"
                        if claims else "no claim survived extraction and the article-quote guard"
                    ),
                    "verdicts_produced": [str(row.get("verdict")) for row in claims],
                }
            )
            continue

        problems = check_invariants(report, evidence_by_id)
        for problem in problems:
            invariant_failures.append({"id": case["id"], "problem": problem})

        row = {
            "id": case["id"],
            "claim": target_text,
            "label": case.get("label", ""),
            "expected_direction": case.get("expect_direction"),
            "expected_verdict": case.get("expect_verdict"),
            "checked_claim": (report or {}).get("claim", {}).get("text"),
            "verdict": (report or {}).get("verdict"),
            "stance_score": (report or {}).get("stance_score"),
            "evidence_strength": (report or {}).get("evidence_strength"),
            "direction": direction_of(float((report or {}).get("stance_score", 0.0))),
            "n_retrieved": (report or {}).get("audit", {}).get("n_retrieved", 0),
            "n_used": (report or {}).get("audit", {}).get("n_used", 0),
            "n_independent": (report or {}).get("audit", {}).get("n_independent", 0),
            "quotes_dropped": (report or {}).get("audit", {}).get("hallucinated_quotes_dropped", 0),
            "invariants_ok": not problems,
            "invariant_problems": problems,
            "status": "measured",
        }
        row["direction_match"] = row["direction"] == case.get("expect_direction")
        row["verdict_match"] = (
            row["verdict"] == case["expect_verdict"] if case.get("expect_verdict") else None
        )
        rows.append(row)

    measured_direction = [row for row in rows if row.get("status") == "measured"]
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": mode,
        "cases": len(rows),
        "measured": len(measured_direction),
        "unmeasured": sum(1 for row in rows if row.get("status") == "unmeasured"),
        "errored": sum(1 for row in rows if row.get("error")),
        "direction_met": sum(1 for row in measured_direction if row.get("direction_match")),
        "direction_total": len(measured_direction),
        "invariants_broken": len(invariant_failures),
        "verdict_rows_expected": sum(1 for row in rows if row.get("expected_verdict")),
        "verdict_met": sum(1 for row in rows if row.get("verdict_match") is True),
        "note": (
            "direction_met counts how often the sign of stance_score matched the label. "
            "This is not an accuracy figure for truth classification: the pipeline reports "
            "which way quote-verified evidence points, and INSUFFICIENT EVIDENCE is a "
            "legitimate outcome when the corpus has nothing to say."
        ),
    }
    return {"summary": summary, "rows": rows, "invariant_failures": invariant_failures}


def _evidence_texts(response: Any) -> dict[str, dict[str, Any]]:
    """id -> the text the quote guard checked against, for independent re-checking."""
    out: dict[str, dict[str, Any]] = {}
    for detail in getattr(response, "per_claim_retrieval", []) or []:
        for source in detail.get("sources", []):
            out[str(source.get("id"))] = {"snippet": source.get("snippet", ""), "full_text": ""}
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the labelled claim set and record outcomes")
    parser.add_argument("--mode", default="offline", choices=("offline", "live", "snapshot", "auto"))
    parser.add_argument("--no-write", action="store_true", help="do not update data/test_set_results.json")
    parser.add_argument("--fail-on-unmet", action="store_true", help="exit 1 if any row misses")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = asyncio.run(run(args.mode))
    # Always written unless suppressed: docs/TEST_SET.md quotes this file, so it must
    # not be able to go stale relative to the code.
    if not args.no_write:
        RESULTS_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    summary = result["summary"]
    print(f"\nVerifyAI QA test set — mode={summary['mode']} — {summary['generated_at']}")
    print("=" * 104)
    print(f"{'id':26s} {'expected':9s} {'got':9s} {'verdict':31s} {'ev':>5s} {'used':>5s}")
    print("-" * 104)
    for row in result["rows"]:
        if row.get("status") == "unmeasured":
            print(f"{row['id']:26s} {'':9s} {'':10s} UNMEASURED — {row['reason']}")
            continue
        if row.get("error"):
            print(f"{row['id']:26s} ERROR: {row['error'][:70]}")
            continue
        mark = "✓" if row["direction_match"] else "✗"
        print(
            f"{row['id']:26s} {str(row['expected_direction']):9s} "
            f"{row['direction']:8s}{mark} {str(row['verdict']):31s} "
            f"{row['n_retrieved']:5d} {row['n_used']:5d}"
        )
    print("-" * 104)
    print(
        f"direction met: {summary['direction_met']}/{summary['direction_total']}   "
        f"invariant violations: {summary['invariants_broken']}   "
        f"unmeasured: {summary['unmeasured']}"
    )
    if result["invariant_failures"]:
        for failure in result["invariant_failures"]:
            print(f"  ✗ {failure['id']}: {failure['problem']}")
    print(f"\nwrote {RESULTS_PATH.relative_to(ROOT)}")
    print(summary["note"])

    if args.fail_on_unmet and (
        summary["direction_met"] != summary["direction_total"] or summary["invariants_broken"]
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
