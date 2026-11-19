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
- **Get time series with `series_from_workbook(file_path, labels)`.** Without
  `periods` it returns the last 12 with data (`periods="last 12"`);
  `periods_available` says how many the file has, so pass `periods="all"` or
  a range for more. It echoes the label it matched (`matched_label`) and
  returns the values exactly as stored, with each value's cell, the units,
  footnote flags such as "123.4(a)", and a `provenance` line for the chart
  footnote. Read `notes`: they say what was assumed (a decimal comma, a
  header with no year).
- **When it does not return values, do what the result says:**
  - a label `ambiguous`: call again with the exact label (or
    "Section > Label") from `candidates`, or ask the user;
  - status `ambiguous` with `choices`: one period has several columns
    (Actual and Budget). Call again with `filters="header=Actual"`;
  - `not_found`: pick from `nearest` or tell the user the series is not in
    the file. Never chart a look-alike series in its place;
  - `formulas_not_calculated`: the row is there but holds no values. Compute
    it from its input rows with `run_python`, or ask the user to open and
    save the file in Excel or LibreOffice;
  - "time series only": the table has no periods, or is row-level records.
    Read it with `run_python` instead.
- **For a chart in a workbook, call `chart_into_workbook`** with the same
  source arguments. It writes a table and a native Excel chart with an axis
  title, number format and a source and period footnote, then rereads the
  saved file and confirms the chart's values equal the source.
- **For a chart on a slide**, use `chart_input` from `series_from_workbook`.
  Its `categories`, `series` and `source` are the fields a slide-decks chart
  slide takes: add `type`, `title` and `chart_type`, and keep `source`, which
  becomes the source line under the chart.
- Compute other figures from the data with `run_python`. Never retype numbers
  you read off a page.
- **Every chart and table names its source and period**, e.g. "Source:
  sales_2024_q3.xlsx, Jan-Sep 2024", and its units. Every figure in the text
  carries its source in brackets.
- Nothing from outside the sources unless you label it as such.
