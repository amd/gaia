# GAIA Presentation Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Post-implementation revision (2026-06-25):** This plan was executed as a single
> `gaia-presentation-builder` skill (one SKILL.md + `assets/` + `reference/`), then split per
> review into **two skills** — `gaia-technical-presentation` and `gaia-executive-presentation`
> — sharing a non-skill `gaia-presentation-assets/` directory (the design system). The output
> subfolder was renamed `decks/` → `presentations/`, and each skill's done-checklist gained a
> "does the presentation satisfy the user's stated goal?" item. The task code/assets below are
> unchanged in substance — only the directory layout and the per-skill split differ. See the
> design spec's Revision section for the authoritative end state.

**Goal:** Build a reusable Claude Code skill, `gaia-presentation-builder`, that turns a source document into two-tier (technical/executive) slide decks as self-contained HTML that prints to PDF one-slide-per-page.

**Architecture:** A prompt-driven skill (`SKILL.md` instructs Claude through a read → inventory → ask → select → emit pipeline) backed by deterministic presentation assets (`deck.css` with a `@media print` profile, base64-embedded brand fonts, a slide-pattern library, and a tiny print-disabled viewer). Slide *content selection* is the model's credibility judgment; *layout/print* is fixed CSS the generated HTML inlines verbatim.

**Tech Stack:** HTML5 + CSS (CSS custom properties, `@media print`/`@page`), vanilla inlined JS (~30 lines), Python 3 (one build-time font-extraction script using only the stdlib), headless Chrome for PDF validation. No runtime/CDN dependencies in output.

## Global Constraints

- Skill dir: `.claude/skills/gaia-presentation-builder/`; frontmatter `name: gaia-presentation-builder`.
- Output HTML is **fully self-contained**: all CSS, fonts (base64), SVG, and JS inline. No `http(s)://`, no CDN, no external file refs in generated decks.
- Brand fonts **Archivo** (display) + **Hanken Grotesk** (body) embedded as base64 `woff2`; code uses a system monospace stack (no embedded mono).
- Palette tokens (verbatim): `--brand:#ed1c24` (AMD red, brand mark only), `--c1:#9c7518` (gold accent, light), `--bg:#fbfaf8`, `--surface:#ffffff`, `--ink:#15140f`, `--soft:#6a655a`, `--line:#e6e2d8`. Dark theme: `--bg:#0b0b0d`, `--surface:#141417`, `--ink:#edebe3`, `--c1:#c9a24a`.
- Print: exactly **one slide per printed page**, consistent size, no clipping/splitting, browser chrome (header/footer/URL) suppressed.
- Credibility contract: **cite-from-source → else ask the user → else loud, labeled placeholder.** Silent fabrication (an invented number presented as real) is forbidden. Placeholders are tinted `⚠ PLACEHOLDER — provide value` chips plus a deck-level `DRAFT — contains placeholders` banner; all placeholders are reported to the user at the end.
- Tiers differ in *kind*: technical = architecture/methodology/contract/metrics, code + inline-SVG diagrams allowed; executive = materially fewer slides, impact/timeline/risk/cost, **no code blocks, no architecture diagrams, no methodology sections**.
- Output path: `<source-dir>/decks/<source-stem>.<tier>.html` — deterministic, overwrite-in-place, no timestamps/random IDs.
- No Claude/AI attribution in any artifact (commits, files, comments).
- Commit messages: conventional-commits; no AI attribution trailer.

**First test source:** `hub/agents/python/email/` (real artifacts: `openapi.email.json`, `specification.html`, `gaia-agent.yaml`, packaging docs, tests; **no** committed benchmark tables — so quantitative slides must be user-supplied or labeled placeholders).

**Reference template (visual identity, not a runtime):** `/Users/tomasz/Public/gaia-roadmap.html`. Its real markup is JSON inside its `<script type="__bundler/template">` tag; its fonts are base64 `woff2` in its `<script type="__bundler/manifest">` tag. We reuse the look and extract the fonts; we drop its `deck-stage` JS runtime and Google Fonts CDN.

---

## File Structure

```
.claude/skills/gaia-presentation-builder/
├── SKILL.md                 # Task 1 — frontmatter (trigger desc) + pipeline + contract + tier rules
├── assets/
│   ├── deck.css             # Task 3 — tokens, slide shell, placeholder/banner, @media print
│   ├── deck-viewer.js       # Task 3 — ~30-line inlined viewer (arrow nav + counter), print-disabled
│   ├── fonts.css            # Task 2 — base64 Archivo + Hanken Grotesk @font-face blocks
│   └── slide-blocks.html    # Task 4 — slide pattern library (title, metric, chart, quote, timeline, close, placeholder, banner)
├── tools/
│   └── build_fonts.py       # Task 2 — extracts fonts.css from the reference template manifest
└── reference/
    ├── example-technical.html   # Task 5 — worked example from the email package
    └── example-executive.html   # Task 6 — derived executive tier
```

Verification helpers used throughout (macOS):
- Render check: `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --screenshot=/tmp/out.png --window-size=1280,720 <file>`
- PDF export: `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --print-to-pdf=/tmp/out.pdf --no-pdf-header-footer <file>`
- PDF page count: `mdls -name kMDItemNumberOfPages /tmp/out.pdf` (macOS native; Spotlight reads PDF metadata). Alt: `python3 -c "import sys;d=open(sys.argv[1],'rb').read();print(d.count(b'/Type /Page')-d.count(b'/Type /Pages'))" /tmp/out.pdf`.

---

### Task 1: Skill scaffold + SKILL.md

**Files:**
- Create: `.claude/skills/gaia-presentation-builder/SKILL.md`

**Interfaces:**
- Consumes: nothing (entry point).
- Produces: the skill's invocation contract every later asset serves — invocation `gaia-presentation-builder <source-path> [--tier technical|executive]`; pipeline steps; the placeholder report format.

- [ ] **Step 1: Write the failing test (trigger-description lint)**

Create `/tmp/check_skill.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
F=.claude/skills/gaia-presentation-builder/SKILL.md
test -f "$F" || { echo "FAIL: SKILL.md missing"; exit 1; }
# Frontmatter name must be the kebab slug
grep -qE '^name: gaia-presentation-builder$' "$F" || { echo "FAIL: name slug"; exit 1; }
# Description must carry trigger words so the skill auto-fires
for w in presentation deck slides technical executive PDF; do
  grep -qiE "description:.*" "$F" && grep -qi "$w" <(sed -n '/^description:/p' "$F") || { echo "FAIL: trigger word '$w' missing from description"; exit 1; }
done
# Credibility contract + tier prohibitions present in body
grep -qi 'cite-from-source' "$F" || { echo "FAIL: credibility contract"; exit 1; }
grep -qi 'PLACEHOLDER' "$F" || { echo "FAIL: placeholder rule"; exit 1; }
grep -qi 'no code blocks' "$F" || { echo "FAIL: executive prohibitions"; exit 1; }
echo "PASS"
```
Run: `bash /tmp/check_skill.sh`
Expected: `FAIL: SKILL.md missing`

- [ ] **Step 2: Write SKILL.md**

Create `.claude/skills/gaia-presentation-builder/SKILL.md`:
```markdown
---
name: gaia-presentation-builder
description: >-
  Generate a two-tier slide deck (technical + executive) from a source document as
  self-contained HTML that prints cleanly to PDF (one slide per page). Use whenever the
  user asks to make a presentation, build a deck, turn a README/spec/design doc into
  slides, create a pitch deck, or produce technical or executive slides from a document.
  Output is offline HTML with an @media print profile; no PPTX/Keynote/Google Slides.
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

Resolve every slide value in this order: **cite from source → else ask the user → else
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

## Checklist before reporting done

- [ ] One self-contained HTML file per tier; no external refs (`grep -c 'https\?://' ` is 0).
- [ ] Executive tier: no `<pre>`/code blocks, no architecture diagram, no methodology slide;
      fewer slides than technical.
- [ ] Every quantitative value is sourced, user-supplied, or a labeled placeholder.
- [ ] Placeholder list reported to the user.
- [ ] (If a browser is available) printed-page count == slide count, nothing clipped.
```

- [ ] **Step 3: Run the lint to verify it passes**

Run: `bash /tmp/check_skill.sh`
Expected: `PASS`

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/gaia-presentation-builder/SKILL.md
git commit -m "feat(skill): scaffold gaia-presentation-builder SKILL.md with pipeline + credibility contract"
```

---

### Task 2: Embedded brand fonts (`fonts.css` + extraction script)

**Files:**
- Create: `.claude/skills/gaia-presentation-builder/tools/build_fonts.py`
- Create: `.claude/skills/gaia-presentation-builder/assets/fonts.css` (generated)

**Interfaces:**
- Consumes: the reference template at `/Users/tomasz/Public/gaia-roadmap.html` (build-time only).
- Produces: `fonts.css` defining `@font-face` for families `Archivo` and `Hanken Grotesk`, each `src: url(data:font/woff2;base64,…)`. Later tasks inline this file verbatim.

- [ ] **Step 1: Write the extraction script**

Create `.claude/skills/gaia-presentation-builder/tools/build_fonts.py`:
```python
#!/usr/bin/env python3
"""Extract Archivo + Hanken Grotesk @font-face blocks from the reference template
bundle and emit a self-contained assets/fonts.css with base64 woff2 data URIs.

The template stores font bytes in its <script type="__bundler/manifest"> tag (base64,
optionally gzip-compressed) keyed by uuid, and references them as url("<uuid>") inside
@font-face blocks in its <script type="__bundler/template"> tag.
"""
import base64
import gzip
import json
import re
import sys
from pathlib import Path

KEEP_FAMILIES = {"Archivo", "Hanken Grotesk"}  # brand identity; code uses system mono

def main() -> int:
    template = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/Users/tomasz/Public/gaia-roadmap.html")
    out = Path(__file__).resolve().parent.parent / "assets" / "fonts.css"

    html = template.read_text(encoding="utf-8")
    manifest = json.loads(re.search(r'<script type="__bundler/manifest">(.*?)</script>', html, re.S).group(1))
    tpl = json.loads(re.search(r'<script type="__bundler/template">(.*?)</script>', html, re.S).group(1))

    blocks = re.findall(r'@font-face\s*\{.*?\}', tpl, re.S)
    if not blocks:
        print("FAIL: no @font-face blocks found", file=sys.stderr)
        return 1

    emitted, seen_uuids = [], set()
    for block in blocks:
        fam_m = re.search(r"font-family:\s*'([^']+)'", block)
        uuid_m = re.search(r'src:\s*url\("([^"]+)"\)', block)
        if not fam_m or not uuid_m:
            continue
        family, uuid = fam_m.group(1), uuid_m.group(1)
        if family not in KEEP_FAMILIES or uuid not in manifest:
            continue
        entry = manifest[uuid]
        raw = base64.b64decode(entry["data"])
        if entry.get("compressed"):
            raw = gzip.decompress(raw)
        b64 = base64.b64encode(raw).decode("ascii")
        block = block.replace(f'url("{uuid}")', f'url(data:font/woff2;base64,{b64})')
        emitted.append(block)
        seen_uuids.add(uuid)

    if not emitted:
        print("FAIL: no Archivo/Hanken faces embedded", file=sys.stderr)
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)
    header = "/* Generated by tools/build_fonts.py — Archivo + Hanken Grotesk, base64 woff2, offline. */\n"
    out.write_text(header + "\n".join(emitted) + "\n", encoding="utf-8")
    kb = out.stat().st_size / 1024
    print(f"PASS: wrote {out} — {len(emitted)} faces, {len(seen_uuids)} unique fonts, {kb:.0f} KB")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run the script to generate fonts.css**

Run: `python3 .claude/skills/gaia-presentation-builder/tools/build_fonts.py`
Expected: `PASS: wrote …/assets/fonts.css — N faces, M unique fonts, KKK KB` (N≥8, size well under 400 KB).

- [ ] **Step 3: Verify the output is offline and well-formed**

Run:
```bash
F=.claude/skills/gaia-presentation-builder/assets/fonts.css
grep -c 'https\?://' "$F"           # expect 0
grep -c "font-family: 'Archivo'" "$F"        # expect >=1
grep -c "font-family: 'Hanken Grotesk'" "$F" # expect >=1
grep -c 'data:font/woff2;base64,' "$F"       # expect == number of faces
```
Expected: first line `0`; Archivo and Hanken counts ≥1; data-URI count equals the face count from Step 2.

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/gaia-presentation-builder/tools/build_fonts.py .claude/skills/gaia-presentation-builder/assets/fonts.css
git commit -m "feat(skill): embed Archivo + Hanken Grotesk as offline base64 fonts"
```

---

### Task 3: `deck.css` (design system + print profile) and `deck-viewer.js`

**Files:**
- Create: `.claude/skills/gaia-presentation-builder/assets/deck.css`
- Create: `.claude/skills/gaia-presentation-builder/assets/deck-viewer.js`

**Interfaces:**
- Consumes: `fonts.css` font-family names (`Archivo`, `Hanken Grotesk`).
- Produces: the class contract slide-blocks (Task 4) and examples (Tasks 5–6) rely on:
  `.deck`, `.slide`, `.slide--surface`, `.tab`, `.eyebrow`, `.lead`, `.metric`, `.metric .n`,
  `.chips`, `.chip`, `.placeholder`, `.draft-banner`, `.pageno`, `.theme-toggle`, theme via
  `data-theme` on `<html>`; viewer reads `.slide` children of `.deck`.

- [ ] **Step 1: Write a render test fixture**

Create `/tmp/deck_probe.html` that links nothing external but inlines the two assets around two slides, so we can render-check:
```bash
mkdir -p /tmp/dp
cp .claude/skills/gaia-presentation-builder/assets/deck.css /tmp/dp/ 2>/dev/null || true
```
(Real assertion is Step 4; this step just establishes the probe path.)

- [ ] **Step 2: Write deck.css**

Create `.claude/skills/gaia-presentation-builder/assets/deck.css`:
```css
/* GAIA Presentation Builder — design system + print profile. Inlined verbatim into decks. */
:root, [data-theme="light"]{
  --bg:#fbfaf8; --surface:#ffffff; --ink:#15140f; --soft:#6a655a; --faint:#9a9488;
  --line:#e6e2d8; --line2:#d9d4c7;
  --brand:#ed1c24;  /* AMD red — brand mark only */
  --c1:#9c7518;     /* gold accent */
  --warn:#b8860b; --warn-bg:#fff6e0; --warn-line:#e6c869;
  --mono: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace;
}
[data-theme="dark"]{
  --bg:#0b0b0d; --surface:#141417; --ink:#edebe3; --soft:#9b988e; --faint:#6f6c63;
  --line:#26261f; --line2:#37362c; --brand:#ed1c24; --c1:#c9a24a;
  --warn:#e0bf72; --warn-bg:#2a2410; --warn-line:#5c4d1f;
}
*{ margin:0; padding:0; box-sizing:border-box; }
html,body{ background:var(--bg); color:var(--ink); font-family:'Hanken Grotesk',-apple-system,BlinkMacSystemFont,sans-serif; }
.deck{ display:flex; flex-direction:column; align-items:center; gap:28px; padding:28px 0; }

/* ── Slide surface: fixed 16:9 on screen ── */
.slide{
  position:relative; width:1280px; height:720px; flex:none; overflow:hidden;
  background:var(--bg); color:var(--ink);
  display:flex; flex-direction:column;
  border:1px solid var(--line); border-radius:10px;
  box-shadow:0 6px 24px rgba(20,18,12,.08);
}
.slide--surface{ background:var(--surface); }

/* ── Header tab ── */
.tab{ height:64px; flex:none; display:flex; align-items:center; justify-content:space-between;
  padding:0 64px; border-bottom:1px solid var(--line); }
.tab .mark{ display:flex; align-items:center; gap:10px; font-family:'Archivo',sans-serif; font-weight:800; letter-spacing:.04em; }
.tab .chev{ width:1.4em; height:1em; color:var(--c1); }
.tab .meta{ color:var(--soft); font-size:15px; }

/* ── Body ── */
.body{ flex:1; display:flex; flex-direction:column; justify-content:center; padding:48px 64px; gap:18px; }
.body.center{ align-items:flex-start; }
.eyebrow{ display:inline-flex; align-items:center; gap:10px; color:var(--c1); font-weight:700;
  text-transform:uppercase; letter-spacing:.12em; font-size:14px; }
.eyebrow .bar{ width:26px; height:3px; background:var(--c1); border-radius:2px; }
h1{ font-family:'Archivo',sans-serif; font-weight:800; font-size:62px; line-height:1.04; letter-spacing:-.02em; }
h2{ font-family:'Archivo',sans-serif; font-weight:700; font-size:40px; line-height:1.1; letter-spacing:-.01em; }
.lead{ font-size:22px; line-height:1.5; color:var(--ink); max-width:64ch; }
.lead.sm{ font-size:18px; color:var(--soft); }
.c1{ color:var(--c1); }
ul.points{ list-style:none; display:flex; flex-direction:column; gap:14px; }
ul.points li{ position:relative; padding-left:28px; font-size:20px; line-height:1.4; }
ul.points li::before{ content:""; position:absolute; left:0; top:.55em; width:10px; height:10px;
  border-right:2.5px solid var(--c1); border-top:2.5px solid var(--c1); transform:rotate(45deg); }

/* ── Metric / KPI ── */
.metrics{ display:flex; gap:28px; flex-wrap:wrap; }
.metric{ flex:1; min-width:200px; border:1px solid var(--line); border-radius:10px; padding:24px; background:var(--surface); }
.metric .n{ font-family:'Archivo',sans-serif; font-weight:800; font-size:46px; line-height:1; color:var(--ink); }
.metric .k{ margin-top:10px; color:var(--soft); font-size:15px; text-transform:uppercase; letter-spacing:.08em; }
.metric .src{ margin-top:8px; color:var(--faint); font-size:12px; }

/* ── Chips ── */
.chips{ display:flex; gap:18px; flex-wrap:wrap; }
.chip{ border:1px solid var(--line); border-radius:10px; padding:18px 22px; background:var(--surface); }
.chip .k{ color:var(--c1); font-size:13px; text-transform:uppercase; letter-spacing:.08em; font-weight:700; }
.chip .n{ font-family:'Archivo',sans-serif; font-weight:700; font-size:22px; margin-top:4px; }
.chip .d{ color:var(--soft); font-size:14px; margin-top:4px; }

/* ── Code / contract (technical tier only) ── */
pre.code{ font-family:var(--mono); font-size:15px; line-height:1.5; background:var(--surface);
  border:1px solid var(--line); border-radius:8px; padding:18px 20px; overflow:hidden; white-space:pre-wrap; }

/* ── Loud placeholder + DRAFT banner ── */
.placeholder{ display:inline-flex; align-items:center; gap:6px; font-weight:700;
  color:var(--warn); background:var(--warn-bg); border:1.5px dashed var(--warn-line);
  border-radius:6px; padding:2px 10px; }
.placeholder::before{ content:"⚠ "; }
.draft-banner{ position:absolute; top:0; left:0; right:0; height:30px; z-index:5;
  display:flex; align-items:center; justify-content:center; gap:8px;
  background:var(--warn-bg); color:var(--warn); border-bottom:1.5px dashed var(--warn-line);
  font-size:13px; font-weight:700; letter-spacing:.06em; text-transform:uppercase; }
.slide.has-draft .tab{ margin-top:30px; }

/* ── Screen chrome (hidden in print) ── */
.theme-toggle{ position:fixed; top:16px; right:16px; z-index:50; cursor:pointer;
  border:1px solid var(--line); background:var(--surface); color:var(--ink);
  border-radius:20px; padding:6px 14px; font-size:13px; display:flex; gap:8px; align-items:center; }
.pageno{ position:absolute; bottom:18px; right:24px; color:var(--faint); font-size:13px; font-variant-numeric:tabular-nums; }

/* ── PRINT: one slide per page, no chrome ── */
@media print{
  @page{ size:landscape; margin:0; }
  html,body{ background:#fff; }
  .deck{ display:block; padding:0; gap:0; }
  .slide{
    width:100%; height:auto; min-height:100vh;
    border:0; border-radius:0; box-shadow:none;
    break-inside:avoid; break-after:page; page-break-after:always;
  }
  .slide:last-child{ break-after:auto; page-break-after:auto; }
  .theme-toggle, .pageno{ display:none !important; }
  /* keep the DRAFT banner — it must survive to the PDF */
}
```

- [ ] **Step 3: Write deck-viewer.js**

Create `.claude/skills/gaia-presentation-builder/assets/deck-viewer.js`:
```javascript
/* Screen-only viewer: arrow-key navigation + page counter. Disabled under print. */
(function () {
  var root = document.documentElement;
  var KEY = 'gaia-deck-theme';
  try { root.setAttribute('data-theme', localStorage.getItem(KEY) || 'light'); } catch (e) {}
  var toggle = document.querySelector('.theme-toggle');
  if (toggle) toggle.addEventListener('click', function () {
    var t = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', t);
    try { localStorage.setItem(KEY, t); } catch (e) {}
  });
  var slides = Array.prototype.slice.call(document.querySelectorAll('.deck > .slide'));
  slides.forEach(function (s, i) {
    var p = document.createElement('div');
    p.className = 'pageno';
    p.textContent = String(i + 1).padStart(2, '0') + ' / ' + String(slides.length).padStart(2, '0');
    s.appendChild(p);
  });
  var cur = 0;
  function go(i) {
    cur = Math.max(0, Math.min(slides.length - 1, i));
    slides[cur].scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  document.addEventListener('keydown', function (e) {
    if (e.key === 'ArrowRight' || e.key === 'PageDown') { go(cur + 1); e.preventDefault(); }
    else if (e.key === 'ArrowLeft' || e.key === 'PageUp') { go(cur - 1); e.preventDefault(); }
  });
})();
```

- [ ] **Step 4: Render-verify both assets together (no external refs, prints to N pages)**

Run:
```bash
D=.claude/skills/gaia-presentation-builder/assets
cat > /tmp/probe.html <<HTML
<!doctype html><html lang="en"><head><meta charset="utf-8">
<style>$(cat $D/fonts.css)
$(cat $D/deck.css)</style></head><body>
<button class="theme-toggle">Theme</button>
<div class="deck">
 <section class="slide slide--surface"><header class="tab"><div class="mark">GAIA</div><div class="meta">Probe</div></header><div class="body center"><div class="eyebrow"><span class="bar"></span>Eyebrow</div><h1>Slide <span class="c1">one</span></h1><p class="lead">Lead paragraph.</p></div></section>
 <section class="slide"><header class="tab"><div class="mark">GAIA</div><div class="meta">Probe</div></header><div class="body"><h2>Slide two</h2><div class="metrics"><div class="metric"><div class="n">42</div><div class="k">things</div></div></div></div></section>
</div>
<script>$(cat $D/deck-viewer.js)</script></body></html>
HTML
grep -c 'https\?://' /tmp/probe.html   # expect 0
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$CHROME" --headless=new --disable-gpu --print-to-pdf=/tmp/probe.pdf --no-pdf-header-footer /tmp/probe.html 2>/dev/null
mdls -name kMDItemNumberOfPages /tmp/probe.pdf
```
Expected: first `grep` prints `0`; `mdls` prints `kMDItemNumberOfPages = 2` (two slides → two pages).

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/gaia-presentation-builder/assets/deck.css .claude/skills/gaia-presentation-builder/assets/deck-viewer.js
git commit -m "feat(skill): deck.css design system with one-slide-per-page print profile + screen viewer"
```

---

### Task 4: Slide-pattern library (`slide-blocks.html`)

**Files:**
- Create: `.claude/skills/gaia-presentation-builder/assets/slide-blocks.html`

**Interfaces:**
- Consumes: classes from `deck.css` (Task 3).
- Produces: named, copy-paste slide patterns the pipeline composes from. Each block is wrapped in an HTML comment naming it and noting tier applicability.

- [ ] **Step 1: Write slide-blocks.html**

Create `.claude/skills/gaia-presentation-builder/assets/slide-blocks.html`:
```html
<!--
  GAIA Presentation Builder — slide pattern library.
  Compose decks by copying these <section class="slide"> blocks and filling real content.
  Tier tags: [both] [technical] [executive]. NEVER invent values — use a .placeholder span
  (and add class "has-draft" + a .draft-banner to any slide that contains one).
-->

<!-- BLOCK: title [both] -->
<section class="slide slide--surface">
  <header class="tab"><div class="mark"><svg class="chev" viewBox="0 0 30 20"><path d="M2 2 L12 10 L2 18" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/><path d="M14 2 L24 10 L14 18" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/></svg><span>GAIA</span></div><div class="meta">{{SUBTITLE}}</div></header>
  <div class="body center">
    <div class="eyebrow"><span class="bar"></span>{{EYEBROW}}</div>
    <h1>{{TITLE_HTML}}</h1>
    <p class="lead">{{LEAD}}</p>
  </div>
</section>

<!-- BLOCK: bullets [both] -->
<section class="slide">
  <header class="tab"><div class="mark"><span>GAIA</span></div><div class="meta">{{SECTION}}</div></header>
  <div class="body">
    <div class="eyebrow"><span class="bar"></span>{{EYEBROW}}</div>
    <h2>{{HEADING}}</h2>
    <ul class="points"><li>{{POINT_1}}</li><li>{{POINT_2}}</li><li>{{POINT_3}}</li></ul>
  </div>
</section>

<!-- BLOCK: metrics [both — values MUST be sourced/user-supplied/placeholder] -->
<section class="slide">
  <header class="tab"><div class="mark"><span>GAIA</span></div><div class="meta">{{SECTION}}</div></header>
  <div class="body">
    <div class="eyebrow"><span class="bar"></span>{{EYEBROW}}</div>
    <h2>{{HEADING}}</h2>
    <div class="metrics">
      <div class="metric"><div class="n">{{VALUE}}</div><div class="k">{{LABEL}}</div><div class="src">{{SOURCE_REF}}</div></div>
      <div class="metric"><div class="n"><span class="placeholder">PLACEHOLDER</span></div><div class="k">{{LABEL}}</div><div class="src">provide value</div></div>
    </div>
  </div>
</section>

<!-- BLOCK: chart [technical — inline SVG only, axes/values from source] -->
<section class="slide">
  <header class="tab"><div class="mark"><span>GAIA</span></div><div class="meta">{{SECTION}}</div></header>
  <div class="body">
    <div class="eyebrow"><span class="bar"></span>{{EYEBROW}}</div>
    <h2>{{HEADING}}</h2>
    <svg viewBox="0 0 600 240" width="600" height="240" role="img" aria-label="{{ALT}}">
      <!-- bars: x,width fixed; height/value from source. Replace rects below. -->
      <rect x="40"  y="80"  width="60" height="120" fill="#9c7518"/>
      <rect x="140" y="40"  width="60" height="160" fill="#9c7518"/>
      <line x1="20" y1="200" x2="580" y2="200" stroke="#d9d4c7"/>
    </svg>
  </div>
</section>

<!-- BLOCK: code [technical only — never in executive] -->
<section class="slide">
  <header class="tab"><div class="mark"><span>GAIA</span></div><div class="meta">{{SECTION}}</div></header>
  <div class="body">
    <div class="eyebrow"><span class="bar"></span>{{EYEBROW}}</div>
    <h2>{{HEADING}}</h2>
    <pre class="code">{{CODE_OR_CONTRACT_SNIPPET}}</pre>
  </div>
</section>

<!-- BLOCK: timeline [both] -->
<section class="slide">
  <header class="tab"><div class="mark"><span>GAIA</span></div><div class="meta">{{SECTION}}</div></header>
  <div class="body">
    <div class="eyebrow"><span class="bar"></span>{{EYEBROW}}</div>
    <h2>{{HEADING}}</h2>
    <div class="chips">
      <div class="chip"><div class="k">{{WHEN}}</div><div class="n">{{MILESTONE}}</div><div class="d">{{DETAIL}}</div></div>
    </div>
  </div>
</section>

<!-- BLOCK: quote/outcome [executive-friendly] -->
<section class="slide slide--surface">
  <header class="tab"><div class="mark"><span>GAIA</span></div><div class="meta">{{SECTION}}</div></header>
  <div class="body center">
    <div class="eyebrow"><span class="bar"></span>{{EYEBROW}}</div>
    <h2 style="max-width:24ch">{{OUTCOME_STATEMENT}}</h2>
    <p class="lead sm">{{SUPPORT}}</p>
  </div>
</section>

<!-- BLOCK: close [both] -->
<section class="slide slide--surface">
  <div class="body center">
    <div class="eyebrow"><span class="bar"></span>{{EYEBROW}}</div>
    <h1>{{CLOSING_HTML}}</h1>
    <p class="lead">{{CALL_TO_ACTION}}</p>
  </div>
</section>

<!-- SNIPPET: draft banner — add to <section class="slide has-draft …"> when it holds a placeholder -->
<!-- <div class="draft-banner">Draft — contains placeholders</div> -->
```

- [ ] **Step 2: Verify every block references only defined classes**

Run:
```bash
F=.claude/skills/gaia-presentation-builder/assets/slide-blocks.html
# Each BLOCK comment must precede a .slide section
test "$(grep -c 'BLOCK:' $F)" -ge 7 || { echo FAIL count; exit 1; }
# No external refs
test "$(grep -c 'https\?://' $F)" -eq 0 || { echo FAIL external; exit 1; }
# Placeholder + draft-banner patterns present
grep -q 'class="placeholder"' $F && grep -q 'draft-banner' $F || { echo FAIL placeholder; exit 1; }
echo PASS
```
Expected: `PASS`

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/gaia-presentation-builder/assets/slide-blocks.html
git commit -m "feat(skill): slide-pattern library with tier tags and placeholder/draft-banner blocks"
```

---

### Task 5: Worked example — technical tier from the email package (end-to-end + PDF validation)

**Files:**
- Create: `.claude/skills/gaia-presentation-builder/reference/example-technical.html`
- Read (source): `hub/agents/python/email/openapi.email.json`, `hub/agents/python/email/gaia-agent.yaml`, `hub/agents/python/email/packaging/README.md`, `hub/agents/python/email/specification.html`

**Interfaces:**
- Consumes: `SKILL.md` pipeline + all `assets/`.
- Produces: a self-contained technical deck proving the full pipeline and the print profile.

- [ ] **Step 1: Build the real-artifact inventory from the email package**

Run (capture artifacts; do not fabricate):
```bash
echo "== endpoints ==";  python3 -c "import json;d=json.load(open('hub/agents/python/email/openapi.email.json'));print('\n'.join(sorted(d.get('paths',{}))))"
echo "== title/version =="; python3 -c "import json;d=json.load(open('hub/agents/python/email/openapi.email.json'));print(d['info'].get('title'),d['info'].get('version'))"
echo "== manifest =="; sed -n '1,40p' hub/agents/python/email/gaia-agent.yaml
echo "== packaging readme =="; sed -n '1,60p' hub/agents/python/email/packaging/README.md
```
Record each artifact + its source location into an inventory note (in the session, not a repo file). Expected: a list of real endpoints, the API title/version, the manifest's declared capabilities — and the explicit observation that **no accuracy/latency numbers exist** in these sources.

- [ ] **Step 2: Compose example-technical.html**

Author `.claude/skills/gaia-presentation-builder/reference/example-technical.html` by:
1. Opening `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>…</title><style>` then **inlining `fonts.css` then `deck.css` verbatim**, closing `</style></head><body><div class="deck">`.
2. Composing **8–14 slides** from `slide-blocks.html`, filled ONLY from the Step-1 inventory: title; what-it-is; the REST contract (a `code` block with 2–3 real endpoints from `openapi.email.json`); architecture (inline-SVG of triage→draft→send pipeline, labels from the manifest); capabilities (bullets from `gaia-agent.yaml`); packaging/release (from packaging README); a **metrics** slide whose accuracy/latency values are `⚠ PLACEHOLDER` (mark the slide `has-draft` + add the `.draft-banner`); close.
3. Inlining `deck-viewer.js` in a `<script>` before `</body></html>`.

(The composing agent writes the literal HTML; values are copied from Step 1, never invented.)

- [ ] **Step 3: Verify self-contained + executive-prohibited-elements-allowed-here**

Run:
```bash
F=.claude/skills/gaia-presentation-builder/reference/example-technical.html
grep -c 'https\?://' "$F"                 # expect 0 (fully offline)
grep -c '<section class="slide' "$F"       # expect 8..14
grep -c 'class="placeholder"' "$F"         # expect >=1 (no real metrics in source)
grep -c 'draft-banner' "$F"                # expect >=1
```
Expected: `0`; a slide count in 8–14; ≥1 placeholder; ≥1 draft banner.

- [ ] **Step 4: Export a real PDF and confirm one slide per page**

Run:
```bash
F=.claude/skills/gaia-presentation-builder/reference/example-technical.html
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$CHROME" --headless=new --disable-gpu --print-to-pdf=/tmp/tech.pdf --no-pdf-header-footer "$F" 2>/dev/null
SLIDES=$(grep -c '<section class="slide' "$F")
PAGES=$(mdls -name kMDItemNumberOfPages /tmp/tech.pdf | sed 's/[^0-9]//g')
echo "slides=$SLIDES pages=$PAGES"
test "$SLIDES" -eq "$PAGES" || { echo "FAIL: page count != slide count"; exit 1; }
echo PASS
```
Expected: `slides=N pages=N` then `PASS`. If pages > slides, a slide is overflowing — reduce its content/scale; do not change the print rules.

- [ ] **Step 5: Visually confirm nothing is clipped (screenshot the PDF's slides)**

Run:
```bash
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$CHROME" --headless=new --disable-gpu --screenshot=/tmp/tech.png --window-size=1280,720 \
  ".claude/skills/gaia-presentation-builder/reference/example-technical.html" 2>/dev/null
echo "open /tmp/tech.png to confirm the first slide renders fully"
```
Expected: a screenshot exists; the implementer (or reviewer) confirms the first slide is not clipped. (Full per-slide visual proof is captured in Task 7.)

- [ ] **Step 6: Commit**

```bash
git add .claude/skills/gaia-presentation-builder/reference/example-technical.html
git commit -m "feat(skill): technical-tier worked example from the email package (PDF one-slide-per-page verified)"
```

---

### Task 6: Worked example — executive tier (verifiably distinct)

**Files:**
- Create: `.claude/skills/gaia-presentation-builder/reference/example-executive.html`

**Interfaces:**
- Consumes: the same inventory + assets; the technical example as the contrast baseline.
- Produces: an executive deck with materially fewer slides and none of the technical-only elements.

- [ ] **Step 1: Compose example-executive.html**

Author the file the same inlining way (fonts.css + deck.css in `<style>`, viewer before close), but select **different facts**: title (impact-led), the problem/outcome, what it enables (business value, bullets), timeline (chips from any dated milestones or a `⚠ PLACEHOLDER` if none), risk/cost framing, close/CTA. **5–7 slides.** **No `pre.code`, no chart SVG diagram, no methodology slide.** Quantitative values follow the same sourced/asked/placeholder rule.

- [ ] **Step 2: Verify distinctness + prohibitions**

Run:
```bash
T=.claude/skills/gaia-presentation-builder/reference/example-technical.html
E=.claude/skills/gaia-presentation-builder/reference/example-executive.html
TS=$(grep -c '<section class="slide' "$T"); ES=$(grep -c '<section class="slide' "$E")
echo "technical=$TS executive=$ES"
test "$ES" -lt "$TS" || { echo "FAIL: executive not fewer slides"; exit 1; }
test "$(grep -c '<pre class="code"' "$E")" -eq 0 || { echo "FAIL: code block in executive"; exit 1; }
test "$(grep -c '<svg' "$E")" -le 1 || { echo "FAIL: diagram in executive"; exit 1; }   # allow at most the chevron
grep -iq 'methodology' "$E" && { echo "FAIL: methodology in executive"; exit 1; } || true
test "$(grep -c 'https\?://' "$E")" -eq 0 || { echo "FAIL: external ref"; exit 1; }
echo PASS
```
Expected: `technical=N executive=M` with M<N, then `PASS`.

- [ ] **Step 3: Export PDF and confirm one slide per page**

Run:
```bash
E=.claude/skills/gaia-presentation-builder/reference/example-executive.html
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$CHROME" --headless=new --disable-gpu --print-to-pdf=/tmp/exec.pdf --no-pdf-header-footer "$E" 2>/dev/null
SLIDES=$(grep -c '<section class="slide' "$E")
PAGES=$(mdls -name kMDItemNumberOfPages /tmp/exec.pdf | sed 's/[^0-9]//g')
echo "slides=$SLIDES pages=$PAGES"; test "$SLIDES" -eq "$PAGES" || { echo FAIL; exit 1; }; echo PASS
```
Expected: `slides=M pages=M`, `PASS`.

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/gaia-presentation-builder/reference/example-executive.html
git commit -m "feat(skill): executive-tier worked example — fewer slides, no code/diagram/methodology"
```

---

### Task 7: Determinism, negative-fabrication check, and visual proof

**Files:**
- Modify (only if a defect surfaces): any `assets/*` file.

**Interfaces:**
- Consumes: everything above.
- Produces: evidence the acceptance criteria hold; the skill ready to use.

- [ ] **Step 1: Determinism / re-run check**

Re-run the technical example composition mentally/manually against the same inputs to the same output path and confirm byte-stability of the deterministic assets:
```bash
md5 -q .claude/skills/gaia-presentation-builder/assets/deck.css
md5 -q .claude/skills/gaia-presentation-builder/assets/fonts.css
python3 .claude/skills/gaia-presentation-builder/tools/build_fonts.py >/dev/null
md5 -q .claude/skills/gaia-presentation-builder/assets/fonts.css   # must match the line above
```
Expected: the two `fonts.css` md5s match (extraction is deterministic). Output path convention `<source-dir>/decks/<stem>.<tier>.html` documented in SKILL.md — confirm with `grep -n 'decks/' .claude/skills/gaia-presentation-builder/SKILL.md`.

- [ ] **Step 2: Negative-fabrication check**

Run:
```bash
T=.claude/skills/gaia-presentation-builder/reference/example-technical.html
# Any percentage/ms/accuracy figure in the deck must sit inside a .placeholder or be traceable.
grep -oE '[0-9]+(\.[0-9]+)?\s*(%|ms|/100)' "$T" || echo "no bare numeric metrics"
echo "Manually confirm: every numeric KPI is either a .placeholder or copied from openapi.email.json / gaia-agent.yaml."
```
Expected: any accuracy/latency figure is within a `.placeholder` span (source has none). Reviewer confirms no invented metric is presented as real.

- [ ] **Step 3: Capture per-slide visual proof for both tiers**

Run (prints each tier to PDF, renders the PDF pages to PNGs if `pdftoppm` exists, else screenshots the HTML):
```bash
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
for tier in technical executive; do
  F=".claude/skills/gaia-presentation-builder/reference/example-$tier.html"
  "$CHROME" --headless=new --disable-gpu --print-to-pdf="/tmp/$tier.pdf" --no-pdf-header-footer "$F" 2>/dev/null
  echo "$tier: $(mdls -name kMDItemNumberOfPages /tmp/$tier.pdf)"
done
```
Expected: both PDFs exist with the page counts asserted in Tasks 5–6. Surface `/tmp/technical.pdf` and `/tmp/exec.pdf` (and a first-slide PNG) to the user as proof.

- [ ] **Step 4: Final SKILL.md polish + reference pointer**

Add to `SKILL.md` a short "Worked example" line pointing at `reference/example-technical.html` and `reference/example-executive.html` so users can see expected output. Re-run `bash /tmp/check_skill.sh` → `PASS`.

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/gaia-presentation-builder/SKILL.md
git commit -m "docs(skill): point gaia-presentation-builder at worked examples; finalize"
```

---

## Self-Review

**1. Spec coverage**
- Reusable skill, repo conventions, fresh-session invocable → Task 1 (SKILL.md + frontmatter).
- Technical tier self-contained, one-slide-per-page → Tasks 3 (print profile) + 5 (PDF page-count assertion).
- Executive tier verifiably distinct, no code/diagram/methodology → Task 6 (distinctness assertions).
- `@media print` one-per-page, breaks controlled, chrome suppressed, verified by PDF → Task 3 (rules) + Tasks 5/6 (`mdls` page count) + Task 7 (visual proof).
- Instructions require real artifacts, forbid fabrication → Task 1 (contract) + Task 7 (negative check).
- Re-run reflects changes, no manual edits → deterministic output path (Task 1) + Task 7 (determinism check).
- Embedded offline fonts → Task 2.
- Auto-trigger description → Task 1 lint.

**2. Placeholder scan:** No TBD/TODO; every code step contains literal content; the only "placeholders" are the intentional `⚠ PLACEHOLDER` UI feature.

**3. Type/name consistency:** Class names used in slide-blocks (Task 4) and examples (Tasks 5–6) — `.deck`, `.slide`, `.slide--surface`, `.tab`, `.eyebrow`, `.lead`, `.metric .n`, `.chips`, `.chip`, `.placeholder`, `.draft-banner`, `.pageno`, `.theme-toggle`, `has-draft` — all defined in `deck.css` (Task 3). Viewer selector `.deck > .slide` matches the `.deck` wrapper used in examples. `build_fonts.py` emits families `Archivo`/`Hanken Grotesk` consumed by `deck.css` `font-family`.
