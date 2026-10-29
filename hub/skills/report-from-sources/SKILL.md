---
name: report-from-sources
description: Build a report, deck, brief, memo, workbook or chart from the user's own files without missing what the sources stress. Use when the user asks to turn documents, spreadsheets or exports into a report, slides, a one-pager, a summary, charts or a dashboard. Pins the newest file per dataset, keeps a key-facts ledger per source, puts source and period on every figure, and checks every requested item before handing over.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: community
    tools_required:
      - list_files
      - read_file
      - run_python
    pairs_with:
      - office-docs
      - data-explore
    provenance:
      source: starter-pack
---

# Report From Sources

A deliverable can read well and still be wrong by omission: it skips the count
of failures the source led with, drops the "down 8% from last year", or charts
last year's file when this year's sat beside it. This skill is the order of
work that prevents that. Use it together with whatever builds the file.

## 1. Inventory the inputs

List the folder with `list_files(path)`, then record, for every file, what it
covers and which period it is for:

| File | Covers | Period / as-of | Found in | Use |
|---|---|---|---|---|
| sales_2023.xlsx | Regional sales | Jan-Dec 2023 | Month column | prior-year comparison |
| sales_2024_q3.xlsx | Regional sales | Jan-Sep 2024 | sheet title | **primary** |
| board_memo.docx | Quarter commentary | as of 30 Sep 2024 | first paragraph | facts ledger |

- The date written in the content (a title, an "as of" line, a date column)
  beats the file name, which beats the modified time.
- When several files cover the same thing for different periods, use the most
  recent one that matches the request; if the user named a period, use that
  one. Older files are for comparison only, and are labelled as such.
- Two files for the same period that disagree: say which one you used and why
  (later revision, "final" over "draft").
- Show this table in your reply before you build anything.

This lists every spreadsheet's sheets, row counts and date ranges in one call:

```python
import re
from datetime import datetime
from pathlib import Path
import pandas as pd

FOLDER = Path(r"C:\Users\me\Documents\quarterly")
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

Each row gets the fact, the value, and where it is (file, plus page, sheet or
section):

| Fact | Value | Source |
|---|---|---|
| Sites closed this year | 3 (2 in Q3) | board_memo.docx, Operations section |
| Q3 sales vs Q2 | +12% | sales_2024_q3.xlsx, Summary |
| Supplier risk flagged | single source for part B | board_memo.docx, Risks |

Keep the ledger in your working reply. Do not save it as an extra file in the
user's folder unless they ask for one.

## 3. Build from the ledger

- Every headline number, stated change and notable event in the ledger goes
  in. Anything else you leave out, leave out on purpose.
- Keep the source's framing. Write "Net income fell 8% to $4.2M", not just
  "Net income $4.2M".
- Compute figures from the data with `run_python`. Never retype numbers you
  read off a page.
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

## 5. Final requirements check

Do this before saying any deliverable is done.

1. **Turn the request into a numbered checklist.** One line for each thing the
   user named: every section, chart, table, column and metric; every ID, item
   or reference ("cite every ticket mentioned" means each one, by ID); every
   structural ask (a labelled input cell, a toggle or dropdown, a sheet in a
   given place); every file name, file type and folder; any length, audience or
   style limit.
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
source_text = Path(r"C:\Users\me\Documents\quarterly\board_memo.txt").read_text(encoding="utf-8")
ID = re.compile(r"\b[A-Z]{2,}-\d+\b")  # fit the pattern to the IDs in front of you
missing = sorted(set(ID.findall(source_text)) - set(ID.findall("\n".join(lines))))
print("IDs missing from the deliverable:", missing or "none")
```

For a document source, take the text from `read_file` instead of `read_text`.

To check a workbook's inputs, dropdowns and charts:

```python
import re
from openpyxl import load_workbook

wb = load_workbook(r"C:\Users\me\Documents\surveys\survey_summary.xlsx")
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
