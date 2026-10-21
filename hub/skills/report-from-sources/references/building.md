# Building from the ledger

Lay the deliverable out answer first:

1. **Header line**: what it covers and its vintage, e.g. "As of 30 Sep 2024;
   data through 2024Q3 (sales_2024_q3.xlsx)". Take it from the inventory's
   `data_through` and any as-of date in the sources.
2. **Summary first**: the conclusion and the three to five figures that carry
   it, before any detail. A reader who stops here has the answer.
3. **Evidence**: the sections, tables and charts that support the summary.
4. **Conflicts and gaps**: sources that disagree (both values, which one you
   used and why), older files you used only for comparison, periods or series
   the request needed but the sources do not have, and anything the tools
   reported as ambiguous or not found. Write "None found" if there are none;
   never drop the section.

Then:

- Every headline number, stated change and notable event in the ledger goes
  in. Anything else you leave out, leave out on purpose.
- Say which figures you computed. "Sales rose 12% (computed from Q2 and Q3
  sales)" is different from "the report says sales rose 12%"; never present a
  figure you calculated as one the source states.
- Keep the source's framing. Write "Net income fell 8% to $4.2M", not just
  "Net income $4.2M".
- **Get series with `series_from_workbook(file_path, labels, periods="last 8")`.**
  It echoes the label it matched (`matched_label`) and returns the values
  exactly as stored, with each value's cell, the units, footnote flags such as
  "123.4(a)", and a `provenance` line for the chart footnote. If a label comes
  back `ambiguous`, call again with the exact label (or "Section > Label")
  from `candidates`, or ask the user; if `not_found`, pick from `nearest` or
  tell the user the series is not in the file. Never chart a look-alike
  series in its place.
- **For a chart in a workbook, call `chart_into_workbook`** with the same
  source arguments. It writes a table and a native Excel chart with an axis
  title, number format and a source and period footnote, then rereads the
  saved file and confirms the chart's values equal the source. For a slide,
  hand `chart_input` from `series_from_workbook` (categories, series, source
  note) to the deck-building code unchanged.
- Compute other figures from the data with `run_python`. Never retype numbers
  you read off a page.
- **Every chart and table names its source and period**, e.g. "Source:
  sales_2024_q3.xlsx, Jan-Sep 2024", and its units. Every figure in the text
  carries its source in brackets.
- Nothing from outside the sources unless you label it as such.
