"""The QA test set is a real artefact, so it is checked like one.

Two things are verified here:

* running the labelled set offline still produces no invariant violations and no
  direction regressions (the safety properties the demo promises);
* the committed ``data/test_set_results.json`` — the file ``docs/TEST_SET.md`` quotes —
  still matches a fresh run, so the documented numbers cannot drift away from the code.

Nothing here re-implements the pipeline: the runner's own ``run()`` is called.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "data" / "test_set_results.json"


def _runner():
    spec = importlib.util.spec_from_file_location("run_test_set", ROOT / "scripts" / "run_test_set.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def offline_run() -> dict:
    return asyncio.run(_runner().run("offline"))


def test_every_case_has_an_expected_direction():
    spec = json.loads((ROOT / "data" / "test_set.json").read_text(encoding="utf-8"))
    assert len(spec["cases"]) >= 8
    for case in spec["cases"]:
        assert case["expect_direction"] in {"support", "refute", "none"}
        assert case["id"] and case["article"] and case["why"]


def test_no_invariant_violations_offline(offline_run: dict) -> None:
    """The promises that must hold whatever the verdicts say."""
    failures = offline_run["invariant_failures"]
    assert not failures, f"invariant violations: {failures}"


def test_documented_directions_still_met(offline_run: dict) -> None:
    summary = offline_run["summary"]
    assert summary["invariants_broken"] == 0
    assert summary["direction_met"] == summary["direction_total"], [
        {
            "id": row["id"],
            "expected": row.get("expected_direction"),
            "got": row.get("direction"),
            "verdict": row.get("verdict"),
        }
        for row in offline_run["rows"]
        if row.get("status") == "measured" and not row.get("direction_match")
    ]


def test_unmeasured_cases_say_why(offline_run: dict) -> None:
    for row in offline_run["rows"]:
        if row.get("status") == "unmeasured":
            assert row["reason"]
            assert "direction_match" not in row or row.get("direction_match") is None


def test_unverifiable_article_yields_no_decisive_verdict(offline_run: dict) -> None:
    """The three cases the corpus cannot speak to must not become confident verdicts."""
    rows = {row["id"]: row for row in offline_run["rows"] if row.get("status") == "measured"}
    for case_id in ("unquoteable-claim", "time-sensitive-figure"):
        assert case_id in rows, f"{case_id} produced no report at all"
        assert rows[case_id]["verdict"] == "INSUFFICIENT EVIDENCE"
        assert rows[case_id]["n_used"] == 0


def test_recorded_results_file_matches_a_fresh_run(offline_run: dict) -> None:
    committed = json.loads(RESULTS.read_text(encoding="utf-8"))
    fresh, stored = offline_run["summary"], committed["summary"]
    assert stored["mode"] == fresh["mode"] == "offline"
    assert stored["cases"] == fresh["cases"]
    assert stored["direction_met"] == fresh["direction_met"]
    assert stored["direction_total"] == fresh["direction_total"]
    assert stored["invariants_broken"] == fresh["invariants_broken"] == 0
    assert stored["unmeasured"] == fresh["unmeasured"]
    # every row's verdict is recorded too, so a per-case regression shows up here
    assert [(r["id"], r.get("verdict")) for r in committed["rows"]] == [
        (r["id"], r.get("verdict")) for r in offline_run["rows"]
    ]


def test_results_file_carries_the_honesty_caveat() -> None:
    committed = json.loads(RESULTS.read_text(encoding="utf-8"))
    assert "not an accuracy figure" in committed["summary"]["note"]
