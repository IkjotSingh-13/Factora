# PR + handoff pack (S2 retrieval, Mansimran)

**Your branch:** `mansimran/retrieval_and_evidence` (1 commit ahead, 3 behind `main`).

Everyone has their own branch, so you will not fight anyone for files. What you *can* lose is the
**shared contract**: four branches, four copies of `contracts.py`, and whichever set of field names
lands last quietly breaks the other three. That is the only thing in this document worth panicking about,
and step 1 catches it.

Do these in order. Total time: ~15 minutes.

> Shortcut: `./scripts/pre_pr_check.sh` does steps 1–3 below in one go (rebase + tests + schema diff).

---

## 0. Rebase onto main first (do not merge)

```bash
git switch mansimran/retrieval_and_evidence    # if you are not already on it
git fetch origin
git rev-list --left-right --count origin/main...HEAD   # "3  1" = 3 behind, 1 ahead
git rebase origin/main
pytest -q                                       # must still be 27 passed
git push origin mansimran/retrieval_and_evidence --force-with-lease
```

You are 3 behind with 1 commit — a rebase here is seconds. Do it now, not the night before the demo.

> `--force-with-lease`, never plain `--force`: it refuses to overwrite anyone else's push to your branch.

### Expected conflicts and which side wins

| File | Likely cause | Resolution |
|---|---|---|
| `contracts.py` | the lead defined the shared schema on `main` | **keep main's version**, then adapt your imports |
| `requirements.txt` | everyone appends | keep **both** line sets, do not re-sort |
| `README.md` | lead wrote the project README | keep **main's**, move your module README to `docs/S2_README.md` |
| `sources.py`, `s2_retrieval.py`, `ranking.py`, `cache.py`, `pytest.ini`, `tests/` | nobody else should touch these | keep **yours** |

If a rebase ever gets messy on a branch nobody else uses, the nuclear option is legal and fast:
`git rebase --abort`, then `git reset --hard origin/main` and re-apply your module files
(they are all in `sources.py`, `s2_retrieval.py`, `ranking.py`, `cache.py`, `http_util.py`, `tests/`,
`scripts/`, `data/mock_corpus.json`). Twenty minutes, zero risk of a half-merged state.

**Footgun:** during a `git rebase`, the sides are inverted relative to a merge.
`--ours` = **upstream/main**, `--theirs` = **your commit**. So to keep main's `contracts.py`:

```bash
git checkout --ours contracts.py
git add contracts.py
git rebase --continue
```

If in doubt, abort safely with `git rebase --abort` and nothing is lost.

---

## 1. Check the schema actually matches (2 minutes, highest value)

A silent field-name mismatch is the only bug that can cost the team a real chunk of the night.
After the rebase:

```bash
python -c "from contracts import Evidence; print(list(Evidence.model_fields))"

# and, if contracts.py also exists on main, compare rather than assume:
git diff origin/main:contracts.py contracts.py
```

Expected:

```
['claim_id', 'url', 'domain', 'title', 'text_or_snippet', 'published_at', 'source_kind', 'relevance']
```

If main's version differs, **do not rename anything yourself.** Post the diff to the team and let the
lead decide — they own that file and everyone else's code reads it.

---

## 2. Open the PR with this description (copy-paste)

**Title**
```
feat(retrieval): S2 retrieval + evidence ranking with fail-soft sources and audit trail
```

**Body**
```markdown
## What
Stage S2 of the pipeline: claim text in → ranked, de-duplicated, normalized evidence out.
Implements the frozen `Evidence` contract plus a `RetrievalAudit` record for the report footer.

## Sources (all keyless, live-tested)
- Wikipedia (encyclopedia) — MediaWiki search + intro extracts
- OpenAlex (academic) — works + reconstructed abstracts + dates
- PubMed (academic) — E-utilities, PMID links
- Tavily / Google Fact Check — code-complete, auto-enable when `TAVILY_API_KEY` /
  `GOOGLE_FACTCHECK_API_KEY` is set in `.env`
- DuckDuckGo (web) — best-effort; returns [] when it serves an anti-bot page
- Local corpus (mock) — offline demo fallback, output tagged `mock` / `demo_backup`

## Why it cannot break the demo
Every external call has a hard timeout, bounded retries and a TTL + disk cache.
A failed source becomes an audit row and the run continues — tested in
`tests/test_s2.py::test_failed_source_does_not_fail_run`.
`RetrievalConfig(offline=True)` needs no network at all.

## How to run
pip install -r requirements.txt
python s2_retrieval.py "Vitamin C supplementation prevents the common cold in most adults" --top-k 6
python scripts/live_smoke.py      # measured source reliability + latency
pytest -q                         # 27 tests, no network

## Integration for the API (2 lines)
from s2_retrieval import RetrievalEngine, RetrievalConfig
engine = RetrievalEngine(RetrievalConfig(top_k=6, mock=True))
rr = engine.search_claim(claim.text, claim_id=claim.id)
# rr.evidence -> S3 stance analysis;  rr.audit -> report audit footer;  rr.has_evidence -> insufficient-evidence state

## Signals for the score (I do not compute the score)
C: Evidence.source_kind + sources.CREDIBILITY_HINTS starter table
R: Evidence.relevance (semantic / bm25 / rrf / coverage breakdown kept in the audit)
A: audit.distinct_domains, per-publisher spread, source_kind mix
T: Evidence.published_at (ISO date or None — never invented)
K: audit.coverage_by_evidence, audit.mean_coverage

## Measured on this build (real network)
4-claim smoke set: median 1.1 s, max 2.0 s end-to-end, sources in parallel.
Wikipedia 100% of runs returned results, OpenAlex 75%, PubMed 25%, DDG 0% (datacenter IPs).
Nonsense claim → 0 evidence + audit note → the "insufficient evidence" state is real, not cosmetic.

## Not done (deliberately)
Cross-encoder reranking, embeddings, vector store, full-text fetching of every result.
All are on the plan's cut list.

## Reviewer note
`sources.py`, `s2_retrieval.py`, `ranking.py`, `cache.py`, `http_util.py`, `tests/` are S2-owned.
`contracts.py` is the lead's — I adapt to it, I don't change it.
```

---

## 3. Team chat message (copy-paste, shorter)

> **S2 (retrieval + ranking) is on `mansimran/retrieval_and_evidence` and works.** Tests green, rebased on main.
>
> Three live keyless sources — Wikipedia, OpenAlex, PubMed — median ~1.1 s per claim, all in parallel. A source that's down is logged in the audit trail and the run continues, so retrieval can't kill the demo. `RetrievalConfig(offline=True)` runs with no network.
>
> **Contract confirmed on my side:** `claim_id, url, domain, title, text_or_snippet, published_at, source_kind, relevance`.
> **Lead:** please confirm these field names are final in `contracts.py`, and tell me whether the audit should come back nested (`report["retrieval"] = rr.audit`) or flattened into the report. I'll adapt either way — I just want it fixed before we both write against it.
> **Ikjot:** I can hand you a JSON bundle of real evidence for 3 claims the moment you want to start S3 — say the word and it's in the repo.
> **Taranpreet:** retrieval numbers for the demo cards are in `docs/S2_README.md`; audit text is print-ready.
>
> Rebased on main, so no merge debt on my side.

---

## 4. Tag a known-good commit (10 seconds, saves the demo)

```bash
git tag -a s2-known-good -m "S2 retrieval: live-tested, tests green, rebased on main"
git push origin s2-known-good
```

If a 2 a.m. merge goes sideways, `git checkout s2-known-good` gets you a working build instantly.

---

## 5. Then, and only then, build the demo snapshot

The cache TTL is 900 s, so a warmed cache is dead an hour later. Before the demo you need a
**saved snapshot that replays forever**, tagged as a cached demo backup (the plan is explicit
that cached output must be labelled, never presented as live).

Ask me and I'll build `scripts/warm_cache.py` + a `SnapshotSource` adapter for it.
