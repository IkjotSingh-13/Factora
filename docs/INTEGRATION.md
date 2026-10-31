# S2 → S3 → S4 integration (verified 2026-10-31)

Proof that the seam works end-to-end with the lead's real scoring code, and the two
facts both teammates need before they build.

## One call for the orchestrator

```python
from s2_service import retrieve_for_claims, apply_analysis, retrieval_summary

bundles = retrieve_for_claims(claims)            # any claim shape -> ClaimEvidenceBundle[]
bundles = apply_analysis(bundles, analyses_s3)   # S3 fills stance/quote/quote_verified
payload = to_score_request(bundles)              # ScoreRequest-ready

# inside FastAPI, use the non-blocking version
bundles = await aretrieve_for_claims(claims)
```

Everything else — sources, ranking, timeouts, caching, pydantic shapes — stays inside S2.

## SEAM FACT 1: `score_claim` ignores unverified quotes

```python
used = [item for item in evidence if item.quote_verified and item.quote.strip()]
```

So **S2 output alone scores `INSUFFICIENT_EVIDENCE`** — 0 items used, C=0.35, R=0.0, K=0.5.
That is correct and honest: retrieval has no opinion about stance. `apply_analysis()`
is the hand-off that makes evidence count, and it re-checks every quote against the
evidence text before accepting `quote_verified`:

```
real quote      -> stance=REFUTES quote_verified=True
fabricated quote-> stance=SUPPORTS quote_verified=False  quote=""   (dropped)
```

## SEAM FACT 2: a confident verdict needs ≥3 independent domains

`score_claim` shrinks the raw score by `n_independent / 3`. Measured with the lead's
scorer and a labelled S3 test double:

| S3 coverage | verdict | strength | shrink | n_independent |
|---|---|---|---|---|
| 1 verified item (1 domain) | `INSUFFICIENT EVIDENCE` | 0.567 | 0.33 | 1 |
| 2 domains | `MIXED / PARTIALLY SUPPORTED` | 0.665 | 0.67 | 2 |
| 3 domains | `SUPPORTED` / `REFUTED` | 0.757 | 1.00 | 3 |
| no verified quotes at all | `INSUFFICIENT EVIDENCE` | 0.500 | 0.00 | 0 |

**Consequence for Ikjot (S3):** analyse *several* evidence items per claim, one per
distinct domain. A single verified quote per claim caps every verdict at
`INSUFFICIENT EVIDENCE` no matter how good the analysis is. S2 already guarantees
domain diversity whenever sources disagree, so the material is there.

**Consequence for the demo:** the "insufficient evidence" state is not a bug to hide —
it is what the scorer legitimately returns for thin evidence. Show it deliberately.

## Calibration fix (mine)

Raw ranking similarity peaks ~0.6–0.7 for an on-topic source, but the R term expects
a quality weight where strong evidence ≈ 0.9. Without correction, even 3-domain
unanimous evidence scored `MIXED`, never `SUPPORTED`.

`s2_retrieval.calibrate_relevance()` applies a monotone linear scale (default
`relevance_scale=1.45`, clipped at 1.0), so ordering is preserved and nothing is
overstated:

```
similarity 0.20 -> 0.29      similarity 0.50 -> 0.73
similarity 0.35 -> 0.51      similarity 0.62 -> 0.90
```

Override with `RetrievalConfig(relevance_scale=1.0)` for raw similarity.
This is documented as calibration, not as evidence of truth.

## Verified end-to-end output

```
claim_001  Vitamin C prevents the common cold      -> 6 evidence, 3 domains
claim_002  quantum banana protocol (nonsense)      -> 0 evidence
  -> lead's score_claim: INSUFFICIENT EVIDENCE for both (honest, pre-S3)
  -> with 3-domain S3 test double: SUPPORTED / REFUTED (strength 0.757, shrink 1.00)
```

## Outstanding on the lead's side

1. **`requests` is missing from the install chain.** Root `requirements.txt` does
   `-r backend/requirements.txt`, which has fastapi/uvicorn/pydantic/pytest/httpx but
   **not `requests`** — S2 needs it. Add one line to root `requirements.txt`:
   ```
   -r backend/requirements.txt
   -r requirements-s2.txt
   ```
2. `.gitattributes` on main lacks `*.ps1`/`*.bat eol=crlf` (mine has them). Merge or
   copy — otherwise Windows checkouts rewrite line endings repo-wide.
3. `pytest.ini` has `pythonpath = backend` but no `testpaths = tests`; add it so a bare
   `pytest` collects only the test suite.


## Measured latency (hackathon network, Windows)

`scripts/live_smoke.py` on 2026-10-31:

```
claim                                                src ok  fail  ev  dom     ms
The Eiffel Tower is 330 metres tall.                      3     0   5    4   2804
Vitamin C supplementation prevents the common cold        3     0   5    3  10363
The Great Wall of China is visible from space with        2     0   5    2   3476
The VerifyAI team invented the quantum banana prot        2     0   1    1   5569

per-source: wikipedia 100% | openalex 75% | duckduckgo 50% | pubmed 25%
latency: median 4522 ms, max 10363 ms
```

Two problems and their fixes:

1. **One slow source stretched a claim to 10.4 s.** `RetrievalConfig.total_deadline_s = 7.0`
   now abandons stragglers and records them as `timeout` in the audit; fast sources
   still contribute. Worst case per claim is bounded.
2. **Claims ran sequentially: 4.5 s x N.** `retrieve_for_claims(..., parallel_claims=4)`
   runs them concurrently and preserves order. Measured: 4 claims in **3.65 s** instead
   of ~18 s.

Demo budget after the fix: a 4-claim article ≈ 4 s, worst case ≈ 7 s.
The offline snapshot is still faster — use it if the venue wifi misbehaves.
