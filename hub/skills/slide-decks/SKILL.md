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
          required_items:
            type: array
            required: false
        returns:
          type: object
      - name: build_deck_from_markdown
        description: Build and check a .pptx from Markdown.
        parameters:
          markdown:
            type: string
            required: true
          output_path:
            type: string
            required: true
          theme:
            type: string
            required: false
            default: ""
          prefs:
            type: object
            required: false
          required_items:
            type: array
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

**Do not write python-pptx code. Build the deck with
`slide-decks/build_deck_from_markdown` (or `slide-decks/build_deck` for a JSON
outline), then check it with `slide-decks/verify_deck`.** The tools measure
every text box, build native editable charts, and apply the theme, fonts and
grid. Hand-written python-pptx code gets charts as pictures, text off the
slide and the wrong slide count. Your job is the story and the numbers.

The quickest deck: write Markdown and make one call.

````markdown
# Northwind Q3 2026 review
Leadership team | October 2026
Must show: Revenue $12.4M; 3 site closures; West +37%

## Q3 revenue grew 12% as the West launch landed
- Revenue $12.4M, up 12% on Q2
- West +37% year on year
  - Two new distributors
- 3 site closures, all in the East
Source: finance_q3.xlsx, Jul-Sep 2026
Notes: Lead with West.

## West drove most of the quarter's growth
- West +37% in a year
```chart column
y_title: Revenue ($M)
source: sales.xlsx, Q3 2025-Q3 2026
Quarter,West,East
Q3 25,4.1,3.9
Q1 26,4.6,4.0
Q3 26,5.6,4.1
```

## Every headline metric moved the right way
```kpi
source: metrics.xlsx, Q3 2026
label,value,delta
Revenue ($M),12.4,+12%
Churn,2.1%,-0.4 pts
```
````

`build_deck_from_markdown(markdown=..., output_path=r"C:\Users\me\Documents\q3_review.pptx")`.
`# ` is the cover, every `## ` line is one slide and its title, `-` lines are
bullets (indent for a sub-bullet), `Source:` and `Notes:` lines add the source
line and speaker notes. A ```` ```chart <type> ```` block (column, bar, line,
pie, area, combo...), a ```` ```table ```` block or a ```` ```kpi ```` block
holds `key: value` lines then CSV. `Must show:` lists what has to be visible.

## 1. Settle the brief (usually without asking)

- **List the must-show items first.** Write down every number, name, event,
  section and message the request asks to see. Pass them as `required_items`
  (or a `Must show:` line). `verify_deck` fails if one is missing from the
  slides, or is only in the speaker notes.
- **Everything the request asks to show goes on the slides.** Density limits
  (words and bullets per slide) are soft. When a slide is too full, cut other
  words or split it. Never move requested content into the notes. Notes are
  for extra detail and the spoken script only.
- Decide audience, purpose and the **exact slide count**. If the user named a
  number, that is the number (`expected_slides`). Otherwise use the
  blueprint's default.
- Saved preferences (theme, brand colours, density, notes, template) apply
  automatically. What this request says wins: pass it as `prefs`, `theme` or
  `template_path`.
- Ask **one** short question **only** for a material ambiguity: two
  instructions that conflict, or a deck for an outside audience with no brand
  (template or colours) known. Use `request_user_input` with 3-4 choices, a
  default and `timeout_seconds` of 60 or less. If nobody answers, go on with
  the default. Never ask about anything a default covers.

  > *"Make a 6-slide pitch for the Lakeside Clinics partnership"* (no
  > template, outside audience) → `request_user_input(message="This deck goes
  > outside the team. Use a brand template, or a built-in theme?", choices=["Light
  > corporate theme", "Minimal theme", "Dark theme", "I'll give a template
  > path"], default_if_no_response="Light corporate theme", timeout_seconds=45)`.
  > *"Turn these notes into slides for our team meeting"* → no question.

## 2. Pull the key facts from the sources

When the deck is built from documents or data, do this before writing slides.
Find the files with `list_files`, read each document in full with
`read_file` (follow `next_offset`), and compute numbers from data with
`run_python`. Never retype a number from a page. For **each source**, write a
short ledger:

| Fact | Value | Source |
|---|---|---|
| Headline numbers (totals, rates, counts) | Revenue $12.4M | finance_q3.xlsx |
| Change vs the prior period, as the source states it | +12% vs Q2 | finance_q3.xlsx |
| Notable events and their counts (closures, incidents, launches) | 3 site closures | board_memo.docx |
| Risks, caveats, missed targets | single supplier for part B | board_memo.docx |

Every ledger row goes on a slide, in the source's own framing ("fell 8% to
$4.2M"), and into `required_items`. When several files cover the same thing,
use the newest one for the requested period. `report-from-sources` has the
longer version of this step.

## 3. Plan the storyline

For a standard deck, `list_blueprints` then `outline_from_blueprint(name,
slide_count=N)` gives exactly N slides with a guidance line per slot. Unfilled
slots stay TODO, and `verify_deck` fails on them.

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

- **Action titles**: each title is the slide's takeaway as one sentence, about
  15 words at most, no full stop ("Revenue grew 12% on West demand", not
  "Revenue"). The titles alone should tell the story.
- One message per slide; aim for 6 bullets or fewer, 12 words each, one level
  of sub-bullets.
- Answer first: an executive summary states the conclusions later slides
  prove. Decks over 10 slides get section dividers. End with next steps
  (action, owner, date).
- Numbers carry units and periods ("$4.2M, Q3 2026"). Every chart, table and
  KPI slide names its source and period.

## 4. Build

- Markdown (above) suits most decks. For diagrams, timelines, combo charts or
  blueprint outlines, use `build_deck(outline, output_path)` with JSON. Each
  slide has a `type` and its fields:

```json
{"required_items": ["Revenue $12.4M"], "slides": [
  {"type": "title", "title": "Northwind Q3 review", "subtitle": "October 2026"},
  {"type": "bullets", "title": "Q3 revenue grew 12% to $12.4M", "bullets": ["West +37%", ["two new distributors"]]},
  {"type": "chart", "title": "West drove the growth", "chart_type": "column", "categories": ["Q1", "Q2", "Q3"],
   "series": {"West": [4.6, 5.0, 5.6]}, "y_title": "Revenue ($M)", "source": "sales.xlsx, 2026"},
  {"type": "diagram", "diagram": "process", "title": "Onboarding takes four steps", "items": ["Sign up", "Verify", "Train", "Order"]}
]}
```

  Other types: `section`, `agenda{items}`, `two_column{left, right}`,
  `table{columns, rows}`, `kpi{metrics}`, `quote{quote, attribution}`,
  `image{path, alt}`, `timeline{milestones}` or `{periods, lanes}`. Diagrams:
  `process`, `flow`, `cycle`, `pyramid`, `matrix` (2x2), `grid`, `boxes`,
  `tree`, `stack`. Charts: `column`, `bar`, `line`, `area`, `pie`,
  `doughnut`, `scatter`, `combo`, `auto`.
- **On `status: "error"`**, the `problems` list names every field to fix and
  gives a corrected example for that slide type. Fix them all and call
  again. Do not switch to writing code.
- **Big decks in steps**: build 6-8 slides, then `add_slide(deck_path,
  {"slides": [...]})` a few at a time. `update_slide(deck_path, index,
  slide)` replaces one slide.

## 5. Verify and fix

`verify_deck(path, expected_slides=N, required_items=[...])` checks the saved
file:
- slide count, and every required item visible on a slide
- text overflow (rendered with LibreOffice when it is installed)
- overlaps, fonts, contrast, titles and density
- chart values, pictures used instead of charts
- sources, alt text, notes (`require_notes=True` when a script is wanted)
- TODO slots left unfilled

Fix every error, rebuild (or `update_slide`) and verify again until `status`
is `pass`.

## 6. Report

Give the path, then one line per slide from `slides_overview` (title, what is
on it, its source), the verify status, and anything you could not do. Offer
the `style_options` in one line. When the user gives style feedback ("less
text", "use our blue #1F4E79", "no notes"), call
`update_deck_prefs(feedback=...)`, apply it, and say it is saved (or, if
`saved` is false, that this chat does not save it).

## When the tools are not offered

Run the same engine through `run_python`. Still no hand-written python-pptx:

```python
import sys; sys.path.insert(0, r"<this skill's folder>")
from slidedeck.markdown import outline_from_markdown
from slidedeck.build import build_deck
print(build_deck(outline_from_markdown(open(r"C:\Users\me\deck.md", encoding="utf-8").read()), r"C:\Users\me\deck.pptx"))
```

`office-docs` covers Word and Excel files.
