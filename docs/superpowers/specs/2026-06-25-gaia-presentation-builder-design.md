# GAIA Presentation Builder — Design

**Date:** 2026-06-25
**Status:** Approved (brainstorm) — pending spec review before implementation plan

## Problem

Technical product teams need to present the same project at two altitudes: a deep
technical deck for engineers (architecture, methodology, evaluation numbers) and a
concise executive deck for decision-makers (outcomes, impact, credibility). Producing
these by hand is slow, inconsistent, and goes stale the moment the project moves on.

The hard constraint is **credibility**: decks made of generic, AI-generated prose fail
with technical and executive audiences alike. What lands is concrete evidence pulled
from the source — real evaluation results, benchmark charts, screenshots of actual
output, measurable outcomes. The tool must surface those real artifacts rather than pad
slides with filler.

## Goal

A reusable Claude Code skill, checked into this repository, that takes a source document
(README, design/spec doc, etc.) and generates a slide deck from it in two tiers —
**technical** and **executive** — output as **self-contained HTML** that exports cleanly
to PDF via a `@media print` profile (exactly one slide per printed page).

## Non-goals

- Producing a finished deck for one specific project as the deliverable (the **skill** is
  the deliverable, not any one deck).
- A presentation server, live-reload app, or hosted viewer.
- Output formats other than HTML (PPTX, Keynote, Google Slides, direct-PDF generators).
- A CI pipeline that auto-regenerates decks.
- Crawling external sites / fetching remote URLs during generation.
- Reproducing the reference template's `deck-stage` JavaScript runtime (see Decisions).

## Decisions (resolved during brainstorm)

| Topic | Decision |
|---|---|
| Skill name | `gaia-presentation-builder` (human title: "GAIA Presentation Builder") |
| Location | `.claude/skills/gaia-presentation-builder/` — `SKILL.md` + `assets/` + `reference/`, matching the repo's existing `SKILL.md`-with-frontmatter convention (`gaia-testing`, `gaia-release`) |
| Input form | A repo **file path** (`.md` / `.mdx` / `.json` / `.html` / `.txt`) at minimum; pasted text accepted as a fallback. Stated explicitly in `SKILL.md`. |
| Fonts | **Embed** Archivo + Hanken Grotesk as base64 woff2 inlined in CSS (offline, keeps brand identity). No Google Fonts CDN. |
| PDF export | Skill emits **print-ready HTML only**. It does not run a headless export and does not document a print command (inherent to the browser). |
| On-screen viewer | Keep a **tiny inlined viewer** (~30 lines JS, no CDN): arrow-key navigation + page counter on screen, **fully disabled under `@media print`**. |
| Missing metrics | **Ask upfront** for known eval/benchmark results, then fall back to **clearly-labeled placeholders** for anything still missing; report the placeholder list at the end. |
| Placeholder style | **Loud + watermarked**: tinted `⚠ PLACEHOLDER — provide value` chip on the value AND a deck-level `DRAFT — contains placeholders` banner on any deck that has them. |
| Output path | `<source-dir>/decks/<source-stem>.<tier>.html` — deterministic, overwrite-in-place, no timestamps / random IDs (clean re-runs). |
| First test source | The email agent hub package, `hub/agents/python/email/` (contract/architecture-rich: `openapi.email.json`, `specification.html`, `gaia-agent.yaml`, packaging docs, tests). |

## Reference template

`gaia-roadmap.html` (provided by the user) is the **visual identity** reference, not a
runtime to reproduce. It is a self-contained "bundler" artifact whose real markup lives in
a `__bundler/template` script tag: an 8-slide GAIA strategy deck (Title, The Arc, Email
Triage, Agent Factory, Agent Hub, Timeline, Supporting Work, Close) rendered by a custom
`<deck-stage>` web component.

What we **keep**: the design language — CSS-variable design tokens, AMD-red brand + gold
accent palette, the forward-chevron motif, Archivo (display) + Hanken Grotesk (body) type,
the slide shell (header tab / eyebrow / heading / lead / chips), light/dark tokens.

What we **drop**: (1) the Google Fonts CDN dependency — replaced by embedded base64 fonts;
(2) the `deck-stage` JS runtime — it depends on external bundled JS and renders one scaled
slide at a time (a screen-viewer model) which fights reliable print pagination. Replaced by
a static stacked-slides layout that scrolls on screen and prints one slide per page, plus
the tiny print-disabled viewer above.

## Architecture

The skill is **prompt-driven**, not a deterministic script: slide selection is a
credibility judgment only the model can make. `SKILL.md` walks Claude through a fixed
pipeline, and `assets/` supplies the deterministic presentation layer.

### Components

```
.claude/skills/gaia-presentation-builder/
├── SKILL.md                 # frontmatter (name/description) + the pipeline + the credibility contract
├── assets/
│   ├── deck.css             # design tokens, slide shell, placeholder/banner styles, @media print profile
│   ├── fonts.css            # base64-embedded Archivo + Hanken Grotesk woff2
│   └── slide-blocks.html    # copy-paste slide patterns: title, section, metric, chart (inline SVG), quote, timeline, close
└── reference/
    └── example-technical.html, example-executive.html   # worked example built from the email package
```

- **`SKILL.md`** — what it does, how it's invoked, the input forms it accepts, the
  generation pipeline, the tier rules, and the cite-or-ask-or-flag credibility contract.
- **`deck.css`** — single source of truth for layout + theme + the print profile.
  Consumers never hand-edit it; the generated HTML inlines it verbatim.
- **`fonts.css`** — offline brand typography (sourced and subset once into the skill).
- **`slide-blocks.html`** — the menu of slide patterns Claude composes from; keeps output
  structurally consistent across runs and sources.

### Generation pipeline (in `SKILL.md`)

1. **Read** the source document fully.
2. **Build a real-artifact inventory** — every concrete metric, table, chart, code/contract
   snippet, screenshot, version string, and dated milestone that *actually exists in the
   source*, each tagged with its source location. This inventory is the **only** content
   source for slides.
3. **Ask upfront** for any known eval/benchmark results not present in the source.
4. **Select slides per tier** from the inventory (tier rules below). A point with no real
   artifact and no user-supplied value becomes a **loud, watermarked placeholder** — never
   a fabricated value presented as real.
5. **Emit** one self-contained HTML file per tier: inline `deck.css` + `fonts.css` + the
   composed slide blocks, populated only from the inventory / user input / labeled
   placeholders.
6. **Self-check** against the print + credibility checklist, then **report the placeholder
   list** to the user.

### Credibility contract

The single forbidden thing is **silent fabrication** — an invented number presented as if
sourced. The allowed resolution order for any slide value is:

> **cite from source → else ask the user → else insert a loud, labeled placeholder.**

This consciously relaxes the original brief's strict "omit-or-nothing" into
"omit, ask, or visibly-flag," as authorized by the user. Placeholders are not fabrication
because they are visually unmistakable as not-yet-real and are reported back explicitly.

The guard-rail is prompt-level (it lives in `SKILL.md`); a human still spot-checks
generated slides before sharing.

### The two tiers (different facts, not different length)

- **Technical** — engineering audience. Architecture, methodology, request/response
  contract, metrics *with* methodology. Code/contract blocks and inline-SVG diagrams
  allowed. More slides.
- **Executive** — leadership audience. **Materially fewer** slides; leads with outcome /
  impact / timeline / risk / cost; distilled but still concrete (real numbers/outcomes
  only). **Forbidden elements: code blocks, architecture diagrams, methodology sections.**

Enforced as hard rules in `SKILL.md`. The two decks must be **verifiably distinct** —
different selected facts, not the technical deck renamed or merely truncated.

### Self-contained HTML + print profile

- One HTML file per tier; **everything inline** — CSS, base64 fonts, inline-SVG charts,
  the tiny viewer JS. No network / CDN / external-runtime dependency.
- **Screen:** each `.slide` is a fixed 16:9 surface; stacked in document order; the viewer
  JS provides arrow-key nav + a page counter.
- **`@media print`:**
  - `@page { size: landscape; margin: 0; }`
  - `.slide { break-after: page; break-inside: avoid; height: auto; min-height: 100vh; }`
  - all chrome (viewer controls, theme toggle, page counter, banners-as-overlays) hidden.
  - Result: **exactly one slide per printed page**, consistent size, nothing clipped/split,
    browser header/footer/URL suppressed.

## Data flow

```
source file ──read──> real-artifact inventory ──+── user-supplied metrics (upfront ask)
                                                 │
                                                 └── labeled placeholders (for the rest)
                                                          │
                              tier rules ──select──> slide set (technical | executive)
                                                          │
              deck.css + fonts.css + slide-blocks ──compose──> <stem>.<tier>.html (self-contained)
                                                          │
                                                  self-check + placeholder report
```

## Error / edge handling

- **Unsupported / unreadable source** → fail loudly with an actionable message (what was
  passed, what forms are accepted). No silent empty deck.
- **Source with zero real artifacts** → deck still generates, but every quantitative slide
  is a labeled placeholder and the DRAFT banner is present; the report lists them all.
- **Re-run on a modified source** → overwrites the deterministic output path in place; no
  manual editing of intermediate files; no leftover stale artifacts.
- **No browser available** → irrelevant to the skill (it emits HTML only); export is the
  user's `Cmd-P` / headless-Chrome step.

## Testing / validation

Build **one tier end-to-end first**, validate its PDF, then derive the second:

1. Generate the **technical** deck from `hub/agents/python/email/`.
2. Export a **real PDF** with headless Chrome (validation only — not part of the shipped
   skill) and confirm **printed-page count == slide count**, nothing clipped or split.
3. Derive the **executive** deck; confirm it is materially shorter and contains **none** of
   the technical-only elements (no code, no architecture diagrams, no methodology).
4. **Negative credibility check:** confirm no invented triage-accuracy / latency numbers
   appear — every such value must be either user-supplied or a labeled placeholder (the
   email package ships no benchmark tables).
5. Re-run on a trivially modified source; confirm the deck updates with no manual edits.

## Acceptance criteria (from the task)

- [ ] Reusable skill in the repo, standard Claude Code skill layout, invocable in a fresh
      session with no setup beyond cloning.
- [ ] Technical tier → self-contained HTML; in print preview/PDF each page is exactly one
      slide, nothing clipped.
- [ ] Executive tier verifiably distinct: materially fewer slides, none of the
      technical-only elements.
- [ ] `@media print` CSS yields one slide per page at consistent size, controls page
      breaks, suppresses browser chrome — verified by inspecting the stylesheet AND
      exporting a real PDF.
- [ ] Skill instructions explicitly require real source artifacts and forbid silent
      fabrication (cite → ask → labeled placeholder).
- [ ] Re-running on a modified source produces an updated deck with no manual editing of
      intermediate files.
