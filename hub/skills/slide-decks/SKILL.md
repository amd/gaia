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
            required: false
            default: ""
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
            required: false
            default: ""
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
          expected_slides:
            type: integer
            required: false
            default: 0
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
outline), then check it with `slide-decks/verify_deck`.** The tools fit the
text, build native editable charts and apply the theme. Hand-written
python-pptx gets charts as pictures, text off the slide and the wrong slide
count. Your job is the story and the numbers.

````markdown
# Northwind Q3 2026 review
Leadership team | October 2026
Must show: Revenue $12.4M; West +37%

## Q3 revenue grew 12% as the West launch landed
- Revenue $12.4M, up 12% on Q2
- West +37% year on year
Source: finance_q3.xlsx, Jul-Sep 2026

## West drove most of the quarter's growth
```chart column
y_title: Revenue ($M)
source: sales.xlsx, Q3 2025-Q3 2026
Quarter,West,East
Q3 25,4.1,3.9
Q3 26,5.6,4.1
```

## Support grows from one desk to a 24/7 team
Diagram: process
### Help desk
- Email tickets
### Live chat
- Chat, 8am-6pm
### 24/7 team
- Phone, chat and email
Axis: More channels, staff and hours
````

`# ` is the cover, each `## ` line is one slide titled with its takeaway, `-`
lines are bullets, `Must show:` lists what has to be visible. Three or more
`### ` headings on a slide are drawn as side-by-side boxes, each heading's
bullets inside it. `Diagram: process` draws them as left-to-right stages
(also `cycle`, `pyramid`, `stack`) and `Axis:` names what grows along them.
Use this for stages, steps, levels or options instead of a wall of bullets.
Keep the user's own names for each stage. Under each, write 2-4 bullets of
2-6 words, with no labels like `Focus:`. Use the user's terms in titles
without inventing abbreviations. Keep `Axis:` under 10 words.

## Hard rules

- **Exact slide count.** If the user named a number, build exactly that many
  and pass it as `expected_slides` to the build and verify tools (a one-slide
  request then gets no cover). Otherwise use the blueprint's default.
- **Every must-show item is visible on a slide.** List each number, name,
  event, section and message the request asks for and pass them as
  `required_items`: short facts and names, not the deck's overall message.
  Never move requested content into the notes: cut other words or split. Notes hold extra detail and the spoken script.
- **Every number comes from a source**, with its unit and period. Every
  chart, table and KPI slide names its source.
- **On `status: "error"`**, fix every listed problem and call again. Never
  switch to writing code.

## Procedure

1. **Brief.** Settle audience, purpose and slide count. Saved preferences
   apply automatically; what this request says wins (`prefs`, `theme`,
   `template_path`). Ask **one** short question **only** for a material
   ambiguity, through `request_user_input` with a default.
   Never ask about anything a default covers.
2. **Key facts.** From files: `list_files`, read each document in full with
   `read_file`, compute numbers from data with `run_python`. Keep a ledger
   (fact, value, source) of headline numbers, changes, events and risks.
   Every ledger row goes on a slide and into `required_items`. Several files
   on one topic: use the newest for the requested period.
3. **Storyline.** One message per slide, answer first, end with next steps.
   For a standard deck call `list_blueprints`, then
   `outline_from_blueprint(name, slide_count=N)` and fill every TODO slot.
4. **Build** with Markdown, or `build_deck` with JSON for timelines, flows,
   2x2 matrices and combo charts. Big decks: 6-8 slides first, then
   `add_slide`.
   Leave `output_path` empty unless the user named where to save it.
5. **Check.** The build result's `verify` is the check. Fix every error it
   lists and rebuild (or `update_slide`) until its `status` is `pass`. Call
   `verify_deck(path, expected_slides=N, required_items=[...])` only after
   `add_slide` or `update_slide`, or for a deck you did not build.
6. **Report** the path, one line per slide, the check status and anything not
   done. On style feedback call `update_deck_prefs(feedback=...)` and say
   whether it was saved.

## References (paths relative to this skill's folder)

- `references/markdown-format.md` - read when a slide needs a chart, table,
  KPI tiles, image, quote, two columns or speaker notes.
- `references/outline-json.md` - read before calling `build_deck`,
  `add_slide` or `update_slide` with JSON, or after an outline error.
- `references/storyline.md` - read when picking a blueprint, writing titles,
  or fixing density warnings.
- `references/brief-and-sources.md` - read when unsure whether to ask the
  user, or when building the key-facts ledger.
- `references/without-tools.md` - read only when the slide-decks tools are
  not offered.

`office-docs` covers Word and Excel files.
