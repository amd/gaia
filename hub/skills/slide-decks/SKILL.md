---
name: slide-decks
description: Build and check PowerPoint decks (.pptx) - business reviews, executive briefings, pitches, project status, training, research readouts, option comparisons, roadmaps, one-slide frameworks. Use when the user asks for a deck, slides, a presentation, a pitch, a slide or two with or without their data, or an image (PNG/JPEG) of a slide. Builds native editable charts, tables, KPI tiles and diagrams from a short outline, then checks fit, data, sources and style.
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
        - pypdfium2>=5.0.0,<6
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
          expected_slides:
            type: integer
            required: false
            default: 0
          expect:
            type: object
            required: false
        returns:
          type: object
      - name: verify_deck
        description: Check a saved .pptx for count, the charts and tables asked for, overflow, overlaps, fonts, contrast, sources and style.
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
          expect:
            type: object
            required: false
        returns:
          type: object
      - name: export_slide_image
        description: Save a built slide as a PNG (default) or JPEG image.
        parameters:
          deck_path:
            type: string
            required: true
          slide:
            type: integer
            required: false
            default: 1
          image_format:
            type: string
            required: false
            default: png
          output_path:
            type: string
            required: false
            default: ""
          width:
            type: integer
            required: false
            default: 1920
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
          expect:
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
        description: Replace one slide of an existing deck, by its number from 1 (the cover is slide 1).
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
          replace_cover:
            type: boolean
            required: false
            default: false
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
outline).** One call makes the file. Hand-written code gets chart pictures. Your job is the story and the numbers.

````markdown
# Northwind Q3 2026 review
Leadership team | October 2026
Must show: Revenue $12.4M; West +37%

## Q3 revenue grew 12% as the West launch landed
- Revenue $12.4M, up 12% on Q2
- West +37% year on year
Source: finance_q3.xlsx, Jul-Sep 2026
Takeaway: Keep funding West

## West drove most of the quarter's growth
```chart column
y_title: Revenue ($M)
source: sales.xlsx, Q3 2025-Q3 2026
Quarter,West,East
Q3 25,4.1,3.9
Q3 26,5.6,4.1
```

## Onboarding takes three steps
Diagram: process
### Sign up
- Two minutes online
### Verify
- ID check, same day
### First order
- Guided by a specialist
````

`# ` is an optional cover slide, each `## ` line is one slide titled with its
takeaway, `-` lines are bullets, `Takeaway:` the line to act on, `Must show:`
what must be seen. Three or more `### ` headings on a slide are drawn as
side-by-side boxes; `Diagram: process` draws them as stages (also `cycle`,
`pyramid`, `stack`, `timeline`). One slide is just one `## ` block:
`build_deck_from_markdown(markdown="## Title\n- Point\n- Point")`.

## What a build returns

- **Any outline with a slide is built.** `path` is the file. `status` is
  `ok`, or `built_with_issues` when the check found errors in the saved deck.
- **`warnings`** list what was read, drawn or left out differently from what
  you sent, and what to check (slide count, numbers with no source, a theme
  or field that does not exist). Each names the call that fixes it. Make that
  call if the user would want it fixed; otherwise tell the user.
- **`status: "error"`** means no file was made: no slides, a path outside the
  folders this chat may use, or no folder to save in. The error names the
  next call. Never switch to writing code.

## Rules

- **Slide count.** Pass the number the user named as `expected_slides`. It
  counts every slide, the cover included: for one slide send one `## ` block
  and no `# ` cover. A different count is built and reported in `warnings`.
- **Numbers that form a series or a comparison go in a chart**, from CSV
  rows or a file (`data:` + `range:`). When the request asks for charts or
  tables pass `expect={"min_charts": N}`: a deck with fewer fails its check.
- **Slides are numbered from 1; the cover is slide 1.** Use the numbers in
  `slides_built` for `update_slide`.
- **Every must-show item is visible on a slide.** List each number, name,
  event and message the request asks for and pass them as `required_items`.
  Never move requested content into the notes: notes hold extra detail.
- **Only numbers the user gave or a tool returned** (compute with
  `run_python`); data slides name their source. An estimate is labelled
  `Illustrative` on its slide.

## Procedure

1. **Brief.** Settle audience, purpose and slide count. Saved preferences
   apply; this request's `prefs`, `theme`, `template_path` win. Ask **one** short question **only** for a material
   ambiguity, through `request_user_input` with a default.
   Never ask about anything a default covers.
2. **Key facts.** From files: `list_files`, read each document in full with
   `read_file`, compute numbers from data with `run_python`. Keep a ledger
   (fact, value, source); every ledger row goes on a slide and into
   `required_items`.
3. **Storyline.** One message per slide, answer first, end with next steps.
   For a standard deck call `list_blueprints`, then
   `outline_from_blueprint(name, slide_count=N)` and fill every TODO slot.
4. **Build** with Markdown, or `build_deck` with JSON for flows, 2x2
   matrices and combo charts. Leave `output_path` empty unless the user named
   where to save it: the deck goes in the working folder and `path` says where.
5. **Check.** The build result's `verify` is the check. Fix its errors with
   `update_slide` or one rebuild. Call
   `verify_deck(path, expected_slides=N, required_items=[...])` only after
   `add_slide` or `update_slide`, or for a deck you did not build.
6. **Report** the path, one line per slide from `slides_built`, the check
   status and any warning left. On style feedback call
   `update_deck_prefs(feedback=...)` and say whether it was saved.

## References (paths relative to this skill's folder)

- `references/markdown-format.md` - every Markdown line and block.
- `references/outline-json.md` - every JSON slide field.
- `references/storyline.md` - blueprints, titles, density.
- `references/brief-and-sources.md` - when to ask; the key-facts ledger.
- `references/without-tools.md` - only when these tools are not offered.
