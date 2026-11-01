# Contract check: S2 output vs the lead's models

Verified against `IkjotSingh-13/Factora`.

## What the plan said vs what the repo actually has

The 24h plan describes a `claim_id, url, domain, title, text_or_snippet, published_at,
source_kind, relevance` object. The repo's canonical contract is different — it lives at

```
backend/app/models.py   (branch main, commit 9f6e7fb)
```

and defines `EvidenceItem`, `ClaimInput`, `ClaimEvidenceBundle`, `ScoreRequest`,
`ClaimReport`, `AuditSummary`. **That file is the contract.** Anything else is a sketch.

## The three real gaps (found 2026-10-31)

| Our `contracts.Evidence` | Their `app.models.EvidenceItem` | Consequence if unhandled |
|---|---|---|
| no `id` | `id: str` **required**, `min_length=1` | every item rejected by pydantic |
| `text_or_snippet` | `snippet` (+ `full_text`) | text never reaches the UI |
| `published_at: str "2025-10-01"` | `published_at: datetime.date` | validation error / T term broken |

Two more fields they need that we never produced: `credibility` (feeds the C term —
they default it to 0.35) and `context_score` (feeds K — default 0.7).

## Resolution: `contract_adapter.py`

One module, no changes to retrieval, no renaming in their file:

```python
from contract_adapter import to_claim_bundle

rr = engine.search_claim(claim.text, claim_id=claim.id)
bundle = to_claim_bundle(
    claim_id=claim.id, claim_text=claim.text,
    evidence=rr.evidence, audit=rr.audit,
)
# -> {"claim": {...ClaimInput...}, "evidence": [...EvidenceItem...]}
ScoreRequest(claims=[bundle])        # accepted
```

Mappings, all deliberate:

| Field | Source | Note |
|---|---|---|
| `id` | `ev_<sha1(claim_id+url)[:10]>` | deterministic → stable UI keys, safe dedupe |
| `snippet` | our `text_or_snippet` | S2 gets API snippets, not scraped full text |
| `full_text` | `""` | honest: we did not fetch the whole page |
| `published_at` | ISO string → `date` | year-only values become `YYYY-01-01`; `None` stays `None` — we never invent a date |
| `credibility` | `sources.CREDIBILITY_HINTS[source_kind]` | fact_check .90, academic .85, encyclopedia .80, news .65, web .45, mock .10 |
| `context_score` | retrieval coverage proxy | share of the claim's distinctive terms present in the evidence; scoring may override |
| `context_gap` | `coverage < 0.5` | |
| `stance`, `quote`, `quote_verified`, `rationale` | neutral defaults | **S3 (Ikjot) owns these. Retrieval does not judge.** |

## Claims: three shapes, one normaliser

| Stage | Shape |
|---|---|
| Plan sketch (`contracts.Claim`) | `id, text, type, article_quote, time_sensitive, entities` |
| Lead (`app.models.ClaimInput`) | `id, text, type, article_quote, time_sensitive` — **no `entities`** |
| Ikjot (`s1_claims.Claim`) | `id, text, entities, claim_type` — **`claim_type`, not `type`** |

`contract_adapter.to_claim_input()` accepts any of them. Two rules it enforces:

* **`claim_type` -> `type`.** Ikjot's name wins on their side, the lead's name wins on
  the wire; nothing gets renamed in anyone's file.
* **`entities` are not emitted.** `ClaimInput` has no field for them, so silently
  passing them through would lose data depending on pydantic's `extra` setting.
  Instead they are used where they actually help: **building the retrieval query**,
  and they are available for the report via `extract_entities()` /
  `to_claim_bundle(..., include_extras=True)`.

### Calling it with Ikjot's object, end to end

```python
from contract_adapter import to_claim_bundle, extract_entities

claim = Claim(id="claim_001",
              text="India is the world's largest solar energy producer",
              entities=["India"],
              claim_type="factual")          # Ikjot's shape, unchanged

rr = engine.search_claim(claim.text, claim_id=claim.id,
                         entities=extract_entities(claim))   # better query, same contract

bundle = to_claim_bundle(claim, evidence=rr.evidence, audit=rr.audit)
ScoreRequest(claims=[bundle])
```

What entities change: the query. For
`"Apple acquired Beats for 3 billion dollars in 2014"` with
`entities=["Apple", "Beats"]` the engine adds
`"Apple Beats acquired 3 billion dollars 2014"`. They never touch ranking or stance.

> **Ask the lead:** should `entities` become a real field on `ClaimInput`? It is
> useful for the UI ("about: India") and for grouping claims by entity. One line in
> their model, and I will emit it. Until then it travels in `extras`.

## Verify it yourself

```powershell
.venv\Scripts\python.exe scripts\contract_check.py
```

```
[ ok ] accepted by ClaimEvidenceBundle (3 items)
[ ok ] accepted by ScoreRequest
Verdict: S2 output satisfies the lead's contract.
```

`tests/vendor/app_models_snapshot.py` is a verbatim copy of their models, so the test
suite fails the moment their schema changes. Refresh it after any change on main:

```powershell
git show origin/main:backend/app/models.py > tests/vendor/app_models_snapshot.py
```

**Why this is worth the effort:** four branches, four ideas of the schema. A silent
mismatch discovered at hour 20 costs the demo; discovered at hour 6 it costs ten minutes.

## Resolved in the final integration (2026-10-31)

The pytest problem above was fixed and is no longer a gotcha: the root `pytest.ini` now has
`testpaths = tests` + `pythonpath = backend`, and `requirements.txt` pulls in
`backend/requirements.txt`, so from a fresh venv with dependencies installed

```bash
python -m pytest -q          # 423 tests, exit 0, from the repo root
```

runs everyone's suite in one go. No member needs to run a per-file command any more.

Three contract points got settled while wiring `/api/verify`. They are recorded here because
the rule is "no silent renames":

| Plan-contract field | Final state | Why |
| --- | --- | --- |
| `Evidence.text_or_snippet` | `snippet` + `full_text` on `EvidenceItem` | unchanged from the mapping table above; `quote_scope` was **added** so "verified in page text" and "verified in snippet" are distinguishable on the wire and in the UI |
| `Claim.entities` | not on `ClaimInput`; carried in `grounding.claims[].entities` | the scoring schema stays frozen, entities drive the retrieval query, and the claim card renders them from the audit payload. `scripts/ui_render_check.mjs` asserts that join against a crafted payload so the field cannot quietly stop appearing |
| `Claim report.stance_direction` | `stance_score` (signed −1…+1) | the value is a magnitude, not an enum, and the verdict field carries the categorical direction; `docs/SCORING.md` shows both |

Failed-source reporting is now also visible, not just present in the JSON: the audit footer
renders `sources that failed` with the names and counts them against `sources_queried`, because
"which sources did not answer?" is the first question a reviewer asks of a weak result.
