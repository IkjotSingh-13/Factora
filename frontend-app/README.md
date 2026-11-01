# Factora — the product UI

This is Taranpreet's app from the `frontend` branch, vendored into the VerifyAI repo as
`frontend-app/` and wired to the real pipeline. It is what `http://localhost:8000/` serves once
it has been built; `../frontend/index.html` (one file, no build step) stays reachable at
**`/lite`** and takes over `/` when `dist/` does not exist.

Stack unchanged: React 18, TypeScript, Vite, Tailwind CSS. Nothing else.

```bash
npm install
npm run dev        # http://localhost:5173  (vite proxies /api -> 127.0.0.1:8000)
npm run build      # type check (tsc -b) plus production build -> dist/
npm run preview
```

From the repository root, the same build is `make build-ui`, or
`../scripts/bootstrap.sh --ui` (`bootstrap.ps1 -UI`), and `dist/` is what the API server mounts.
`dist/` and `node_modules/` are gitignored: `make zip` carries the built UI so a teammate needs
no npm.

## Where the data comes from

`src/lib/api.ts` is the only module that talks to the backend. `verifyInput(text, mode,
evidenceMode)` posts `{article_text | url, mode}` to `POST /api/verify`, and `mapResponse()`
turns that response into the `Report` shape this app's components already expected. Every number,
label, quote and link on screen is read out of the response — nothing is computed in the browser,
and nothing is invented:

| on screen | comes from |
| --- | --- |
| claim text, verbatim article quote | `report.claims[].claim`, matched back into the submitted article by the S1/S2 quote guard |
| per-claim C/R/A/T/K rows, S, verdict label, strength | `report.claims[].scores`, `score_raw`, `verdict`, `evidence_strength` |
| evidence passage, its quote, "verified" state, scope | `report.claims[].evidence[]` (`quote_verified`, `quote_scope`) |
| "N of M quotes matched verbatim", dropped-quote and failed-source counts | `audit`, `retrieval_summary` |
| the pipeline's own caveats (Run Caveats panel) | `warnings[]`, quoted verbatim |
| demo label + why a run is not live | `mode`, `demo_backup`, `note` |
| weights `0.30 C + 0.30 R + …` | the scoring formula, as a string — never as a result |

`src/lib/verifyEngine.ts`, which the branch shipped as the data source, is **deleted**: it
generated `en.wikipedia.org/wiki/…` and `reuters.com/fact-check/…` URLs, marked sliced
sentences `quote_verified: true`, hard-coded `C = 0.92 / R = 0.89`, and played 1.4 s of
`setTimeout` "progress". `tests/test_ui_serving.py` fails if it comes back.

## Rules this app has to keep

These are the project's guarantees, restated for the front end. Each has a check behind it.

1. **Never display an unverified quote.** If `quote_verified` is false there is no quote to show;
   the card says so instead. → `ui_render_check.mjs`, `test_fabricated_client_engine_stays_deleted`
2. **Say when a run is not live.** `mode !== "live"` prints the banner in `ReportHeader.tsx`
   (offline / snapshot / cached fixture are worded differently, because they differ).
   → `ui_render_check.mjs`
3. **Name the scope of a match.** A quote verified against a search snippet is labelled as the
   weaker check it is — in the evidence card, in the source modal, in the warning list.
   `Analysis.quote_scope` exists for this; `quote_mode` still means "exact vs insertions".
4. **No network dependency on the page itself.** No CDN, no remote fonts, no remote images: the
   hero's six card thumbnails live in `public/hero/` and the two faces in `src/assets/fonts/`.
   Measured with a request log: zero requests outside localhost, at any viewport.
   → `test_react_app_has_no_hot_linked_assets`
5. **Decorative data must say it is decorative.** The hero's audit cards are mock-ups of the
   product, so each reads `DEMO FIXTURE - NOT A LIVE RUN` and its source rows say
   `status="DEMO FIXTURE - not fetched"`. The two fixture reports (`Apollo 11`, `Cold Plunge`)
   self-label through `mode: "mock"` + `demo_label`, and the audit tab's fixture chips cannot
   claim to be your run: a real run gets a `This run` chip.
6. **No feature work after hour 14.** This directory is finished unless a check fails.

## What is where

```
src/
  App.tsx                    view switch: hero, report, method, limits, audit (+ modal verify)
  types.ts                   the Report/Claim/Evidence schema the backend response is mapped onto
  lib/api.ts                 the ONLY module that touches /api — client + response mapper
  components/
    Navbar.tsx               fixed nav, hamburger morph, mobile overlay
    Hero.tsx                 blurred label, typewriter, pill actions with copy
    DistillHero.tsx          landing: pointer-scrubbed hero video, draggable demo fixture belt
    VerifyInput.tsx          article/URL input, evidence-mode pills, real elapsed timer, errors
    ReportView.tsx           report shell, claim selection, source panel, export
    ReportHeader.tsx         title block, claim counts, not-live banner, disclaimer
    ClaimRail.tsx            claim index and stance split
    ClaimDetail.tsx          direction, strength, score table, evidence, audit
    AuditView.tsx            run telemetry: adapters, quote guard per claim, run caveats, export
    MethodSheet.tsx          scoring method page
    LimitsSheet.tsx          what the tool will not claim
  hooks/
    useTypewriter.ts         character reveal with done state
    useScrubVideo.ts         pointer scrubbed playback with a seek queue
  data/                      two cached demo fixtures, schema 1.0, self-labelled mode: "mock"
  assets/fonts/              Helvetica Now Display, medium and regular
public/hero.mp4              hero footage
public/hero/thumb-*.webp     the six landing-page card images
```

## Two things that differ from the brief, on purpose

**Assets are vendored, not hot linked.** The fonts, the hero video and the card thumbnails are
stored in the repo and served locally. When this branch was adopted, `index.html` still carried
four remote `<link>`s (Google Fonts preconnect + stylesheet, two `db.onlinewebfonts.com`
stylesheets) and `DistillHero.tsx` hot-linked six `cloudfront.net` webp files, which is the
opposite of what its README claimed. All ten are local now, so the preview renders with no
network and the faces are identical — these are the same files the remote URLs served.

**Copy is Factora's.** The layout, type sizes, spacing, animation timings and component structure
follow the brief line for line. The words are the product's: the agent line becomes "Factora's
Adaptive Response Interface Agent", the four pills open real parts of the report, and the nav is
Report, Method, Limits, Audit instead of Labs, Studio, Openings, Shop. Swapping the original
strings back in is a one line change in `Navbar.tsx` and `Hero.tsx`.

## Notes on the video scrub

`useScrubVideo` tracks horizontal movement, converts delta to a time offset scaled by clip
duration, and clamps to the clip. It only issues a new seek when the previous one has settled, so
a fast pointer cannot flood the decoder. It starts at 12 percent of the clip rather than frame
zero, so the frame is never a blank slate.

## Font note

Helvetica Now Display has a narrow space glyph, roughly 0.19em. At 10 to 13px that reads as one
run of letters, so `index.css` adds a small amount of word spacing on `body`, and a larger amount
on the `.mono` labels. Form controls do not inherit `word-spacing` in any engine, so `button`,
`input`, `select` and `textarea` re-inherit it explicitly. Without that rule the claim cards,
which are buttons, lose their word gaps.
