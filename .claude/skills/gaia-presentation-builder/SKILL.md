---
name: gaia-presentation-builder
description: "Generate a two-tier slide deck (technical + executive) from a source document as self-contained HTML that prints cleanly to PDF (one slide per page). Use whenever the user asks to make a presentation, build a deck, turn a README/spec/design doc into slides, create a pitch deck, or produce technical or executive slides from a document. Output is offline HTML with an @media print profile; no PPTX/Keynote/Google Slides."
---

# GAIA Presentation Builder

Turn one source document into a credible slide deck in two tiers — **technical**
(engineers) and **executive** (decision-makers) — as a single self-contained HTML file
per tier that exports cleanly to PDF, one slide per printed page.

## Invocation

`gaia-presentation-builder <source-path> [--tier technical|executive]`

- **Input:** a path to a repo file (`.md`, `.mdx`, `.json`, `.html`, `.txt`) — the primary
  form. Pasted text is accepted as a fallback when no file exists.
- **--tier:** omit to build **both** tiers. Each tier is a separate output file.
- **Output:** `<source-dir>/decks/<source-stem>.<tier>.html` (deterministic; re-runs
  overwrite in place — never hand-edit the output).

## Pipeline (follow in order, every run)

1. **Read** the source document in full.
2. **Build a real-artifact inventory.** List every concrete artifact that *actually exists
   in the source*: metrics, tables, charts, code/contract snippets, screenshots, version
   strings, dated milestones — each with its source location (`file:line` or section). This
   inventory is the ONLY content source for slide facts.
3. **Ask the user upfront** for any known evaluation/benchmark results the source does not
   contain (accuracy, latency, throughput, cost, adoption). One concise prompt; proceed
   with whatever they give.
4. **Select slides per tier** (rules below), drawing values only from the inventory or the
   user's answers. Any quantitative point with neither becomes a **loud, labeled
   placeholder** — never an invented value.
5. **Emit** the deck: copy `assets/deck.css`, `assets/fonts.css`, and
   `assets/deck-viewer.js` inline into one HTML file, compose slides from
   `assets/slide-blocks.html`, populate from the inventory. Write to the output path.
6. **Self-check** against the checklist, then **report the placeholder list** to the user
   (every `⚠ PLACEHOLDER` with what value it needs).

## Credibility contract (non-negotiable)

Resolve every slide value in this order: **cite-from-source → else ask the user → else
insert a loud, labeled placeholder.** The one forbidden act is **silent fabrication**: an
invented number presented as if it came from the source. Placeholders are fine because they
are visually unmistakable as not-yet-real and are reported back. Where the source supports
no real artifact for a slide and the slide is not load-bearing, omit it rather than pad with
generic prose.

## Tiers (different facts, not different length)

**Technical** — engineering audience:
- Architecture, methodology, the request/response contract, metrics *with* their method.
- Code blocks and inline-SVG diagrams allowed.
- Typically 8–14 slides.

**Executive** — leadership audience:
- Leads with outcome, impact, timeline, risk, cost. Distilled but still concrete (real
  numbers/outcomes only).
- **Forbidden: code blocks, architecture diagrams, methodology sections.**
- Materially fewer slides than the technical tier (typically 5–7). Must select *different
  facts* — not the technical deck truncated or renamed.

## Self-contained + print (enforced by the assets — do not weaken)

- Inline everything. No `http(s)://`, no CDN, no external file refs.
- `assets/deck.css` carries the `@media print` profile: `@page { size: landscape; margin:0 }`,
  one slide per page (`break-after: page; break-inside: avoid`), `height:auto; min-height:100vh`
  in print, all chrome hidden. Do not edit these rules per-deck.
- The viewer JS is screen-only; it is disabled under `@media print`.

## Worked examples

Both examples were generated from the GAIA email agent hub package
(`hub/agents/python/email/`) and demonstrate the full pipeline end-to-end.
Eval-metric slides intentionally show labeled `⚠ PLACEHOLDER` values because no
benchmark results existed in the source at generation time.

- **Technical tier (12 slides):** `reference/example-technical.html` — architecture,
  request/response contract, packaging table, token-usage schema, code blocks.
- **Executive tier (7 slides):** `reference/example-executive.html` — outcome, timeline,
  risk, and cost; no code, no architecture diagrams.

## Checklist before reporting done

- [ ] One self-contained HTML file per tier; no external refs (`grep -c 'https\?://' ` is 0).
- [ ] Executive tier: no `<pre>`/code blocks, no architecture diagram, no methodology slide;
      fewer slides than technical.
- [ ] Every quantitative value is sourced, user-supplied, or a labeled placeholder.
- [ ] Placeholder list reported to the user.
- [ ] (If a browser is available) printed-page count == slide count, nothing clipped.
