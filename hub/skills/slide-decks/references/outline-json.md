# JSON outline for build_deck

Use `build_deck(outline, output_path)` when Markdown can't express the slide:
diagrams, timelines, combo charts, or a blueprint outline.

```json
{"required_items": ["Revenue $12.4M"], "slides": [
  {"type": "title", "title": "Northwind Q3 review", "subtitle": "October 2026"},
  {"type": "bullets", "title": "Q3 revenue grew 12% to $12.4M", "bullets": ["West +37%", ["two new distributors"]]},
  {"type": "chart", "title": "West drove the growth", "chart_type": "column", "categories": ["Q1", "Q2", "Q3"],
   "series": {"West": [4.6, 5.0, 5.6]}, "y_title": "Revenue ($M)", "source": "sales.xlsx, 2026"},
  {"type": "diagram", "diagram": "process", "title": "Onboarding takes four steps", "items": ["Sign up", "Verify", "Train", "Order"]}
]}
```

Every slide has a `type` and may add `notes` and `source`.

| Type | Fields |
|---|---|
| `title` | `title`, `subtitle` |
| `section` | `title` |
| `agenda` | `items` |
| `bullets` | `title`, `bullets` (a nested list is a sub-bullet level) |
| `two_column` | `left`, `right` |
| `chart` | `chart_type`, `categories`, `series`, `y_title`, `source` |
| `table` | `columns`, `rows` |
| `kpi` | `metrics` |
| `quote` | `quote`, `attribution` |
| `image` | `path`, `alt` |
| `diagram` | `diagram`, `items` |
| `timeline` | `milestones`, or `periods` and `lanes` |

- Diagrams: `process`, `flow`, `cycle`, `pyramid`, `matrix` (2x2), `grid`,
  `boxes`, `tree`, `stack`.
- Charts: `column`, `bar`, `line`, `area`, `pie`, `doughnut`, `scatter`,
  `combo`, `auto`.

## Errors

On `status: "error"` the `problems` list names every field to fix and gives a
corrected example for that slide type. Fix them all and call again. Do not
switch to writing code.

## Editing a deck

- `add_slide(deck_path, {"slides": [...]})` appends slides in the deck's
  theme (`position` inserts at a 1-based place). Build a big deck as 6-8
  slides, then add a few at a time.
- `update_slide(deck_path, index, slide)` replaces slide `index` (1-based).
