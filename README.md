# Factora

Landing page and evidence report for Factora, built to the Mainframe-style hero spec:
full-bleed video that scrubs with the pointer, blurred agent label, typewriter line,
pill actions, and the report rendered in the same type system.

Stack: React 18, TypeScript, Vite, Tailwind CSS. Nothing else.

```bash
npm install
npm run dev        # http://localhost:5173
npm run build      # type check plus production build
npm run preview
```

## What is where

```
src/
  App.tsx                    view switch: hero, report, method
  components/
    Navbar.tsx               fixed nav, hamburger morph, mobile overlay
    Hero.tsx                 blurred label, typewriter, pill actions with copy
    ReportView.tsx           report shell, claim selection, source panel, export
    ReportHeader.tsx         title block, claim counts, disclaimer
    ClaimRail.tsx            claim index and stance split
    ClaimDetail.tsx          direction, strength, score table, evidence, audit
    MethodSheet.tsx          scoring method page
  hooks/
    useTypewriter.ts         character reveal with done state
    useScrubVideo.ts         pointer scrubbed playback with a seek queue
  data/                      two cached fixtures, schema 1.0
  assets/fonts/              Helvetica Now Display, medium and regular
public/hero.mp4              hero footage
```

## Two things that differ from the brief, on purpose

**Assets are vendored, not hot linked.** The fonts and the hero video are stored in the
repo and served locally. The brief pointed at remote URLs for both; vendoring means the
preview renders with no network and the faces are identical, since these are the same
files the remote URLs serve.

**Copy is Factora's.** The layout, type sizes, spacing, animation timings and component
structure follow the brief line for line. The words are the product's: the agent line
becomes "Factora's Adaptive Response Interface Agent", the four pills open real parts of
the report, and the nav is Report, Method, Limits, Audit instead of Labs, Studio,
Openings, Shop. Swapping the original strings back in is a one line change in
`Navbar.tsx` and `Hero.tsx`.

## Notes on the video scrub

`useScrubVideo` tracks horizontal movement, converts delta to a time offset scaled by
clip duration, and clamps to the clip. It only issues a new seek when the previous one
has settled, so a fast pointer cannot flood the decoder. It starts at 12 percent of the
clip rather than frame zero, so the frame is never a blank slate.

## Font note

Helvetica Now Display has a narrow space glyph, roughly 0.19em. At 10 to 13px that reads
as one run of letters, so `index.css` adds a small amount of word spacing on `body`, and a
larger amount on the `.mono` labels. Form controls do not inherit `word-spacing` in any
engine, so `button`, `input`, `select` and `textarea` re-inherit it explicitly. Without
that rule the claim cards, which are buttons, lose their word gaps.
