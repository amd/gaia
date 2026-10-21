---
name: report-from-sources
description: Build a report, deck, brief, memo, workbook or chart from the user's own files without missing what the sources stress. Use when the user asks to turn documents, spreadsheets or exports into a report, slides, a one-pager, a summary, charts or a dashboard. Pins the newest file per dataset, keeps a key-facts ledger per source, puts source and period on every figure, and checks every requested item before handing over.
license: MIT
version: 1.1.0
metadata:
  gaia:
    security_tier: community
    requirements:
      python: ">=3.10"
      dependencies:
        - openpyxl>=3.1
        - python-pptx>=1.0
    tools_required:
      - list_files
      - read_file
      - run_python
    tools:
      - name: inventory_data_files
        description: List a folder's data files with sheets, header rows, units and period coverage, and rank copies of one dataset by vintage.
        parameters:
          folder:
            type: string
            required: true
          recursive:
            type: boolean
            required: false
            default: false
        returns:
          type: object
        atomic: true
      - name: series_from_workbook
        description: Read named series for chosen periods from a spreadsheet, CSV or JSON file, matched by label and cited to their cells.
        parameters:
          file_path:
            type: string
            required: true
          labels:
            type: array
            required: true
          sheet:
            type: string
            required: false
            default: ""
          periods:
            type: string
            required: false
            default: last 12
          orientation:
            type: string
            required: false
            default: auto
          filters:
            type: string
            required: false
            default: ""
        returns:
          type: object
        atomic: true
      - name: chart_into_workbook
        description: Chart named series from a source file into an .xlsx as a native Excel chart with a source footnote, verified on reopen.
        parameters:
          output_path:
            type: string
            required: true
          file_path:
            type: string
            required: true
          labels:
            type: array
            required: true
          sheet:
            type: string
            required: false
            default: ""
          periods:
            type: string
            required: false
            default: last 12
          chart_type:
            type: string
            required: false
            default: line
          title:
            type: string
            required: false
            default: ""
          y_axis_title:
            type: string
            required: false
            default: ""
          number_format:
            type: string
            required: false
            default: ""
          secondary_labels:
            type: array
            required: false
          source_note:
            type: string
            required: false
            default: ""
          filters:
            type: string
            required: false
            default: ""
        returns:
          type: object
        atomic: false
    pairs_with:
      - office-docs
      - data-explore
    provenance:
      source: starter-pack
---

# Report From Sources

A deliverable can read well and still be wrong by omission: it skips the count
of failures the source led with, drops the "down 8% from last year", or charts
last year's file when this year's sat beside it. It can also be wrong by
addition: a figure no source contains. This skill is the order of work that
prevents both. Use it together with whatever builds the file.

## 1. Inventory the inputs

**Call `inventory_data_files(folder)` first, then interpret its result.** One
call returns, for every spreadsheet, CSV and JSON file, each sheet's layout,
header rows (multi-row and merged headers), units, the periods it covers
(first, last, and last with data) and its series names. It groups files that
hold the same dataset and ranks them by vintage: `datasets[].note` names the
newest copy and the older ones ("two versions of the same series: X (through
2026Q2, newest) and Y (through 2025Q2)"). Documents come back under
`narrative_sources`; read those in step 2.

From that result, record for every file what it covers and which period it is
for, and show the table in your reply before you build anything:

| File | Covers | Period / as-of | Found in | Use |
|---|---|---|---|---|
| sales_2023.xlsx | Regional sales | Jan-Dec 2023 | Month column | prior-year comparison |
| sales_2024_q3.xlsx | Regional sales | Jan-Sep 2024 | sheet title | **primary** |
| board_memo.docx | Quarter commentary | as of 30 Sep 2024 | first paragraph | facts ledger |

- The tool ranks by the data the file holds (last period with data), then an
  "as of" line in it, then the period or revision in the file name, then the
  modified time. Use the newest unless the user named a period; then use the
  file that covers it. Older files are for comparison only, and are labelled
  as such.
- When the note says two copies cover the same periods, say which one you used
  and why (later revision, "final" over "draft"), and compare their values.
- A file under `not_read`, or a sheet with layout `unknown`, the tool could
  not parse. Open it with the fallback below.

**Fallback when the tool can't parse a file.** List the folder with
`list_files(path)`; this lists every spreadsheet's sheets, row counts and date
ranges:

```python
import re
from datetime import datetime
from pathlib import Path
import pandas as pd

FOLDER = Path(r"<sources folder>")  # the absolute path you inventoried
DATEISH = re.compile(r"date|month|year|period|quarter|week|day|as.?of", re.I)
for p in sorted(FOLDER.iterdir()):
    if not p.is_file():
        continue
    print(f"\n== {p.name}  (modified {datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d})")
    years = sorted(set(re.findall(r"(?:19|20)\d\d", p.stem)))
    if years:
        print("   years in file name:", years)
    if p.suffix.lower() in (".csv", ".xlsx", ".xlsm", ".xls"):
        sheets = {"csv": pd.read_csv(p)} if p.suffix.lower() == ".csv" else pd.read_excel(p, sheet_name=None)
        for name, df in sheets.items():
            print(f"   [{name}] {len(df)} rows; columns: {list(df.columns)[:12]}")
            for col in df.columns:
                if DATEISH.search(str(col)):
                    vals = df[col].dropna()
                    if len(vals):
                        print(f"     {col}: {vals.min()} .. {vals.max()}")
```

For documents, the period comes from reading them (step 2).

## 2. Keep a key-facts ledger per source

Read each source in full with `read_file(file_path)`, following `next_offset`
until it is null. A skim or a search result misses exactly the line that
matters. For each source, write down:

- **Headline numbers**: totals, rates, counts, balances.
- **Changes the source states** against a prior period: from, to, and by how
  much ("revenue $4.6M, down 8% from $5.0M").
- **Notable events**: counts of failures, closures, incidents, outages, recalls,
  defaults, departures. A small count of rare events is often the most
  important fact on the page.
- **Risks and caveats**: warnings, exceptions, restatements, missed targets.
- **What the source itself emphasises**: its summary or highlights, the first
  paragraph, bold text, "notably", "for the first time", "record".

Each row gets the fact, the value, whether the source **states** it or you
**computed** it, and where it is (file, plus page, sheet, section or cell):

| Fact | Value | Kind | Source |
|---|---|---|---|
| Sites closed this year | 3 (2 in Q3) | stated | board_memo.docx, Operations section |
| Q3 sales | 4.6 ($M) | stated | sales_2024_q3.xlsx, Summary!D7 (from `series_from_workbook`) |
| Q3 sales vs Q2 | +12% | computed: 4.6 / 4.1 - 1 | sales_2024_q3.xlsx, Summary!C7:D7 |
| Supplier risk flagged | single source for part B | stated | board_memo.docx, Risks |

A computed row names its formula and the stated rows it uses. When two sources
give different values for the same fact, keep both rows; they go in the
"Conflicts and gaps" section (step 3), with which one you used and why.

Keep the ledger in your working reply. Do not save it as an extra file in the
user's folder unless they ask for one.

## 3. Build from the ledger

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

## 4. Check coverage against the saved file

Reopen what you saved and check every ledger row against it. This reads a
.docx, .pptx (including chart data and notes), .xlsx or text file and reports
each fact as found or missing:

```python
import re
from pathlib import Path

def deliverable_lines(path):
    """What a reader sees in a saved .docx/.pptx/.xlsx/text file, one line per
    paragraph, table row, chart series or sheet row."""
    p, out = Path(path), []
    kind = p.suffix.lower()
    if kind == ".docx":
        from docx import Document
        doc = Document(p)
        out += [x.text for x in doc.paragraphs]
        out += [" | ".join(c.text for c in r.cells) for t in doc.tables for r in t.rows]
    elif kind == ".pptx":
        from pptx import Presentation
        for slide in Presentation(p).slides:
            for sh in slide.shapes:
                if sh.has_text_frame:
                    out += sh.text_frame.text.splitlines()
                if sh.has_table:
                    out += [" | ".join(c.text for c in r.cells) for r in sh.table.rows]
                if sh.has_chart:
                    for plot in sh.chart.plots:
                        cats = list(plot.categories)
                        out += [f"{s.name}: " + ", ".join(f"{c}={v}" for c, v in zip(cats, s.values))
                                for s in plot.series]
            if slide.has_notes_slide:
                out += slide.notes_slide.notes_text_frame.text.splitlines()
    elif kind in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        for ws in load_workbook(p):  # formulas read as text, not results
            out += [" | ".join(str(v) for v in row if v is not None) for row in ws.iter_rows(values_only=True)]
    else:
        out = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return [line for line in out if line.strip()]

lines = deliverable_lines(r"C:\Users\me\Documents\quarterly\q3_review.pptx")
# (fact, a word that sits next to it, the value as written)
ledger = [
    ("sites closed this year", "clos", "3"),
    ("Q3 sales vs Q2", "sales", "12%"),
]
for fact, word, value in ledger:
    num = re.compile(rf"(?<![\d.]){re.escape(value)}(?![\d])")
    hit = next((l for l in lines if word.lower() in l.lower() and num.search(l)), None)
    print("ok     " if hit else "MISSING", f"{fact} = {value}", f"-> {hit[:80]}" if hit else "")
```

A `MISSING` row means look, not necessarily absent: the value may be written
as a word ("three") or rounded. Add whatever is truly missing and run the check
again.

**Then trace the other way: every number in the deliverable must come from the
ledger or a computation you can name.** That catches figures that were made
up or mistyped, which the check above cannot. Append this to the snippet above
and run them together. `traced` holds every ledger value plus the values
`series_from_workbook` returned (paste its `chart_input["series"]` lists) and
your computed results:

```python
traced = [3, 2, 12, 4.6, 4.1]  # ledger values, series values, computed results
traced += [v for values in {}.values() for v in values if v is not None]  # chart_input["series"]

NUM = re.compile(r"(?<![\w.])[-+]?\$?\d[\d,]*(?:\.\d+)?%?")
YEARISH = re.compile(r"^(?:19|20)\d\d$")

def matches(text, value):
    """A number as written ("4.6", "12%", "$1,200") matches a value at the shown precision."""
    digits = text.lstrip("+$").rstrip("%").replace(",", "")
    places = len(digits.split(".")[1]) if "." in digits else 0
    return any(abs(float(digits) - round(v * s, places)) < 10 ** -places / 2 + 1e-9
               for v in [value] for s in (1, 100))

untraced = []
for line in lines:
    for text in NUM.findall(line):
        bare = text.lstrip("+-$").rstrip("%").replace(",", "")
        if YEARISH.match(bare) or not bare:
            continue  # years and period labels are not figures
        if not any(matches(text.lstrip("-"), abs(v)) for v in traced):
            untraced.append((text, line[:80]))
for text, line in untraced:
    print("UNTRACED", text, "->", line)
print(len(untraced), "untraced number(s)")
```

An `UNTRACED` number is either a ledger row you forgot to add (add it with its
source), a computation (add it as a computed row), or an error: remove or fix
it. List numbers, slide numbers and dates show up too; skip those.

## 5. Final requirements check

Do this before saying any deliverable is done.

1. **Turn the request into a numbered checklist.** One line for each thing the
   user named: every section, chart, table, column and metric; every ID, item
   or reference ("cite every ticket mentioned" means each one, by ID); every
   structural ask (a labelled input cell, a toggle or dropdown, a sheet in a
   given place); every file name, file type and folder; any length, audience or
   style limit. Always add three lines of your own: the as-of line in the
   header, the summary before the evidence, and the Conflicts and gaps section.
2. **Check each line against the saved file.** Reopen it; your memory of what
   you wrote is not evidence.
3. **Fix the gaps and check again.**
4. **Report the checklist** with each line marked met or not met. Say plainly
   what is not met and why. Never mark a line met that you did not verify.

Example request: *"Build a workbook from the three survey exports with a
Summary sheet, a chart of satisfaction by region, and a labelled input at the
top of Summary to switch between 2023 and 2024. Save it as survey_summary.xlsx
next to the exports."*

1. Uses all three exports
2. Sheet named Summary
3. Chart of satisfaction by region
4. Input cell at the top of Summary, with a label beside it
5. The input is a 2023/2024 dropdown, and the Summary formulas read it
6. File name survey_summary.xlsx
7. Saved in the exports folder

To check that every ID in the sources made it in, append this to the step 4
snippet and run them together (each `run_python` call starts fresh):

```python
source_text = Path(r"<source text file>").read_text(encoding="utf-8")
ID = re.compile(r"\b[A-Z]{2,}-\d+\b")  # fit the pattern to the IDs in front of you
missing = sorted(set(ID.findall(source_text)) - set(ID.findall("\n".join(lines))))
print("IDs missing from the deliverable:", missing or "none")
```

For a document source, take the text from `read_file` instead of `read_text`.

To check a workbook's inputs, dropdowns and charts:

```python
import re
from openpyxl import load_workbook

wb = load_workbook(r"<saved workbook>")  # absolute path of the file you built
formulas = [c.value for s in wb for row in s.iter_rows() for c in row
            if isinstance(c.value, str) and c.value.startswith("=")]
print("sheets:", wb.sheetnames, "| formulas:", len(formulas))
for ws in wb:
    print(f"[{ws.title}] {ws.max_row} rows, {len(ws._charts)} charts")
    for dv in ws.data_validations.dataValidation:
        for rng in str(dv.sqref).split():
            cell = ws[rng.split(":")[0]]
            label = ws.cell(cell.row, cell.column - 1).value if cell.column > 1 else None
            ref = re.compile(rf"(?<![A-Za-z$])\$?{cell.column_letter}\$?{cell.row}(?!\d)")
            users = sum(1 for f in formulas if ref.search(f))
            print(f"  input {rng} label={label!r} options={dv.formula1} value={cell.value!r} "
                  f"formulas reading it={users}")
```

An input that no formula reads (`formulas reading it=0`) is decoration, not a
toggle.

## Fork this

Pin step 2 to your field's standing questions, for example churn, pipeline and
headcount for a monthly business review, and the ledger becomes a checklist no
report can skip.
