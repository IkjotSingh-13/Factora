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
    """The cases the corpus cannot speak to must not become confident verdicts.

    Two outcomes are correct, and which one you get is up to the environment: with the
    real spaCy model installed S1 declines to turn an opinion sentence into a claim at
    all, so the row is recorded ``unmeasured`` with a stated reason; on the blank
    fallback pipeline the sentence survives extraction and the scorer answers
    ``INSUFFICIENT EVIDENCE``. A decisive verdict, or a silent pass, is the failure.
    """
    rows = {row["id"]: row for row in offline_run["rows"]}
    decisive = {"SUPPORTED", "REFUTED"}
    for case_id in ("unquoteable-claim", "time-sensitive-figure"):
        assert case_id in rows, f"{case_id} produced no row at all"
        row = rows[case_id]
        if row.get("status") != "measured":
            assert row["status"] == "unmeasured", f"{case_id}: {row}"
            assert row.get("reason"), "an unmeasured row has to say why"
            assert not decisive & set(row.get("verdicts_produced") or []), (
                f"{case_id} was skipped, yet a claim from it got a decisive verdict"
            )
            continue
        assert row["verdict"] == "INSUFFICIENT EVIDENCE"
        assert row["n_used"] == 0


def test_recorded_results_file_matches_a_fresh_run(offline_run: dict) -> None:
    """The committed results reproduce, within what the environment is allowed to change.

    Every verdict that reached scoring has to match exactly — that is the point of the
    comparison. Which cases *reach* scoring depends on whether ``en_core_web_sm`` is
    installed, so those counts are checked as invariants rather than as constants; a
    run that quietly stopped measuring most of the set still fails here.
    """
    committed = json.loads(RESULTS.read_text(encoding="utf-8"))
    fresh, stored = offline_run["summary"], committed["summary"]
    for label, summary in (("recorded file", stored), ("fresh run", fresh)):
        assert summary["mode"] == "offline", f"{label} is not the offline run"
        assert summary["invariants_broken"] == 0, f"{label} broke a scoring invariant"
        assert summary["measured"] >= 8, f"{label} measured too little of the set"
        assert (
            summary["direction_met"] == summary["direction_total"] > 0
        ), f"{label}: {summary['direction_met']}/{summary['direction_total']} directions met"
        assert (
            summary["verdict_met"] == summary["verdict_rows_expected"]
        ), f"{label}: an expected verdict was missed"
        assert (
            summary["measured"] + summary["unmeasured"] + summary["errored"]
            == summary["cases"]
        ), f"{label}: rows unaccounted for"
    assert stored["cases"] == fresh["cases"]

    committed_rows = {row["id"]: row for row in committed["rows"]}
    fresh_rows = {row["id"]: row for row in offline_run["rows"]}
    assert set(committed_rows) == set(fresh_rows), "the case set itself moved"

    # Compare what both runs scored. Which cases *are* scored is up to the environment
    # (see above), so a case measured on one machine and recorded unmeasured on the
    # other is not a regression — a case whose verdict moved is.
    moved = []
    for case_id, before in committed_rows.items():
        after = fresh_rows[case_id]
        if before.get("status") != "measured" or after.get("status") != "measured":
            continue
        for field in ("verdict", "direction", "direction_match", "n_used"):
            if before.get(field) != after.get(field):
                moved.append((case_id, field, before.get(field), after.get(field)))
    assert not moved, f"recorded results no longer reproduce: {moved}"
    # and the two runs have to have scored most of the same cases, or this comparison
    # would be quietly vacuous
    both = [
        cid
        for cid, row in committed_rows.items()
        if row.get("status") == "measured" and fresh_rows[cid].get("status") == "measured"
    ]
    assert len(both) >= 8, f"only {len(both)} cases are comparable between the two runs"
    for row in offline_run["rows"]:
        if row.get("status") == "unmeasured":
            assert row.get("reason"), f"{row['id']} is unmeasured without a reason"



def test_results_file_carries_the_honesty_caveat() -> None:
    committed = json.loads(RESULTS.read_text(encoding="utf-8"))
    assert "not an accuracy figure" in committed["summary"]["note"]
