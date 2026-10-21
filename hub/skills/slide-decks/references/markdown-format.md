# Deck Markdown format

`build_deck_from_markdown(markdown=..., output_path=...)` turns this Markdown
into slides, builds them and checks them in one call.

## Slides and text

- `# Title` (first line) is the cover; the next paragraph is its subtitle.
- Each `## ` line (or a `---` line) starts a slide; the heading is its title.
  Write the title as the slide's takeaway sentence.
- A `##` slide with no content is a section divider. One titled Agenda or
  Contents whose body is a list becomes an agenda slide.
- `-`, `*`, `+` and `1.` lists are bullets; indent 2+ spaces for a sub-bullet.
- Exactly two `###` headings on a slide make a two-column slide.
- A pipe table is a table slide; `![alt](path)` an image slide; a slide that
  is only a `>` block is a quote slide.
- `Source: ...` adds the source line, `Note: ...` a footnote, and `Notes:`
  (or `Speaker notes:`) starts the speaker notes.
- `Must show: a; b; c` before the first slide lists the `required_items`:
  what the request needs visible on the slides.

## Charts, tables and KPI tiles

A fenced block holds optional `key: value` lines, then CSV.

- ```` ```chart <type> ```` - type is column, bar, line, area, pie, doughnut,
  scatter, combo or auto (the default). Keys: `y_title`, `x_title`,
  `y2_title`, `source`, `format`, `highlight`, `legend`, `footnote`. The CSV
  is a header row (category, series...) and one row per category.
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
