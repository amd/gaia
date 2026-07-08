---
name: gaia-executive-presentation
description: "Generate an EXECUTIVE slide presentation (leadership / decision-maker audience) from a source document as self-contained HTML that prints cleanly to PDF, one slide per page. Use when the user asks for an executive presentation, a leadership briefing, a pitch or exec deck, or to turn a README/spec/design doc into executive slides. Leads with outcome, impact, timeline, risk, and cost; no code blocks, no architecture diagrams, no methodology. Offline HTML with an @media print profile; no PPTX/Keynote/Google Slides. For an engineering-facing version, use gaia-technical-presentation."
---

# GAIA Executive Presentation

Turn one source document into a credible **executive** slide presentation for a leadership /
decision-maker audience — outcome, impact, timeline, risk, and cost — as a single
self-contained HTML file that exports cleanly to PDF, one slide per printed page.

This skill produces the **executive tier only**. For an engineering-facing version of the
same source (architecture, methodology, contracts), use the **`gaia-technical-presentation`**
skill. The executive presentation is **not** a shortened technical one: it selects *different
facts* for a different audience.

## Invocation

`gaia-executive-presentation <source-path>`

- **Input:** a path to a repo file (`.md`, `.mdx`, `.json`, `.html`, `.txt`) — the primary
  form. Pasted text is accepted as a fallback when no file exists.
- **Output:** `<source-dir>/presentations/<source-stem>.executive.html` (deterministic;
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
   the source*: outcomes, metrics, dated milestones, cost/footprint facts, adoption — each
   with its source location (`file:line` or section). This inventory is the ONLY content
   source for slide facts.
3. **Ask the user upfront** for any known impact figures the source does not contain
   (accuracy, time saved, adoption, cost). One concise prompt; proceed with whatever they give.
4. **Select the executive slides** (rules below), drawing values only from the inventory or
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
prose. Executive does not mean vague — every claim must still be concrete and credible.

## Executive tier — what belongs here (and what must not)

- Leadership audience: **lead with outcome, impact, timeline, risk, and cost.** Distilled but
  still concrete (real numbers/outcomes only).
- **Forbidden: code blocks, architecture diagrams, methodology sections.** (The header
  chevron glyph is fine; do not add a pipeline/architecture diagram.)
- **Materially fewer slides** than a technical deck would use — typically 5–7.
- Select *different facts* for this audience, not the technical deck truncated or renamed. If
  you find yourself copying technical slides and deleting detail, stop and re-select for
  impact.

## Self-contained + print (enforced by the shared assets — do not weaken)

- Inline everything. No `http(s)://`, no CDN, no external file refs.
- `deck.css` carries the `@media print` profile: `@page { size: landscape; margin:0 }`, one
  slide per page (`break-after: page; break-inside: avoid`), `height:auto; min-height:100vh`
  in print, all chrome hidden. Do not edit these rules per-presentation.
- The viewer JS is screen-only; it is disabled under `@media print`.

## Worked example

`reference/example-executive.html` — generated from the GAIA email agent hub package
(`hub/agents/python/email/`): 7 leadership-framed slides (problem/compliance risk, business
value, deployment footprint, timeline, KPIs, close) — no code, no architecture diagrams, no
methodology. Its KPI and timeline-date values intentionally show labeled `⚠ PLACEHOLDER`
because no benchmark/date data existed in the source at generation time — a demonstration of
the credibility contract.

## Checklist before reporting done

- [ ] **Does the presentation satisfy the user's goal?** Re-read the user's request: do the
      slides cover what they asked to present, framed for leadership (impact/decision), so the
      audience can act on it? If not, revise before reporting done.
- [ ] No `<pre>`/code blocks, no architecture diagram, no methodology slide.
- [ ] Materially fewer slides than a technical deck of the same source would be.
- [ ] One self-contained HTML file; no external refs (`grep -c 'https\?://'` is 0).
- [ ] Every quantitative value is sourced, user-supplied, or a labeled placeholder — no
      fabricated numbers.
- [ ] Placeholder list reported to the user.
- [ ] (If a browser is available) printed-page count == slide count, nothing clipped.
