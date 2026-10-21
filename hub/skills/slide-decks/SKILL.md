---
name: slide-decks
description: Build and check PowerPoint slide decks (.pptx) - quarterly or business reviews, executive briefings, pitches, project status, training, research readouts, option comparisons, roadmaps, one-slide frameworks. Use when the user asks for a deck, slides, a presentation, a pitch, or a slide or two from their data. Plans the storyline, builds native editable charts, tables, KPI tiles and diagrams from a short outline, then verifies fit, data, sources and style.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: community
    permissions:
      - program:run:soffice
      - scratch:write
    requirements:
      python: ">=3.10"
      dependencies:
        - python-pptx>=1.0,<2
        - lxml>=5.0
        - pillow>=10.0
        - openpyxl>=3.1,<4
        - pymupdf>=1.24
    tools_required:
      - list_files
      - read_file
      - run_python
      - request_user_input
    pairs_with:
      - report-from-sources
      - office-docs
    tools:
      - name: build_deck
        description: Build a .pptx from a slide outline, then check it.
        parameters:
          outline:
            type: object
            required: true
          output_path:
            type: string
            required: true
          theme:
            type: string
            required: false
            default: ""
          template_path:
            type: string
            required: false
            default: ""
          prefs:
            type: object
            required: false
        returns:
          type: object
      - name: verify_deck
        description: Check a saved .pptx for count, overflow, overlaps, fonts, contrast, charts, sources and style.
        parameters:
          path:
            type: string
            required: true
          expected_slides:
            type: integer
            required: false
            default: 0
          require_native_charts:
            type: boolean
            required: false
            default: true
          require_notes:
            type: boolean
            required: false
            default: false
          render:
            type: boolean
            required: false
            default: true
          render_dir:
            type: string
            required: false
            default: ""
          outline:
            type: object
            required: false
        returns:
          type: object
      - name: add_slide
        description: Add slides to an existing deck in its theme.
        parameters:
          deck_path:
            type: string
            required: true
          slide:
            type: object
            required: true
          position:
            type: integer
            required: false
            default: 0
        returns:
          type: object
      - name: update_slide
        description: Replace one slide of an existing deck.
        parameters:
          deck_path:
            type: string
            required: true
          index:
            type: integer
            required: true
          slide:
            type: object
            required: true
        returns:
          type: object
      - name: list_blueprints
        description: List the deck blueprints and their slots.
        parameters: {}
        returns:
          type: object
      - name: outline_from_blueprint
        description: Make an outline from a blueprint, scaled to an exact slide count.
        parameters:
          name:
            type: string
            required: true
          slide_count:
            type: integer
            required: false
            default: 0
          slots:
            type: object
            required: false
          variant:
            type: string
            required: false
            default: ""
        returns:
          type: object
      - name: deck_outline_from_markdown
        description: Turn Markdown into a build_deck outline.
        parameters:
          markdown:
            type: string
            required: true
        returns:
          type: object
      - name: update_deck_prefs
        description: Save the user's deck style preferences from their feedback.
        parameters:
          feedback:
            type: string
            required: false
            default: ""
          changes:
            type: object
            required: false
        returns:
          type: object
    provenance:
      source: starter-pack
---

# Slide Decks

**Write an outline and call `slide-decks/build_deck`; then call
`slide-decks/verify_deck` and fix what it reports.** Never write python-pptx
code for a deck: the tool measures every text box, builds native editable
charts, and applies the theme, the fonts and the grid. Your job is the story
and the numbers.

## 1. Settle the brief (usually without asking)

- Saved preferences (theme, brand colours, density, notes on/off, template)
  are applied by the tools automatically. Explicit instructions in this
  request win over them; pass those as `prefs` (or `theme`, `template_path`).
- Decide audience, purpose and the **exact slide count** from the request. If
  the user named a number, that is the number — `expected_slides` in
  `verify_deck` checks it. No number: use the blueprint's default.
- Ask **one** short question **only** for a material ambiguity: two
  instructions that conflict, or a deck for an outside audience with no brand
  (template or colours) known. Use `request_user_input` with 3-4 choices, a
  default and `timeout_seconds` of 60 or less; on no answer, go on with the
  default. Never ask about anything a default covers.

  > *"Make a 6-slide pitch for the Lakeside Clinics partnership"* (no
  > template, outside audience) → `request_user_input(message="This deck goes
  > outside the team. Use a brand template, or a built-in theme?", choices=["Light
  > corporate theme", "Minimal theme", "Dark theme", "I'll give a template
  > path"], default_if_no_response="Light corporate theme", timeout_seconds=45)`.
  > *"Turn these notes into slides for our team meeting"* → no question.

## 2. Plan the storyline

Call `list_blueprints`, pick the closest, and call
`outline_from_blueprint(name, slide_count=N)`. It returns an outline scaled to
exactly N slides, with a `guidance` line per slot and TODO markers in every
slot still to fill.

| The user wants | Blueprint |
|---|---|
| Quarterly / monthly / annual review, QBR, KPI review | `quarterly_review` |
| Executive summary, briefing, board update | `executive_summary` |
| Pitch, proposal, investor or sales deck | `pitch` |
| Project or programme status, steering committee | `project_status` |
| Training, tutorial, explainer, onboarding | `training` |
| Research, survey or analysis readout | `research_findings` |
| Choosing between options, recommendation | `decision` |
| Roadmap, plan, timeline, launch plan | `roadmap` |
| One slide: 2x2, pyramid, cycle, process, layer stack, canvas | `one_slide_framework` (`variant`) |

Rules the verifier enforces (limits follow the user's density preference):

- **Action titles**: each title is the slide's takeaway as a sentence, at most
  ~15 words, no full stop ("Revenue grew 12% on West demand", not
  "Revenue"). Read the titles alone: they must tell the whole story.
- **One message per slide**; at most 6 bullets of at most 12 words, one level
  of sub-bullets, about 60 words of body text (30 on an executive summary).
  Detail goes in `notes` (speaker notes) or an appendix.
- Answer first: the executive summary states the conclusions the later slides
  prove. Decks over 10 slides get section dividers; end with next steps
  (action, owner, date).
- Numbers carry units and periods ("$4.2M, Q3 2026").

## 3. Gather content, with sources

Load `report-from-sources` for any deck built from the user's files: find them
with `list_files`, read documents in full with `read_file`, keep the key-facts
ledger, and compute every number with `run_python`.
A chart-data tool result `{"categories", "series": {name: values}, "source"}`
drops straight into a chart slide's `data`. Every chart, table and KPI slide
needs `source` ("sales_2026.xlsx, Jan-Sep 2026"); `verify_deck` fails without
one.

## 4. Build

Fill the slots and call `build_deck(outline, output_path)` with an absolute
path in the user's folder. A compact outline:

```json
{"slides": [
  {"type": "title", "title": "Northwind Q3 review", "subtitle": "Operations | October 2026"},
  {"type": "bullets", "kind": "executive_summary", "title": "Q3 revenue grew 12% as the West launch landed",
   "bullets": ["Revenue $12.4M, +12% vs Q2", "West +37% year on year", ["Two new distributors"], "Churn down to 2.1%"],
   "notes": "Lead with West; East is flat by design."},
  {"type": "chart", "title": "West drove most of the quarter's growth", "chart_type": "column",
   "categories": ["Q3 25", "Q4 25", "Q1 26", "Q2 26", "Q3 26"],
   "series": {"West": [4.1, 4.3, 4.6, 5.0, 5.6], "East": [3.9, 3.8, 4.0, 4.0, 4.1]},
   "y_title": "Revenue ($M)", "number_format": "0.0", "highlight": "West",
   "bullets": ["West +37% in a year"], "source": "sales.xlsx, Q3 2025-Q3 2026"},
  {"type": "kpi", "title": "Every headline metric moved the right way", "source": "metrics.xlsx, Q3 2026",
   "metrics": [{"label": "Revenue", "value": 12.4, "unit": "M", "format": "$0.0", "delta": "+12% vs Q2"},
               {"label": "Churn", "value": 0.021, "format": "0.0%", "delta": "-0.4 pts", "good": "down"}]},
  {"type": "diagram", "diagram": "process", "title": "Onboarding takes five steps",
   "items": [{"title": "Sign up", "text": "Web form"}, {"title": "Verify", "text": "ID check in 24h"}]},
  {"type": "table", "title": "Three regions beat plan; North missed by 6%", "source": "plan.xlsx, Q3 2026",
   "columns": ["Region", "Plan ($M)", "Actual ($M)"], "rows": [["West", 5.0, 5.6], ["North", 1.8, 1.69]]}
]}
```

Other slide types: `section`, `agenda{items}`, `two_column{left, right}`,
`quote{quote, attribution}`, `image{path, alt}` (alt text is required),
`timeline{milestones: [{date, title}]}` or `{periods, lanes: [{name, items:
[{label, start, end}]}]}`. Charts: `column`, `bar` (ranked, sorted), `line`
(time series), `area`, `pie`/`doughnut` (up to 4-5 parts of one whole),
`scatter`, `combo` (a series with `"type": "line", "axis": "secondary"`), or
`auto` to choose from the data. Diagrams: `process`, `flow` (`items` with
`id` + `edges`), `cycle`, `pyramid`, `matrix` (4 quadrants, `x_axis`,
`y_axis`, optional `points`), `grid` (cells with `row`/`col`/spans), `boxes`,
`tree` (`parent`), `stack`. `**bold**` inside any text marks emphasis.

- **Big decks in steps**: build the first 6-8 slides, then
  `add_slide(deck_path, {"slides": [...]})` a few at a time, so no single call
  is long. `update_slide(deck_path, index, slide)` replaces one slide.
- An `error` result lists every problem in the outline at once; fix them all
  and call again. Warnings name the slide and say what to shorten.
- Markdown from the user: `deck_outline_from_markdown(markdown)` first.

## 5. Verify and fix

`verify_deck(path, expected_slides=N)` checks the saved file: slide count,
text overflow (measured, and rendered with LibreOffice when installed),
overlaps, margins, font sizes, contrast, titles, words and bullets per slide,
chart values against the data, pictures used instead of charts, missing
sources, alt text, notes (`require_notes=True` when the user wants a script),
colours and fonts outside the theme, and TODO slots left unfilled. Every
issue has a `fix`. Fix all errors, rebuild (or `update_slide`), and verify
again until `status` is `pass`. Warnings are worth fixing too: they lower the
`style_score`. Pass `render_dir` to get slide PNGs if you can look at images.

## 6. Report

Give the path, then one line per slide from `slides_overview` (title, what is
on it, its source), the verify status, and anything you could not do. Then
offer the `style_options` from the build result in one line ("Want it in the
dark theme, with less text, or with speaker notes?"). When the user answers
with style feedback ("less text", "use our blue #1F4E79", "no notes"), call
`update_deck_prefs(feedback=...)`, apply it to this deck, and say it is saved
for next time (or, if `saved` is false, that this chat does not save it).

## When the tools are not available

If `slide-decks/build_deck` is not offered, run the same engine through
`run_python` with this skill's folder on the path:

```python
import sys; sys.path.insert(0, r"<this skill's folder>")
from slidedeck.build import build_deck
from slidedeck.verify import verify_deck
print(build_deck(outline, r"C:\Users\me\Documents\q3_review.pptx", verify=True))
```

`office-docs` covers Word and Excel files and the manual python-pptx route.
