# S2 — Retrieval & Evidence Ranking (VerifyAI)

Owner: **Mansimran**. Stage S2 of the VerifyAI pipeline:
`claim → [S2: retrieve + rank evidence] → stance → score → report`.

A claim goes in; a **ranked, de-duplicated, normalized, citable** evidence list comes out,
with an audit record of which sources were queried, which failed, and how long it took.

> Principle: retrieval never decides truth, and never lets a dead API kill the demo.

---

## Quick start

**Windows / PowerShell:** see [`docs/WINDOWS.md`](docs/WINDOWS.md), or just run
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` then `.\scripts\setup_windows.ps1`.

```bash
pip install -r requirements.txt
cp .env.example .env          # keys are optional; .env is auto-loaded

# single claim (live sources)
python s2_retrieval.py "Vitamin C supplementation prevents the common cold in most adults" --top-k 6

# same claim, no network at all (demo safety net)
python s2_retrieval.py --offline "vitamin C common cold"

# JSON for the API layer
python s2_retrieval.py --file claim.txt --json --out out.json   # --out avoids PowerShell UTF-16 redirects

# measured source reliability across a fixed claim set
python scripts/live_smoke.py

# capture a demo snapshot once (needs network), then replay it offline forever
python scripts/warm_snapshot.py
python s2_retrieval.py --replay-snapshot data/demo_snapshot.json "vitamin C common cold"

# tests (no network needed)
pytest -q

# something looks broken? (interpreter, packages, .env, sources, snapshot)
python scripts/doctor.py
```

## Integration (what the Team Lead calls)

```python
from s2_retrieval import RetrievalEngine, RetrievalConfig

engine = RetrievalEngine(RetrievalConfig(top_k=6, mock=True))   # mock=True adds the local corpus fallback
rr = engine.search_claim(claim.text, claim_id=claim.id)

rr.evidence        # list[contracts.Evidence]  -> Ikjot's stance analysis (S3)
rr.audit           # contracts.RetrievalAudit  -> report audit footer + scoring inputs
rr.has_evidence    # False => surface the "insufficient evidence" state
```

Nothing else is required: no database, no vector store, no model download, no API key.
`contracts.py` mirrors the frozen shared schema (the lead owns it — read, don't edit).

## What a call does

1. **Queries** — a literal keyword query is always issued (filler stripped, entities/numbers kept),
   plus one entity/number variant. LLM rewrites are optional and off by default.
2. **Fetch in parallel** — every configured adapter runs with a hard timeout, bounded retries,
   and a TTL + disk cache. A failing adapter becomes an audit row and the run continues.
3. **Normalize** — everything becomes a contract `Evidence` item
   (`claim_id, url, domain, title, text_or_snippet, published_at, source_kind, relevance`).
4. **Rank** — blended similarity (TF-IDF cosine 0.5 + BM25 0.3 + RRF 0.2), with a
   distinctive-term **coverage** signal; zero-overlap candidates are heavily penalized.
5. **Clean** — canonical-URL de-dupe, near-duplicate shingle de-dupe, per-publisher cap
   (`doi.org` grouped by DOI prefix so academic sources aren't collapsed), then `top_k`.
6. **Guarantee diversity** — one slot reserved per source kind that produced results,
   so "independent agreement" in the score has something to stand on.
7. **Audit** — sources queried / ok / failed, results per source, items used, duplicates dropped,
   domain cap drops, low-coverage drops, distinct domains, cache hits, latency, human-readable notes.

## Sources

| Adapter | Kind | Key | Notes |
|---|---|---|---|
| `WikipediaSource` | encyclopedia | none | MediaWiki `generator=search` + intro extracts. Live-verified. |
| `OpenAlexSource` | academic | none | Scholarly works with reconstructed abstracts + dates. Live-verified. |
| `PubMedSource` | academic | none | E-utilities esearch + efetch XML, PMID links. Live-verified. |
| `TavilySource` | web | `TAVILY_API_KEY` | Auto-enables when the key is set. |
| `GoogleFactCheckSource` | fact_check | `GOOGLE_FACTCHECK_API_KEY` | Highest value source; auto-enables with a key. |
| `DuckDuckGoSource` | web | none | HTML endpoint sometimes serves anti-bot pages → returns `[]`, never fails. |
| `LocalCorpusSource` | mock | none | Offline fallback from `data/mock_corpus.json`; output is tagged `mock` / `demo_backup`. |

Adding a source = subclass `SourceAdapter`, implement `search()`, add it to
`default_adapters()`. It inherits timeouts, retries, caching, ranking, de-dupe and audit.

## Files

```
contracts.py        frozen shared schema (lead owns)
sources.py          source adapters + URL/date normalization
ranking.py          TF-IDF/BM25/RRF blend, coverage, de-dupe
s2_retrieval.py     orchestration, fail-soft execution, audit  (+ CLI)
cache.py            TTL cache, memory + disk
snapshot.py         capture + replay demo evidence (offline backup, labelled as such)
requirements-s2.txt module-scoped install list (immune to shared-file churn)
envload.py          tiny .env loader (no python-dotenv needed)
http_util.py        timeouts, retries, cache-aware requests
tests/test_s2.py    27 offline tests
scripts/live_smoke.py   live source-reliability + latency report
scripts/warm_snapshot.py  capture data/demo_snapshot.json for offline demo replay
scripts/pre_pr_check.sh rebase on main + tests + contract-drift diff (run before every push)
scripts/pre_pr_check.ps1   same, PowerShell edition (Windows)
scripts/setup_windows.ps1  venv + deps + .env + tests + doctor, one shot
scripts/setup_windows.bat  same, cmd.exe edition (ignores execution policy)
scripts/doctor.py          diagnose interpreter / packages / .env / network / snapshot
docs/WINDOWS.md            PowerShell quickstart and the three Windows gotchas
scripts/git_branch_setup.sh  create/switch to mansimran/retrieval_and_evidence
docs/MANSIMRAN_ROLE.md  role analysis, handoff, git branch workflow
```

## Tuning knobs (`RetrievalConfig` / `.env`)

| Knob | Default | Effect |
|---|---|---|
| `top_k` | 6 | evidence items returned per claim |
| `per_source_limit` | 5 | results requested per source per query |
| `max_per_domain` | 2 | cap per publisher (see `sources.cap_key`) |
| `min_coverage` | 0.34 | drops evidence that doesn't mention the claim's distinctive terms |
| `timeout_s` / `retries` | 8 s / 1 | per-request budget |
| `cache_ttl_s` | 900 | memory + disk cache lifetime |
| `offline` / `mock` | off | local corpus only / live + local corpus |
| `replay_snapshot` | off | serve captured evidence only, no network (demo backup) |

## Demo backup (read this before you demo)

`data/demo_snapshot.json` holds real retrieval output captured once with
`scripts/warm_snapshot.py`. Replaying it needs no network, no keys and no cache TTL,
so the demo survives a dead wifi. It is **labelled everywhere**:

- audit note: `DEMO BACKUP - evidence replayed from a cached snapshot captured <timestamp>`
- source row: `snapshot` with `cached=True`
- each item carries `demo_backup: True`

Say "this is a cached run from earlier" out loud. The plan is explicit about that,
and judges check for it.

## Honest limitations (say these in the demo)

- **Relevance ≠ truth.** A 0.73 relevance means "this text is about the claim", nothing more.
- Source mix is what it is: Wikipedia, OpenAlex and PubMed. No paywalled news, no social media.
- DDG's HTML endpoint is unreliable from datacenter IPs; treat it as a bonus, not a dependency.
- Snippets come from APIs, not full-text scraping — some evidence is a summary, not the article body.
- Latest-coverage is weak: dates are often missing or coarse (year-only), so the `T` term is soft.
- If nobody wrote about the claim, the right output is **no evidence**, not a weak verdict.
