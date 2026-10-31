# How VerifyAI scores a claim

This document describes what the code in `backend/app/pipeline/s4_scoring.py` actually
computes, so the formula on a slide and the number on screen are the same number.

**One sentence:** we score the *quality and agreement of quote-verified evidence*, never
the probability that a claim is true, and we report direction separately from strength.

---

## 1. Only verified quotes are allowed to count

```python
used = [item for item in evidence if item.quote_verified and item.quote.strip()]
dropped = len(evidence) - len(used)
```

`dropped` is reported as `audit.hallucinated_quotes_dropped`. An item whose quote could not
be located verbatim in the retrieved text is displayed with a
"quote not verified — excluded from the score" chip and contributes nothing. This is the
project's non-negotiable rule, and it lives in the scorer as well as in S3.

`quote_verified` means different things depending on what S2 could fetch, and the UI says
which one it means (`EvidenceItem.quote_scope`):

| `quote_scope` | Meaning in the UI |
| --- | --- |
| `full_text` | "quote verified in fetched page text" |
| `snippet` | "quote verified in the snippet only" |
| `""` | "quote not verified — excluded from the score" |

## 2. The five components

Weights come from the hackathon plan: `S_raw = 0.30·C + 0.30·R + 0.20·A + 0.10·T + 0.10·K`.

| Component | Weight | Computed as |
| --- | --- | --- |
| **C** credibility | 0.30 | relevance-weighted mean of `item.credibility` over `used`. Weights are `max(relevance, 0.05)`. |
| **R** relevance | 0.30 | arithmetic mean of `item.relevance` over `used`. |
| **A** agreement | 0.20 | `agreement × independence` — see §3. |
| **T** timeliness | 0.10 | `exp(-ln2 · age_years / 1.5)` for time-sensitive claims, else `0.85 − 0.15 · min(1, age_years/10)`; age is measured from the **median** publication date. If no date is known, `T = 0.5` and `audit.unknown_freshness = true`. |
| **K** context | 0.10 | `0.7 · mean(context_score) + 0.3 · (1 if no item has a context_gap else 0)`. |

With **no** usable evidence the components fall back to `C = 0.35, R = 0, K = 0.5` — a
deliberately unimpressive set that lands in INSUFFICIENT EVIDENCE rather than a fake zero.

## 3. Direction is separate from strength

For each **independent domain** (the strongest item per normalized domain, by
`relevance × credibility`):

```python
support_weight = Σ relevance × credibility   over SUPPORTS items
refute_weight  = Σ relevance × credibility   over REFUTES items

stance_score = (support_weight − refute_weight) / (support_weight + refute_weight)   # −1 … +1
agreement    = |support_weight − refute_weight| / (support_weight + refute_weight)   #  0 …  1
independence = 1 − exp(−n_independent / 2)
A            = agreement × independence
```

- `stance_score` is the **direction**: −1 refutes, +1 supports, 0 means the evidence does not
  point either way (no directional evidence, or an exact balance).
- `agreement` is how lopsided it is, and `independence` caps the confidence that can be
  claimed from a single domain. One domain agreeing with itself is not corroboration: with
  `n_independent = 1`, `independence ≈ 0.39`.

The UI draws these as two separate gauges ("Stance direction" and "Evidence strength") so a
strongly one-sided-but-thin result cannot be misread as "probably true".

## 4. Shrinkage: thin evidence is pushed toward "we don't know"

```python
score_raw        = 0.30·C + 0.30·R + 0.20·A + 0.10·T + 0.10·K
shrinkage_factor = min(1.0, n_independent / 3.0)
evidence_strength = 0.5 + (score_raw − 0.5) · shrinkage_factor      # clipped to [0, 1]
```

With one independent domain, `evidence_strength` is pulled halfway to 0.5; with two,
two-thirds of the way; only three or more leave `score_raw` intact. 0.5 is the "no
information" midpoint, so a thin evidence set reads as *near the middle*, not as a high
score with an asterisk. `score_raw`, `shrinkage_factor` and `evidence_strength` are all in
the response so anyone can re-derive the arithmetic.

## 5. Verdict thresholds

Evaluated in this order, first match wins:

| Condition | Verdict |
| --- | --- |
| `n_independent ≥ 2` and `directional_total > 0` and `agreement < 0.25` | CONFLICTING EVIDENCE |
| `n_independent < 2` or `evidence_strength < 0.45` | INSUFFICIENT EVIDENCE |
| `evidence_strength ≥ 0.70` and `support_weight > refute_weight` | SUPPORTED |
| `evidence_strength ≥ 0.65` and `refute_weight > support_weight` | REFUTED |
| otherwise | MIXED / PARTIALLY SUPPORTED |

Notes on the choices:

- Two-sided disagreement is resolved **before** the low-score fallback, so real conflict is
  labelled "conflicting", not "insufficient".
- REFUTED has a lower threshold than SUPPORTED (0.65 vs 0.70): refusing a widely repeated
  false claim is the point of the tool, and the cost of "we could not refute this" is a
  weaker statement, not a wrong one.
- `conflicts[]` lists every source taking either side whenever both sides are present, and
  the claim card footer says so in words.

## 6. Worked example (real run, `mode=offline`, `as_of=2026-10-31`)

Input: `"The Eiffel Tower is 330 metres tall."` — 4 items retrieved, 3 quote-verified, 1
dropped for an unverifiable quote, 3 independent domains.

```
C = 0.1      R = 0.9558   A = 0.7769   T = 0.8233   K = 0.811
0.30·C = 0.03   0.30·R = 0.2868   0.20·A = 0.1554   0.10·T = 0.0823   0.10·K = 0.0811
score_raw = 0.6356      shrinkage = 1.0      evidence_strength = 0.6356
stance_score = +1.0     n_independent = 3    verdict = MIXED / PARTIALLY SUPPORTED
```

The direction is unambiguous (+1.0: every independent domain that took a side supports), but
`C` is 0.10 because **every source in the local demo corpus is tagged `source_kind=mock` and
is deliberately scored at 0.10**. That is why the verdict is MIXED and not SUPPORTED: the
corpus is a mechanism demo, not real evidence. Run the same claim with `mode=snapshot` or
`mode=live` and `C` becomes 0.35–0.85 depending on which sources answered.

## 7. What the score is not

- Not a probability of truth. The disclaimer text says this on every response and in the UI
  footer: *"Evidence-weighted assessment, not a guaranteed truth verdict."*
- Not a model's opinion. `s3_audit.analyzer` records `lexical` or `llm` for the run, and an
  LLM answer that fails the quote guard is re-labelled
  `"lexical (after the LLM answer was rejected)"` at the item level.
- Not a substitute for reading the source. Every evidence card carries the link and the
  exact quote it was scored on.
