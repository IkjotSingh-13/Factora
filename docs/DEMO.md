# VerifyAI demo script (3 minutes)

Two things this demo has to land, in this order: (1) a claim can be checked against real
evidence **without the tool inventing a quote**, and (2) when the evidence is thin, the tool
says so instead of guessing. Everything else is decoration.

## Before you present

```bash
cd Factora
./scripts/bootstrap.sh --skip-tests     # once, on whatever machine you will present from
./scripts/bootstrap.sh --ui --skip-tests   # optional: also build the React UI served at /
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --app-dir backend
```

`/` is the React UI (`frontend-app/dist`) when it has been built, and the zero-build file
otherwise; **`/lite` is always the zero-build file**. The beats below are written for the React
UI, with the `/lite` element named where the wording differs - so whichever is on screen, you
are pointing at something that exists.

`docs/SETUP.md` is the long version (keys, Ollama, troubleshooting). Nothing in this script
requires them: it assumes no keys, no model server and no network beyond localhost.

Open `http://localhost:8000/` (Swagger at `/docs`). Warm the process with one run before you
speak: `+ Verify` → paste a sample article (`curl -s localhost:8000/api/samples | python -m
json.tool` prints them, and the `/lite` UI has sample buttons) → `offline` → Verify. The run is
~50 ms warm, and the elapsed timer next to the Verify button counts real seconds, so a cold
start costs nothing to hide.

Check what this process will actually do: `GET /api/config` reports the S1 provider, the S3
mode, whether the snapshot file exists and which API keys are present. On a laptop with no
keys and no network it reads `s1_provider: mock`, `verify_mode: auto` with
`verify_mode_note: "per request: the mode in the request decides"`, `snapshot_available: true`
— which is exactly the setup this script assumes. `python scripts/check_connections.py` prints
the same facts plus whether Ollama and each source answer; every `absent` row there is a
supported state, so read it out as "the tool runs without all four of those".

If `check_connections.py` reports `ollama · inference: ok`, you have one more beat available:
run a sample in `auto`, and the audit footer reads `analyzer: llm` instead of `lexical` — the
stance came from the local model, and the quote guard still dropped anything it invented. Say
which machine that is on; do not demo it if you have not run it once yourself.

## Beat 1 — paste an article, get claims with quotes (0:00–0:45)

Pick **`refuted`** from the sample buttons (each sample is written to exercise one honest
outcome; `data/samples.json` records which). Set mode **offline**. Hit Verify.

Point at the claim cards and say: *every card shows the sentence from the article it came
from, quoted verbatim. If extraction had paraphrased, the guard would have dropped the claim —
the Audit tab counts those drops (`claims_dropped_ungrounded`, currently 0 because these samples
are written as clean declarative sentences). On the same page, `DROPPED QUOTES` counts the
unmatched bytes and the quote-guard column says per claim how many quotes actually matched.*

Measured for that sample in `offline` mode: 6 claims kept, all 6 with exact quotes,
`[CONFLICTING EVIDENCE]` + 5 × `INSUFFICIENT EVIDENCE`, ~47 ms.

## Beat 2 — the guard actually drops stuff (0:45–1:30)

Switch to the **`thin`** sample (an article whose claims nothing in the corpus speaks to).

Every card reads **INSUFFICIENT EVIDENCE** at strength 0.50, and the amber block under it
spells out the floor that was not met — `Only 1 quote-verified item(s) could be scored, and the
floor is 2`, `1 independent domain(s) answered, and a decisive verdict needs 2`. The evidence
header below it reads `0 of N quotes matched verbatim`, and the Audit tab's **Run Caveats**
panel repeats the pipeline's own warnings. (`/lite` shows the same degraded state as per-stage
progress chips: `S2 retrieval (offline) → degraded`.)

Say: *"This is the important screen. A model that always has an opinion is useless for this
job. Here the pipeline retrieved nothing it could quote, so it refused to conclude anything —
and it tells you which stage was degraded and how long each stage took."*

Then open the audit footer and read the counters: retrieved / used / independent domains /
quotes dropped / sources failed, plus per-stage milliseconds.

## Beat 3 — real sources, and what changes (1:30–2:20)

Set mode to **live** and run **`supported`** (the Eiffel Tower height claim).

Measured on 2026-10-31 in this environment: 5 claims, 19 evidence items across 6 domains,
9 quote-verified, 6 with fetched page text, `C = 0.81–0.85`, verdicts
`[INSUFFICIENT, CONFLICTING, MIXED, MIXED, INSUFFICIENT]`, ~4.3 s, 2 warnings.

Two things to say while it runs:

- *"The strength number is not a probability of truth — it is how good the evidence is: credibility,
  relevance, agreement across independent domains, timeliness, and whether the quote was read in
  context. The direction gauge is separate: which way that evidence points."*
- *"Every evidence card carries the source link and the exact quote we scored. The chip on the
  quote says whether it was verified in fetched page text or only in the search snippet. A quote
  we cannot find in the retrieved text is never shown as support."*

If the network or the judge's Wi-Fi is unreliable, switch to **snapshot** instead — it replays
`data/demo_snapshot.json` (real captured pages) and the banner says
**demo backup** in the same breath as the result. Measured: 317 ms, `demo_backup: true`,
`quote_verified_items: 2`, `C = 0.35 / 0.8`.

## Beat 4 — the score is arithmetic you can check (2:20–3:00)

Press **View S Equation** in one claim card: C/R/A/T/K rows with weight, points and the reason
for each, and `evidence_strength` in the card header. `score_raw` and `shrinkage_factor` are not
in the React UI — they are one curl away, and running it in front of them is the point:

```bash
curl -s localhost:8000/api/verify -H 'content-type: application/json' \
  -d '{"article_text":"The Eiffel Tower is 330 metres tall.","mode":"offline","as_of":"2026-10-31"}' \
  | python -c 'import json,sys; d=json.load(sys.stdin); c=d["report"]["claims"][0]; print(c["scores"], c["contributions"], c["score_raw"], c["verdict"])'
```

```
{'C': 0.1, 'R': 0.9558, 'A': 0.7769, 'T': 0.8233, 'K': 0.811}
{'C': 0.03, 'R': 0.2868, 'A': 0.1554, 'T': 0.0823, 'K': 0.0811}
0.6356  MIXED / PARTIALLY SUPPORTED
```

*"The contributions add up to the score, and `docs/SCORING.md` shows the derivation. In this
mode C is 0.10 because every source in the local demo corpus is tagged mock and scored 0.10
by design — the warning at the top of the screen says so rather than letting the number look
better than the evidence."*

## If something breaks

| What happens | Why it is fine | What to do |
| --- | --- | --- |
| Live run returns all INSUFFICIENT | Sources answered with snippets only; the guard refuses to score a quote it cannot find | "That is the guard working" → switch to offline, then snapshot |
| URL paste returns HTTP 502 | The target site refused our fetcher (`trafilatura, 0 chars < 200 required`, measured on Wikipedia) | Paste the text instead — never blame the demo, say what the error means |
| No API keys | Retrieval fails soft per source; the response lists which sources failed in `audit.sources_failed` | Run offline; the mode pill already says so |
| Wi-Fi dead entirely | `mode=offline` needs no network at all | Continue from Beat 2 with the `refuted` sample |

## Slides

`docs/slides.html` is the deck — 9 slides with speaker notes, one self-contained file, no
build step, no internet: open it in a browser, `→` to advance, `n` to show the notes, `f` for
fullscreen, print it for a handout. It is ordered for the 3-minute version above, and slides
6–7 (refusing + what we tested) are the ones to fall back to if the live run misbehaves.

## Rehearsal checklist (run it twice, out loud, before you submit)

- [ ] `python -m pytest -q` → 423 passed, exit 0. If it is not, you are demoing a broken build.
- [ ] `python scripts/check_connections.py` → 0 `FAIL` rows. `absent` is fine (no key, no Ollama);
      a `FAIL` is a dependency that is configured and broken, which is the one thing to fix first.
- [ ] Server starts from a **fresh venv** on the presenting machine, not just yours.
- [ ] Beat 1–4 performed once with **live** working, then once with the network turned off.
- [ ] You can say the C=0.10 line without looking: offline sources are mock and scored 0.10 by design.
- [ ] You can point at a `quote_scope` chip and explain snippet vs full text in one sentence.
- [ ] You have read `data/test_set_results.json` recently enough to quote 9/9 and 5/9 in the same breath.
- [ ] Backup article is already pasted in the input box of a second tab, ready to run.
- [ ] Nobody in the team says "accuracy" unless they are about to say "we do not claim one".

## Numbers you may quote, and where they come from

| Claim you can make | Source |
| --- | --- |
| "423 tests pass, exit 0" | `python -m pytest -q` — 423 tests (306 pre-existing + 1 new S2 bound test + 29 S3 + 26 quote-guard + 34 verify API + 7 QA-set + 11 Ollama wiring + 2 provider tests moved in from the repo root + 7 UI-serving contract tests) |
| "9 of 9 scorable cases pointed the expected way, with 0 invariant violations" | `data/test_set_results.json`, `mode=offline` |
| "5 of 9 on captured real sources — retrieval noise costs us 4 cases" | `python scripts/run_test_set.py --mode snapshot` |
| "10/10 evidence items analysed, 0 fabricated quotes, seam compatible" | `python scripts/check_s3_contract.py --analyze s3_analysis:analyze --replay data/demo_snapshot.json` |
| "The UI renders every state with no build step and no CDN" | `node scripts/ui_render_check.mjs` — both frontends: the zero-build file rendered against a real response (6 claim cards, 3 evidence cards, audit footer) and the React app checked for what it must never do again (call a scripted engine, hot-link a font, drop the not-live banner) |
| "~50 ms offline, ~4.3 s live" | `stages[].ms` in the same response, on screen |

Do **not** say "90% accurate", "AI-powered fact-checking", or "we can tell you if something is
true". We say: which way quote-verified evidence points, how good that evidence is, and what we
could not check. See `docs/TEST_SET.md` for why the 9/9 number is not an accuracy figure.
