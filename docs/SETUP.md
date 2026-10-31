# Setup: from a clean machine to a running VerifyAI

The one-command path first, then each level of "more connected" separately, so a demo that
only needs level 1 never has to debug level 4.

```bash
git clone https://github.com/IkjotSingh-13/Factora.git && cd Factora
./scripts/bootstrap.sh              # macOS / Linux
```

```powershell
git clone https://github.com/IkjotSingh-13/Factora.git; cd Factora
.\scripts\bootstrap.ps1             # Windows (PowerShell)
```

Both create `.venv`, install the three requirements files, fetch the spaCy model if the network
allows, copy `.env.example` → `.env`, and run the test suite. They are idempotent — run them
again after pulling, or after adding a key. Then:

```bash
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Open **http://localhost:8000/** — that is the whole app. `/docs` is Swagger, `/health` is
liveness, `/api/config` says what this process will actually do (start there when behaviour
and this document disagree).

Windows note: if the script is blocked, run
`powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1`.

---

## Level 0 — nothing configured (this is a supported state)

`mode = offline` in the UI. No keys, no network, no model server:

- S1 runs its deterministic extractor (the mock/heuristic provider),
- S2 reads `data/mock_corpus.json`,
- S3 runs the lexical analyser,
- every one of those substitutions is named in the response `warnings` and in the audit footer.

Run the QA set to see it: `python scripts/run_test_set.py` → 9/9 directions met offline.

**What you will notice:** verdicts top out around MIXED / CONFLICTING. That is by design —
every demo-corpus source is `source_kind=mock` with credibility 0.10, so the C term (weight
0.30) cannot carry a decisive result. The screen says so. Do not "fix" it by inflating
`CREDIBILITY_HINTS`.

## Level 1 — real sources, still no keys

`mode = live`. Three adapters need no key at all: **Wikipedia, OpenAlex, PubMed** (DuckDuckGo is
last in the list and may return an anti-bot page from datacenter IPs; it then yields nothing
instead of failing).

```bash
python scripts/check_connections.py --only wikipedia,openalex   # is it reachable from here?
```

Measured on this repo: a 5-claim article in `live` mode → 19 evidence items across 6 domains,
9 quote-verified, 6 with fetched page text, credibility 0.81–0.85, ~4.3 s.

To fetch more page text (so more quotes can be verified against real pages rather than
snippets): `S2_FETCH_FULL_TEXT=6` in `.env`.

## Level 2 — the two keys worth having

```bash
cp .env.example .env      # skip if bootstrap already made one
```

| Variable | Where | Why it matters |
| --- | --- | --- |
| `GOOGLE_FACTCHECK_API_KEY` | [Claim Review Tools](https://console.cloud.google.com/apis/library/factchecktools.googleapis.com) | the only source that returns a *published* ruling; enables `source_kind=fact_check`, the highest-credibility bucket |
| `TAVILY_API_KEY` | [tavily.com](https://tavily.com) | general web coverage; free tier is enough for a demo |

`OPENALEX_MAILTO=you@example.com` is not a key but gets you the polite pool (faster answers).
`.env.example` is safe to copy as-is: every line in it is either a key you fill in or a knob
whose value equals the code's own default, and none of them changes what the UI does until you
ask for it. Keys are read from `.env` at startup (`backend/config.py` via python-dotenv, `envload.py` for
S2), and a real environment variable always wins over the file — so
`$env:TAVILY_API_KEY="..."` in PowerShell overrides without editing anything.

Verify: `python scripts/check_connections.py --only tavily,factcheck` — a refused key says so
with HTTP status; nothing is ever printed but the fact that a key is set.

## Level 3 — a local model (Ollama)

This is the level that changes *analysis quality*, because S3 stops relying on the lexical
aligner alone.

```bash
# 1. install Ollama (https://ollama.com/download), then:
ollama serve                    # not needed if you installed the desktop app
./scripts/bootstrap.sh --ollama # pulls the model and writes the .env lines for you
```

or by hand:

```bash
ollama pull qwen2.5:7b
```

```ini
# .env
LLM_PROVIDER=ollama
OLLAMA_MODEL=qwen2.5:7b
OLLAMA_TIMEOUT=120
S3_MODE=auto          # try the model, fall back to lexical, record which one ran
# S3_MODE=llm         # require the model; still falls back, but the audit says so
```

```bash
python scripts/check_connections.py --only ollama
# [ ok ] ollama · server      reachable
# [ ok ] ollama · model       qwen2.5:7b present
# [ ok ] ollama · inference   answered in one call
```

What you get, and what you do not:

- **S1** extraction prompts become model-backed (structured JSON out of the same provider).
- **S3** stance analysis asks the model per claim, and the **quote guard still applies**:
  a quote the model invents is dropped, the item falls back to the lexical answer, and the item
  is tagged `lexical (after the LLM answer was rejected)` — see
  `tests/test_ollama_wiring.py::test_s3_rejects_a_quote_the_source_does_not_contain`.
- The UI shows `analyzer: llm` in the audit footer when the model answered. If it still reads
  `lexical`, either the provider is unavailable (see `s3_audit.provider_note`) or its answer
  was rejected by the guard. Both are recorded, neither is silent.
- Nothing about scoring changes: the model can propose a stance, it cannot smuggle a quote.

First run on CPU is slow (tens of seconds per claim); `OLLAMA_TIMEOUT` is there for that reason.
7B int4 fits in 8 GB RAM; smaller `qwen2.5:3b` works if you only need the mechanism.

## Level 4 — a hosted model instead

```ini
LLM_PROVIDER=gemini
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.0-flash
```

Same contract, same guard, no download. `check_connections.py --only gemini` lists models so a
wrong `GEMINI_MODEL` name is caught before the demo, not during it.

---

## Checks, and what "green" means

```bash
make checks          # or run them one at a time
```

| Gate | Command | Meaning |
| --- | --- | --- |
| suite | `python -m pytest -q` | 415 tests: every stage, the seams, the wiring, the UI contract, the QA set |
| seam | `python scripts/check_s3_contract.py --analyze s3_analysis:analyze --replay data/demo_snapshot.json` | S3 accepts what S2 emits; counts fabricated quotes (must be 0) |
| QA | `python scripts/run_test_set.py` | measured outcomes on labelled cases → `data/test_set_results.json` (9/9 on the blank pipeline, 8/8 with `en_core_web_sm`; `docs/TEST_SET.md` explains why both are correct) |
| UI | `node scripts/ui_render_check.mjs` | the real render functions against a captured response (needs node; nothing else does) |
| wiring | `python scripts/doctor.py` | environment, imports, snapshot, offline replay |
| reachability | `python scripts/check_connections.py` | every external dependency, with a fix line per failure |

A `FAIL` from `check_connections.py` is worth reading; an `absent` row is a supported state.

## Environment reference

| Variable | Default | Read by | Effect |
| --- | --- | --- | --- |
| `LLM_PROVIDER` | `mock` | S1, S3 | `mock` \| `ollama` \| `gemini`; `mock` is *not* an S3 provider (no model behind it) |
| `S1_LLM_FALLBACK` | `true` | S1 | if false, an unavailable provider is an error instead of a fallback |
| `OLLAMA_HOST` / `OLLAMA_MODEL` / `OLLAMA_TIMEOUT` | `http://localhost:11434` / `qwen2.5:7b` / `120` | S1, S3, `check_connections` | local model |
| `GEMINI_API_KEY` / `GEMINI_MODEL` / `GEMINI_BASE_URL` | – | S1, S3 | hosted model |
| `S3_MODE` | `auto` | S3 | `auto` \| `llm` \| `lexicon` |
| `S3_LLM_TIMEOUT` | `60` | S3 | per-claim wall clock; on timeout that claim falls back |
| `TAVILY_API_KEY`, `GOOGLE_FACTCHECK_API_KEY` | – | S2 | enable those adapters |
| `OPENALEX_MAILTO`, `ENABLE_OPENALEX` | – / `1` | S2 | academic source |
| `S2_TOP_K`, `S2_PER_SOURCE_LIMIT`, `S2_MAX_PER_DOMAIN` | `6`, `5`, `2` | S2 | retrieval width and domain caps |
| `S2_FETCH_FULL_TEXT` | `3` | S2 | how many page bodies to fetch per claim (0 = snippets only) |
| `S2_CACHE_TTL_S`, `S2_DISK_CACHE_DIR` | `900`, *off* | S2 | caching; set the dir to persist fetched pages, delete it for a cold run |
| `VERIFY_MODE` | – | API | force `auto`/`live`/`offline`/`snapshot`, overriding the request |
| `VERIFY_OFFLINE`, `VERIFY_SNAPSHOT_ONLY` | – | API | truthy switches for the demo |
| `VERIFY_SNAPSHOT_PATH` | `data/demo_snapshot.json` | API | which capture to replay |
| `SPACY_MODEL` | `en_core_web_sm` | S1 | blank pipeline is used if the model is missing |

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `Address already in use` | another process on 8000 | `--port 8010` (and `make run PORT=8010`) |
| `ModuleNotFoundError: No module named 'app'` | running uvicorn from the wrong place, or `--app-dir` missing | run from the repo root with `--app-dir backend` |
| `python -m pytest` → *no module named pytest* | venv not activated | `source .venv/bin/activate` / `.\.venv\Scripts\Activate.ps1` |
| spaCy model download fails | offline or blocked GitHub | ignore it: S1 falls back to `spacy.blank("en")`; the warning is in the response |
| `live` mode returns `INSUFFICIENT EVIDENCE` everywhere | sources answered with snippets only, and quotes that cannot be located are dropped (correct behaviour) | raise `S2_FETCH_FULL_TEXT`, or demo `offline`/`snapshot` |
| `502 … trafilatura, 0 chars < 200 required` on a URL | the site blocks automated fetching (Wikipedia does) | paste the text; the error is the extractor's own reason, not a bug |
| audit footer says `analyzer: lexical` after configuring Ollama | provider unavailable, or its answers failed the guard | `check_connections.py --only ollama`, then read `s3_audit.provider_note` |
| `ollama` answers 404 | model not pulled | `ollama pull qwen2.5:7b` |
| Ollama unreachable from WSL2 / a container | server bound to 127.0.0.1 | `OLLAMA_HOST=0.0.0.0 ollama serve`, then set `OLLAMA_HOST=http://<host>:11434` |
| first claim takes a minute | model load on CPU | warm it: `ollama run qwen2.5:7b "hi"`, or use a smaller model |
| claims look merged or garbled (two sentences in one claim) | the spaCy model is missing, so S1 is on `spacy.blank` | `python -m spacy download en_core_web_sm`, or re-run `./scripts/bootstrap.sh`; the response warning names the fallback |
| the mode dropdown does nothing | `VERIFY_MODE` is set to a mode in `.env` | empty it — in the current `.env.example` it is, and `auto` is now treated as "no pin" |
| demo Wi-Fi dies | live retrieval | switch the dropdown to `offline` — nothing else changes |

## Presenting on someone else's laptop

1. `./scripts/bootstrap.sh --skip-tests` on that machine, the day before, while the network works.
2. `python scripts/check_connections.py` → screenshot the report; `absent` rows are fine.
3. `python scripts/run_test_set.py` once, so the corpus cache is warm.
4. Run the UI once in `live`, once in `offline`, once in `snapshot` (`docs/DEMO.md` beats 1–4).
5. Keep the repo folder on the machine: the offline path needs no network at all.

Nothing in this project writes outside the repo except `.cache/retrieval` and the optional
`.env`; no telemetry, no accounts, no daemon.
