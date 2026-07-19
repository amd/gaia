---
name: gaia-technical-presentation
description: "Generate a TECHNICAL slide presentation (engineering audience) from a source document as self-contained HTML that prints cleanly to PDF, one slide per page. Use when the user asks for a technical presentation, engineering slides, an architecture or deep-dive deck, or to turn a README/spec/design doc into technical slides. Covers architecture, methodology, request/response contracts, and metrics with their method; code blocks and diagrams allowed. Offline HTML with an @media print profile; no PPTX/Keynote/Google Slides. For a leadership-facing version, use gaia-executive-presentation."
---

# GAIA Technical Presentation

Turn one source document into a credible **technical** slide presentation for an engineering
audience — architecture, methodology, contracts, and metrics — as a single self-contained
HTML file that exports cleanly to PDF, one slide per printed page.

This skill produces the **technical tier only**. For a leadership / decision-maker version of
the same source, use the **`gaia-executive-presentation`** skill.

## Invocation

`gaia-technical-presentation <source-path>`

- **Input:** a path to a repo file (`.md`, `.mdx`, `.json`, `.html`, `.txt`) — the primary
  form. Pasted text is accepted as a fallback when no file exists.
- **Output:** `<source-dir>/presentations/<source-stem>.technical.html` (deterministic;
  re-runs overwrite in place — never hand-edit the output).

## Shared design system

The look, fonts, slide patterns, and print profile live in the shared assets directory and
are inlined verbatim into the output (do not duplicate or hand-edit them per run):

- `.claude/skills/gaia-presentation-assets/assets/fonts.css` — base64 brand fonts (offline)
- `.claude/skills/gaia-presentation-assets/assets/deck.css` — design system + `@media print`
- `.claude/skills/gaia-presentation-assets/assets/deck-viewer.js` — screen-only viewer
- `.claude/skills/gaia-presentation-assets/assets/slide-blocks.html` — slide patterns to compose from

## Pipeline (follow in order, every run)

1. **Read** the source document in full, and re-read the user's request so you know the goal
   the presentation must serve.
2. **Build a real-artifact inventory.** List every concrete artifact that *actually exists in
   the source*: metrics, tables, charts, code/contract snippets, screenshots, version
   strings, dated milestones — each with its source location (`file:line` or section). This
   inventory is the ONLY content source for slide facts.
3. **Ask the user upfront** for any known evaluation/benchmark results the source does not
   contain (accuracy, latency, throughput, cost). One concise prompt; proceed with whatever
   they give.
4. **Select the technical slides** (rules below), drawing values only from the inventory or
   the user's answers. Any quantitative point with neither becomes a **loud, labeled
   placeholder** — never an invented value.
5. **Emit** the presentation: inline `fonts.css`, `deck.css`, and `deck-viewer.js` from the
   shared assets directory into one HTML file, compose slides from `slide-blocks.html`,
   populate from the inventory. Write to the output path.
6. **Self-check** against the checklist, then **report the placeholder list** to the user
   (every `⚠ PLACEHOLDER` with what value it needs).

## Credibility contract (non-negotiable)

Resolve every slide value in this order: **cite-from-source → else ask the user → else insert
a loud, labeled placeholder.** The one forbidden act is **silent fabrication**: an invented
number presented as if it came from the source. Placeholders are fine because they are
visually unmistakable as not-yet-real and are reported back. Where the source supports no real
artifact for a slide and the slide is not load-bearing, omit it rather than pad with generic
prose.

## Technical tier — what belongs here

- Engineering audience: architecture, methodology, the request/response contract, metrics
  *with* their method.
- **Code blocks and inline-SVG diagrams are allowed and encouraged** where they carry real
  source content.
- Typically 8–14 slides — enough to preserve precision and depth.
- This is *not* the executive tier: keep the depth; do not strip methodology to chase brevity.
  (A leadership-facing cut is the `gaia-executive-presentation` skill's job.)

## Self-contained + print (enforced by the shared assets — do not weaken)

- Inline everything. No `http(s)://`, no CDN, no external file refs.
- `deck.css` carries the `@media print` profile: `@page { size: landscape; margin:0 }`, one
  slide per page (`break-after: page; break-inside: avoid`), `height:auto; min-height:100vh`
  in print, all chrome hidden. Do not edit these rules per-presentation.
- The viewer JS is screen-only; it is disabled under `@media print`.

## Worked example

`reference/example-technical.html` — generated from the GAIA email agent hub package
(`hub/agents/email/python/`): 12 slides covering architecture, the request/response contract,
the packaging table, the token-usage schema, and code blocks. Its benchmark slide
intentionally shows labeled `⚠ PLACEHOLDER` values because no eval results existed in the
source at generation time — a demonstration of the credibility contract.

## Checklist before reporting done

- [ ] **Does the presentation satisfy the user's goal?** Re-read the user's request: do the
      slides cover what they asked to present, at engineering depth, so the audience leaves
      with what the user intended? If not, revise before reporting done.
- [ ] One self-contained HTML file; no external refs (`grep -c 'https\?://'` is 0).
- [ ] Every quantitative value is sourced, user-supplied, or a labeled placeholder — no
      fabricated numbers.
- [ ] Placeholder list reported to the user.
- [ ] (If a browser is available) printed-page count == slide count, nothing clipped.
