# JSON outline for build_deck

Use `build_deck(outline)` when Markdown can't express the slide:
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

Every slide has a `type` and may add `notes`, `source`, `subtitle` and
`takeaway` (the one line to act on, drawn as a dark band above the footer).

| Type | Fields |
|---|---|
| `title` | `title`, `subtitle` |
| `section` | `title` |
| `agenda` | `items` |
| `bullets` | `title`, `bullets` (a nested list is a sub-bullet level) |
| `two_column` | `left`, `right` |
| `chart` | `chart_type`, `y_title`, `source`, and the values as `rows`, as `categories` + `series`, or as `data_path` + `range` |
| `table` | `columns` + `rows`, or `data_path` + `range` |
| `kpi` | `metrics` |
| `quote` | `quote`, `attribution` |
| `image` | `path`, `alt` |
| `diagram` | `diagram`, `items` (`title`, `text`, `bullets`); a process also takes `axis` and `dimensions` |
| `timeline` | `milestones`, or `periods` and `lanes` |

- Diagrams: `process`, `flow`, `cycle`, `pyramid`, `matrix` (2x2), `grid`,
  `boxes`, `tree`, `stack`.
- Charts: `column`, `bar`, `line`, `area`, `pie`, `doughnut`, `scatter`,
  `combo`, `auto`.
- Chart and table values, any one of:
  - `"rows": [["Quarter", "West", "East"], ["Q1", 4.1, 3.9]]` - a header
    row, then one row per label. A list of objects works too.
  - `"categories": [...]` and `"series": {"West": [...]}`.
  - `"data_path": "C:/data/sales.xlsx", "sheet": "Q3", "range": "A1:D8"` -
    read from a .csv or .xlsx file; the range's first row is the header.
    `"series_in": "rows"` when each series is a row and the periods run
    across. Up to 60 rows and 30 columns; name a range for a larger sheet.
- A process diagram's `dimensions` are rows drawn under the steps:
  `[{"label": "Cost", "values": ["Low", "Medium", "High"]}, "Effort"]`. A
  row with `values` has one cell per step, in step order. A bare label is
  drawn as an arrow along the steps. Up to 6 steps and about 6 rows fit.
- Every line of an item's `text` and every entry of its `bullets` is kept
  as its own line in the box.

## Warnings and errors

Any outline with a slide is built. A slide that cannot be drawn as its type
(a chart with no data, say) is built as a text slide with its title, and
`warnings` gives the `update_slide` call and a corrected example for that
type. A misspelt field is read as the field it is closest to, and said. A
field the slide does not draw is a `content_dropped` issue, and the deck's
`style_score` drops with it.
`status: "error"` means no file was made (no slides, or a path this chat may
not use) and names the next call. Do not switch to writing code.

## Editing a deck

Slides are numbered from 1 and the cover is slide 1. Every edit returns
`slide_titles`, the deck's slides with their numbers.

- `add_slide(deck_path, {"slides": [...]})` appends slides in the deck's
  theme (`position=2` puts the new slide right after the cover). Build a big
  deck as 6-8 slides, then add a few at a time.
- `update_slide(deck_path, index, slide)` replaces slide `index` and returns
  `replaced`, the title of the slide that was there. It replaces a cover
  with a content slide only with `replace_cover=true`.

## What the request asks the deck to hold

`expect={"min_charts": 4, "min_tables": 1, "slides": 12}` on `build_deck`,
`build_deck_from_markdown` or `verify_deck` fails a deck with fewer native
charts or tables, and each issue gives the call that adds one. `counts` in
the check says how many the deck has.
Without `expect`, a text slide that is plainly numbers is a warning carrying
the `update_slide` call that draws the chart.
