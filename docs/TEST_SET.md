# QA test set — measured results

Plan §3 asks for "a small test set with known supported, refuted, and misleading /
context-sensitive claims" and for **actual recorded results, not an accuracy figure that was
not measured**. This file is the record. Every number below was printed by the runner; none
was rounded up or hand-picked.

```
inputs   data/test_set.json           10 labelled cases (id, claim, article, label,
                                      expect_direction, optional expect_verdict, why)
runner   scripts/run_test_set.py      runs the real pipeline, no mocks of its own
output   data/test_set_results.json   per-case actual outcome + aggregate counts
```

```bash
python scripts/run_test_set.py                    # offline demo corpus (no network)
python scripts/run_test_set.py --mode snapshot     # captured real sources
python scripts/run_test_set.py --fail-on-unmet     # exit 1 if a row misses
```

## What is being checked

Two things, deliberately kept apart:

1. **Direction.** The sign of the headline `stance_score` is compared with `expect_direction`
   (`support` / `refute` / `none`), with a ±0.05 dead band. The scorer is *not* asked whether
   a claim is true; it is asked which way the retrieved, quote-verified evidence points. That
   is the only question this pipeline can be measured on without a human label set.
2. **Invariants**, on every case, regardless of verdict:
   - no unverified quote reaches the report (`quote_verified` false → must not be shown);
   - every displayed quote appears in the text the guard checked it against;
   - a `quote_verified: true` item never carries an empty quote;
   - `n_used ≤ n_retrieved`;
   - no SUPPORTED/REFUTED verdict on fewer than 2 independent domains.

A case whose extracted claims never match the labelled claim (e.g. S1 classified it as an
opinion and the factual-only filter removed it) is recorded as **unmeasured**, not as a pass.

## Results — `mode=offline`, run 2026-10-31T21:36:25+00:00

| case | label | expected | got | verdict | ev | used |
| --- | --- | --- | --- | --- | --- | --- |
| `eiffel-height` | supported | support | support ✓ | MIXED / PARTIALLY SUPPORTED | 4 | 3 |
| `eiffel-wrong-height` | refuted | refute | refute ✓ | MIXED / PARTIALLY SUPPORTED | 4 | 2 |
| `great-wall-visible` | misleading | refute | refute ✓ | MIXED / PARTIALLY SUPPORTED | 3 | 3 |
| `vitamin-c-prevents` | misleading | refute | refute ✓ | CONFLICTING EVIDENCE | 3 | 3 |
| `lightning-myth` | misleading | refute | refute ✓ | MIXED / PARTIALLY SUPPORTED | 3 | 3 |
| `empire-state-strikes` | supported | support | support ✓ | INSUFFICIENT EVIDENCE | 2 | 2 |
| `fabricated-consortium` | insufficient | — | — | **UNMEASURED**: no claim report matched the labelled claim | | |
| `unquoteable-claim` | insufficient | none | none ✓ | INSUFFICIENT EVIDENCE | 0 | 0 |
| `quote-guard-must-hold` | supported | support | support ✓ | MIXED / PARTIALLY SUPPORTED | 2 | 2 |
| `time-sensitive-figure` | context | none | none ✓ | INSUFFICIENT EVIDENCE | 0 | 0 |

**direction met 9/9 · invariant violations 0 · unmeasured 1**

(On a machine where the real `en_core_web_sm` pipeline is installed — i.e. after
`scripts/bootstrap.sh` — this reads **8/8 with 2 unmeasured**: the model declines to turn
the opinion sentence in `unquoteable-claim` into a factual claim, so the runner records the
case as unmeasured with that reason instead of scoring nothing. Both are the intended
behaviour, `tests/test_test_set.py` accepts either, and neither is a pass awarded by
accident.)

The `fabricated-consortium` case is the interesting failure and it is a *good* one: the
article's checkable-sounding sentences were classified as prediction/opinion, so the only
claim S1 kept was *"The announcement came from an account with eleven followers."* — which
does not match the labelled claim. The runner refuses to score an unrelated sentence and call
it a result. That is exactly the behaviour we want from a verification tool, and it is
recorded as unmeasured instead of quietly counted as a pass.

## Results — `mode=snapshot` (captured real sources, `demo_backup=true`)

**direction met 5/9 · invariant violations 0 · unmeasured 1**

| case | expected | got | verdict | used | dropped quotes |
| --- | --- | --- | --- | --- | --- |
| `eiffel-height` | support | **refute ✗** | INSUFFICIENT EVIDENCE | 2 | 3 |
| `eiffel-wrong-height` | refute | refute ✓ | INSUFFICIENT EVIDENCE | 2 | 3 |
| `great-wall-visible` | refute | refute ✓ | INSUFFICIENT EVIDENCE | 3 | 2 |
| `vitamin-c-prevents` | refute | **support ✗** | MIXED / PARTIALLY SUPPORTED | 4 | 1 |
| `lightning-myth` | refute | **none ✗** | INSUFFICIENT EVIDENCE | 0 | 0 |
| `empire-state-strikes` | support | **none ✗** | INSUFFICIENT EVIDENCE | 0 | 0 |
| `quote-guard-must-hold` | support | support ✓ | MIXED / PARTIALLY SUPPORTED | 2 | 0 |
| `unquoteable-claim` | none | none ✓ | INSUFFICIENT EVIDENCE | 0 | 0 |
| `time-sensitive-figure` | none | none ✓ | INSUFFICIENT EVIDENCE | 0 | 0 |

Why the misses happen, from the data rather than from excuses:

- `eiffel-height` — of 5 captured items, 3 had quotes the guard could not locate and were
  dropped; the 2 survivors were a comparison sentence in another landmark's article, which
  genuinely contradicts the number in context. Direction is therefore wrong at snippet scope.
  Two sources that mention a height is *not* 1,083 ft outweigh one snippet that says it is.
- `vitamin-c-prevents` — the captured medical pages discuss vitamin C for cold *prevention*
  studies; at `align ≥ 0.5` the lexical analyser judged a sentence strong enough to count as
  SUPPORTS on 4 items, and REFUTES on 1 (`dropped=1`). A misleading claim picked up support.
  This is the failure mode of a lexical stance analyser, and it is why the verdict stayed at
  MIXED rather than SUPPORTED and why `s3_audit.analyzer=lexical` is on screen.
- `lightning-myth`, `empire-state-strikes` — the snapshot has nothing relevant for these
  domains at all (`used=0`), so INSUFFICIENT EVIDENCE is the correct answer, not a miss on
  the claim; the label said `refute`/`support` and the pipeline declined to guess.

## Reading these numbers

- 9/9 offline is **not** "90% accurate". It is: on a 10-case set built to exercise the
  mechanism, with an evidence corpus we wrote ourselves, the sign of the direction score
  matched the label on all 9 cases that could be scored.
- 5/9 on real captured sources is the honest counterpart: retrieval noise, snippet-only
  scope, and a lexical stance analyser cost 4 cases. Two of those four (the `used=0` rows)
  are the tool declining rather than being wrong.
- 0 invariant violations across both runs means the safety property held on every case:
  **nothing was displayed as a quote that the retrieved text does not contain, and nothing
  decisive was concluded from one domain.** That is the property the demo promises.
- `n=10` cases is a demo-scale set. It is enough to catch regressions and to be honest about
  what we have and have not shown. It is not enough to publish an accuracy number, so we
  are not publishing one.

## Reproducing

`data/test_set_results.json` holds the run that produced the offline table above
(`summary.direction_met`, `summary.invariants_broken`, `summary.unmeasured`, plus every row).
`scripts/run_test_set.py` rewrites it on every run, and `tests/test_test_set.py` re-runs the
set and fails if the committed file no longer matches — so the numbers in this document
cannot silently drift away from the code. Add a case to `data/test_set.json` any time QA
finds something the pipeline got wrong; the runner will record what it does now.
