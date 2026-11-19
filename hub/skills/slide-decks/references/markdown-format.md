# Deck Markdown format

`build_deck_from_markdown(markdown=...)` turns this Markdown into slides,
builds them and checks them in one call. Anything it reads or draws
differently from what you wrote is in the result's `warnings`.

## Slides and text

- `# Title` (first line) is an optional cover slide; the next paragraph is
  its subtitle. A cover counts towards `expected_slides`.
- Each `## ` line (or a `---` line) starts a slide; the heading is its title.
  Write the title as the slide's takeaway sentence.
- A `##` slide with no content is a section divider. One titled Agenda or
  Contents whose body is a list becomes an agenda slide.
- `-`, `*`, `+` and `1.` lists are bullets; indent 2+ spaces for a sub-bullet.
- Exactly two `###` headings on a slide make a two-column slide.
- Three or more `###` headings make a diagram: one box per heading, with the
  heading's text and bullets inside it. A `Diagram: <kind>` line picks the
  shape: `boxes` (the default), `process` (left-to-right stages), `cycle`,
  `pyramid` or `stack`. With `Diagram:` and no `###` headings, each top-level
  bullet is a box and its sub-bullets are the detail.
- `Diagram: timeline` draws dated milestones on a line: write each heading as
  `### Feb 3 — Beta opens` (date, a dash, what happens), its bullets beneath.
  Use it for launch plans and dated roadmaps instead of boxes.
- A `##` slide with bullets and a `> quote` shows the quote in a panel beside
  them; a `##` title with only one short line is a statement slide.
- On a process of up to 6 steps, `Axis: More cost and effort` draws one arrow
  under the stages with that label. An `Axis:` line on a slide with no
  `Diagram:` line draws its boxes as a process.
- `Dimension: Cost | Low | Medium | High` draws a labelled row under a
  process with one cell per step, in step order. Write one line per
  dimension. A `Dimension:` line with only a label (or a second `Axis:`
  line) is drawn as its own arrow along the steps.
- On a diagram slide, a plain line between the `##` title and the first `###`
  is the slide's subtitle; a plain line right under a `###` heading is that
  box's bold lead line, above its bullets.
- A pipe table is a table slide; `![alt](path)` an image slide; a slide that
  is only a `>` block is a quote slide.
- `Source: ...` adds the source line, `Note: ...` a footnote, and `Notes:`
  (or `Speaker notes:`) starts the speaker notes.
- `Takeaway: ...` (or `Decision:`, `Ask:`) draws the one line to act on as a
  dark band above the footer. Use it on the slide that carries the ask.
- Two to four short top-level bullets with no sub-bullets are drawn as cards
  side by side. Write each as `**Heading** detail` or `Heading: detail` and
  the heading becomes the card's title.
- Use numbers the user gave or a tool returned; compute shares, growth and
  totals with `run_python`. A number with no such source is built and listed
  in `warnings`.
- `Must show: a; b; c` before the first slide lists the `required_items`:
  what the request needs visible on the slides.

## Charts, tables and KPI tiles

A fenced block holds optional `key: value` lines, then CSV.

- ```` ```chart <type> ```` - type is column, bar, line, area, pie, doughnut,
  scatter, combo or auto (the default). Keys: `y_title`, `x_title`,
  `y2_title`, `source`, `format`, `highlight`, `legend`, `footnote`. The CSV
  is a header row (category, series...) and one row per category.
- In place of the CSV lines, `data: C:/data/sales.xlsx` with `range: A1:D8`
  (and `sheet: Q3`) reads the rows from a .csv or .xlsx file; `series_in:
  rows` when each series is a row. A ```` ```table ```` block takes the same.
- ```` ```table ```` - the same CSV, shown as a table.
- ```` ```kpi ```` - `label,value,delta,note` rows, shown as KPI tiles.
- ```` ```slide ```` (or ```` ```json ````) - any slide fields as JSON,
  merged into the slide (see `references/outline-json.md`).

## Full example

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

`deck_outline_from_markdown(markdown)` returns the JSON outline without
building it, when you want to edit the outline before `build_deck`.
