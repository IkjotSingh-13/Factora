# Mansimran — Role Analysis & Branch Workflow

**Team:** VerifyAI (Problem 21 — claim extraction, evidence retrieval, transparent assessment)
**Your role:** Retrieval & Evidence Ranking — owner of stage **S2**
**Your files:** `sources.py`, `s2_retrieval.py`, `ranking.py`, `cache.py`, `http_util.py`, `tests/test_s2.py`, `scripts/live_smoke.py`
**You do NOT touch:** claim extraction (Ikjot, S1), stance analysis (Ikjot, S3), scoring formula + API (Team Lead, S4/S5), UI (Taranpreet).

---

## 1. What your role actually is (and is not)

Read the plan again — the pipeline is `S1 claims → S2 retrieval → S3 stance → S4 score → S5 report → UI`.

You are the **pipe between the internet and the analysts**. Three sentences that define your job:

1. **Turn a claim into ranked, normalized, citable evidence.** Nothing else leaves your module.
2. **Never be the reason the demo dies.** Everything external fails soft: timeout → log → continue.
3. **Be the honest broker.** If a claim has no real coverage, retrieval must say *so* — you are the first place where "insufficient evidence" becomes visible.

What you are **not** doing: deciding whether a claim is true. Stance is Ikjot's. Score is the lead's. If you catch yourself writing `verdict = ...`, stop.

### Your definition of done (from the plan, restated as testable checks)

| Requirement | Status in this repo | Where |
|---|---|---|
| Ranked list of normalized evidence items | ✅ done | `s2_retrieval.RetrievalEngine.search_claim` |
| Every item carries url, domain, title, snippet, date, source_kind, relevance | ✅ done | `contracts.Evidence` |
| Two reliable sources, third only if stable | ✅ Wikipedia + OpenAlex + PubMed (all keyless, live-verified) | `sources.default_adapters` |
| Timeouts, retries, caching; failed source ≠ failed verification | ✅ done, tested | `http_util.py`, `cache.py`, `tests/test_s2.py::test_failed_source_does_not_fail_run` |
| Literal keyword query always included | ✅ done (plus an entity/number variant) | `s2_retrieval.build_queries` |
| Semantic + BM25/RRF ranking, dedupe, domain cap | ✅ done, no model download needed | `ranking.py` |
| Audit: which sources were queried, how many results, which were used | ✅ done | `contracts.RetrievalAudit` |

---

## 2. How your module plugs into the team

```python
# Team Lead's /api/verify handler — this is the entire integration surface
from s2_retrieval import RetrievalEngine, RetrievalConfig

engine = RetrievalEngine(RetrievalConfig(top_k=6, mock=True))   # mock=True → local corpus fallback

for claim in claims:                       # claims come from Ikjot's s1_claims.py
    rr = engine.search_claim(claim.text, claim_id=claim.id)
    evidence = rr.evidence                 # list[contracts.Evidence] -> feed Ikjot's s3_analysis.py
    audit_slice = rr.audit.model_dump()    # -> merge into the report's audit footer
```

Handoff rules you must not break:

- Field names in `contracts.py` are frozen. If something must change, the **lead** changes it and everyone re-syncs.
- Your audit fills these report fields: sources queried / failed, failed source names, candidates retrieved, duplicates dropped, distinct domains, cache hits, duration.
- `retrieval.notes` is written for humans — Taranpreet can print it in the audit footer verbatim.
- Relevance is 0..1 and means **"how well this text matches the claim"**, never "how true". Say this out loud in the demo; judges test for exactly this confusion.

### Signals you hand to scoring (do not compute score yourself)

| Term | What you provide |
|---|---|
| **C** source credibility | `Evidence.source_kind` + `sources.CREDIBILITY_HINTS` starter table |
| **R** evidence relevance | `Evidence.relevance` (with `semantic`/`bm25`/`rrf`/`coverage` breakdown stashed in `candidate.raw.extra["scores"]`) |
| **A** independent agreement | `audit.distinct_domains`, per-domain spread, `source_kind` mix |
| **T** freshness | `Evidence.published_at` (ISO date or `None`) — never invent it |
| **K** contextual consistency | `audit.coverage_by_evidence`, `audit.mean_coverage` (signals when sources talk *around* the claim) |

---

## 3. What is already built and verified

Live run on this sandbox (real network), 4 claims, end-to-end:

```
claim                                                src ok  fail  ev  dom     ms
The Eiffel Tower is 330 metres tall.                      2     0   5    4    743
Vitamin C supplementation prevents the common cold        3     0   5    3   1997
The Great Wall of China is visible from space with        2     0   5    2   1170
The VerifyAI team invented the quantum banana prot        1     0   0    0   1095   <- thin evidence, correctly
```

Per-source reliability on that run: wikipedia 100%, openalex 75%, pubmed 25%, duckduckgo 0%
(DDG's HTML endpoint serves anti-bot pages to datacenter IPs — it is wired up but treated as
best-effort and never allowed to fail the run).

Measured latency: **median ~1.1 s, max ~2.0 s** for the whole claim (all sources in parallel).

Correct behaviour worth demoing:

- nonsense claim → **0 evidence + audit note**, so the lead's "insufficient evidence" state is real, not cosmetic;
- myth claim ("Great Wall visible from space") → top hit is academic evidence *contradicting* it — evidence strength and direction are separate, which is your argument for the score design;
- domain cap + diversity guarantee → no single publisher can dominate the list.

Run it yourself:

```bash
python s2_retrieval.py "Vitamin C supplementation prevents the common cold in most adults" --top-k 6
python s2_retrieval.py --offline "vitamin C common cold"     # no network, local corpus only
python scripts/live_smoke.py                                 # measured source reliability report
pytest -q                                                    # 27 tests, no network
```

---

## 4. Your next 3 moves (in this order)

1. **Hour 1 checkpoint:** push this branch, send the lead the `contracts.py` block and the one-line integration snippet above. Ask them to confirm `Evidence` fields before they build the API around them.
2. **Warm the demo cache** once the lead's `/api/verify` runs end-to-end: `engine.warm([...demo claims...])`. Then your module answers from disk even with no wifi. Label cached output as a demo backup — the plan is explicit about that.
3. **Add a third source only if stable.** Ask a teammate for a free **Tavily** key (already implemented in `sources.TavilySource`, activates from `TAVILY_API_KEY`) or a **Google Fact Check** key (highest value: real fact-check verdicts in the evidence list). Both are code-complete — you only need keys in `.env`.

Stretch, only after the above and only if the demo is safe: cross-encoder rerank via `rank_candidates(..., rerank_fn=...)` and `ranking.SentenceTransformerBackend`.

---

## 5. Git: your own branch, without merge pain

Branch name for you: **`mansimran/retrieval_and_evidence`** (separate branch per member — never commit to `main`).

```bash
# one-time
git clone <repo-url> verifyai && cd verifyai
git switch -c mansimran/retrieval_and_evidence
git push -u origin mansimran/retrieval_and_evidence

# the loop, every 1-2 hours (plan says integrate every 2-3 h)
./scripts/pre_pr_check.sh          # rebase on main + tests + contract-drift diff
git add -A
git commit -m "feat(retrieval): add pubmed source + fail-soft engine"
git push origin mansimran/retrieval_and_evidence
```

Since each member owns a branch, the merge pain is not file conflicts — it is **contract drift**:
`contracts.py` will exist on several branches. `pre_pr_check.sh` diffs yours against `main` and
tells you when they diverge, which is the moment to message the lead instead of "fixing" it yourself.

Conventional-commit prefixes the whole team should use (it makes the demo's "who built what" slide trivial):
`feat(retrieval):` · `fix(retrieval):` · `test(retrieval):` · `docs(retrieval):` · `chore:`

Rules that keep the hackathon from burning an hour on conflicts:

- **One owner per file.** `sources.py`, `s2_retrieval.py`, `ranking.py` are yours; nobody else edits them.
- `contracts.py` is the lead's. You *read* it, you never edit it. Need a field? Ask; they change it once for everyone.
- `requirements.txt` conflicts are the #1 hackathon time sink: append your own lines at the bottom, never re-sort.
- Before every PR/rebase: `./scripts/pre_pr_check.sh` must end green (this runs `pytest -q`).
- Open a PR to `main` at the 3 h, 6 h and 12 h checkpoints with a 3-line description: what changed, how to run, what is not done. Small PRs = the lead can merge without reading everything.
- **After hour 12 (feature freeze):** your branch gets `fix(...)` commits only, and nothing risky.

Helper script: `scripts/git_branch_setup.sh` (creates/refreshes your branch and prints the push commands).

---

## 6. Demo lines you own (rehearsal script, 30 s)

> "Each claim goes to three independent sources in parallel — Wikipedia, OpenAlex and PubMed — with hard timeouts. If one is down, we log it in the audit trail and carry on.
> Everything you see is ranked by relevance, deduplicated, and capped per publisher, so a single site can't drown out the others.
> And notice the last claim: nothing on the web actually covers it, so we return **no evidence** and the report says 'insufficient evidence'. We would rather say that than invent a verdict."

Then show the audit line: sources queried, one failed source, duplicates dropped, distinct domains, latency.
