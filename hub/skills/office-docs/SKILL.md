---
name: office-docs
description: Build real Office files with Python and check them before handing them over - Excel workbooks with live formulas and native charts, PowerPoint decks with native editable charts, Word documents with real headings and tables, and in-place .docx edits. Use when the user asks for a spreadsheet, workbook, financial model, summary or pivot-style table, a deck or slides, or a memo or report as a Word file.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: community
    tools_required:
      - run_python
      - verify_workbook
    provenance:
      source: starter-pack
---

# Office Docs

The deliverable is a file the user opens in Excel, PowerPoint or Word, not a
description of one. Build it with code through `run_python`, then prove it
works by checking the saved file — a file that opens is not yet a file that is
right.

## Procedure

1. **Check the libraries once per session.**

   ```python
   import importlib.util, sys
   need = {"openpyxl": "openpyxl", "pptx": "python-pptx", "docx": "python-docx"}
   missing = [pkg for mod, pkg in need.items() if importlib.util.find_spec(mod) is None]
   print(sys.executable, "MISSING:", missing or "none")
   ```

   If one is missing, stop: say the file was not created and ask the user to
   run `gaia init --profile chat`, which installs them. Never substitute CSV,
   Markdown or hand-written XML and call it the requested file.
2. **Save to an absolute path the user can reach**, normally the folder they
   named or the one their source files are in. Print the path at the end of the
   snippet.
3. **Compute every number from the source data inside the snippet.** Never type
   figures you read off a page into the code.
4. **Keep each `run_python` call small.** A long script can be cut off at the
   output limit and then nothing runs. Build a big file in steps: one call
   per sheet or per few slides, each opening the file the previous call saved,
   adding its part and saving again.
5. **Verify the saved file** (below), fix what it reports, and verify again.
6. **Report only what the verification showed**: the path, what is in the
   file, the key numbers as computed, and anything you could not do.

## Excel (openpyxl)

- Raw data on its own sheet, results on another. Results are **formulas** over
  the data, so the workbook stays live when the user edits it.
- A pivot-style table is grouped rows of `SUMIFS`/`COUNTIFS` over the data
  sheet. The criteria range must be the column that holds the criteria values,
  and the sum range the column with the numbers — compute the column letters
  from the header row, never by counting by eye.
- Build every formula's references from the row and column indexes you wrote
  the data to (`get_column_letter`), not from hand-typed addresses. When you
  copy a formula pattern across columns, check which references must stay
  fixed (`$A2`, `Data!$C:$C`).
- A formula never references its own cell or a total that includes itself.
- Newer functions (XLOOKUP, IFS, STDEV.S, ...) must be written with the
  `_xlfn.` prefix, e.g. `=_xlfn.XLOOKUP(...)`, or Excel shows `#NAME?`.
- Store numbers as numbers, dates as `datetime`, never as formatted strings;
  apply `number_format` (`'#,##0'`, `'0.0%'`, `'yyyy-mm-dd'`) for display.
- Charts come from `openpyxl.chart` (`BarChart`, `LineChart`, `PieChart`),
  referencing cells — never a pasted image.
- openpyxl does not compute formulas. Run `verify_workbook` on the saved file:
  it recalculates a copy, returns the computed values, and names every cell with
  `#REF!`, `#VALUE!`, `#DIV/0!`, `#NAME?`, a circular reference, a SUM over text
  or empty cells, or a criterion that matches no row. Fix the code that builds
  the workbook, rebuild, and run `verify_workbook` again until it reports no
  errors. Report numbers from its `computed_values`.

```python
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.utils import get_column_letter

rows = [("East", "Q1", 120), ("West", "Q1", 90), ("East", "Q2", 150), ("West", "Q2", 110)]
header = ["Region", "Quarter", "Sales"]

wb = Workbook()
data = wb.active
data.title = "Data"
data.append(header)
for row in rows:
    data.append(row)
last = data.max_row
col = {name: get_column_letter(i) for i, name in enumerate(header, start=1)}
region, sales = f"Data!${col['Region']}$2:${col['Region']}${last}", f"Data!${col['Sales']}$2:${col['Sales']}${last}"

summary = wb.create_sheet("Summary")
summary.append(["Region", "Sales"])
regions = sorted({r[0] for r in rows})
for i, name in enumerate(regions, start=2):
    summary.append([name, f"=SUMIFS({sales},{region},A{i})"])
total_row = len(regions) + 2
summary.append(["Total", f"=SUM(B2:B{total_row - 1})"])
summary[f"B{total_row}"].number_format = "#,##0"

chart = BarChart()
chart.title = "Sales by region"
chart.add_data(Reference(summary, min_col=2, min_row=1, max_row=total_row - 1), titles_from_data=True)
chart.set_categories(Reference(summary, min_col=1, min_row=2, max_row=total_row - 1))
summary.add_chart(chart, "D2")
path = r"C:\Users\me\Documents\sales_summary.xlsx"
wb.save(path)
print(path)
```

Then call `verify_workbook(file_path=path)`.

## PowerPoint (python-pptx)

- A title on every slide and one message per slide; put the message in the
  title ("Revenue grew 17% in Q3"), not a label ("Revenue").
- When the user asks for charts, they mean **native charts**
  (`shapes.add_chart`) whose numbers can be edited in PowerPoint. A matplotlib
  PNG is a picture of a chart, not a chart — never use one for that.
- Pick the chart type for the data: column/bar to compare categories, line for
  a trend over time, pie only for parts of one whole with few slices.
- Every chart or figure slide names its source in a small text box; speaker
  notes carry the talking points.
- Keep shapes inside the slide (`prs.slide_width`, `prs.slide_height`) and text
  short enough to fit its box at a readable size (at least 12 pt).

```python
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.util import Inches, Pt

prs = Presentation()
prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
slide = prs.slides.add_slide(prs.slide_layouts[5])  # title only
slide.shapes.title.text = "Revenue grew 17% quarter over quarter"

chart_data = CategoryChartData()
chart_data.categories = ["Q1", "Q2", "Q3"]
chart_data.add_series("Revenue ($M)", (4.1, 4.6, 5.4))
chart = slide.shapes.add_chart(
    XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.8), Inches(1.6), Inches(11.7), Inches(4.9), chart_data
).chart
chart.has_legend = True
chart.legend.position = XL_LEGEND_POSITION.BOTTOM
chart.legend.include_in_layout = False
plot = chart.plots[0]
plot.has_data_labels = True
plot.data_labels.number_format = '0.0'
plot.data_labels.number_format_is_linked = False

source = slide.shapes.add_textbox(Inches(0.8), Inches(6.7), Inches(11.7), Inches(0.4)).text_frame
source.text = "Source: finance_q3.xlsx, Revenue sheet"
source.paragraphs[0].runs[0].font.size = Pt(12)
slide.notes_slide.notes_text_frame.text = "The Q3 lift came from the West launch."
prs.save(r"C:\Users\me\Documents\qbr.pptx")
```

Verify by reopening, in a separate `run_python` call:

```python
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

prs = Presentation(r"C:\Users\me\Documents\qbr.pptx")
for i, s in enumerate(prs.slides, 1):
    charts = sum(1 for sh in s.shapes if sh.has_chart)
    pictures = sum(1 for sh in s.shapes if sh.shape_type == MSO_SHAPE_TYPE.PICTURE)
    off = [sh.name for sh in s.shapes if sh.left + sh.width > prs.slide_width or sh.top + sh.height > prs.slide_height]
    notes = s.has_notes_slide and bool(s.notes_slide.notes_text_frame.text.strip())
    title = s.shapes.title.text if s.shapes.title else "(no title)"
    print(i, title, "| charts:", charts, "pictures:", pictures, "off-slide:", off, "notes:", notes)
```

A chart slide that shows `charts: 0` and `pictures: 1` used an image — rebuild
it with `add_chart`.

## Word (python-docx)

A report uses real headings (`add_heading`), real tables and real lists
(`style="List Bullet"`), not bold paragraphs and typed dashes, so the reader
gets a navigation pane and a table of contents.

```python
from docx import Document
from docx.shared import Pt

doc = Document()
doc.styles["Normal"].font.size = Pt(11)
doc.add_heading("Term Sheet Review", level=1)
doc.add_paragraph("To: Investment Committee | From: Deal Team")
doc.add_heading("Key terms", level=2)
table = doc.add_table(rows=1, cols=2, style="Table Grid")
table.rows[0].cells[0].text, table.rows[0].cells[1].text = "Term", "Value"
for term, value in [("Pre-money valuation", "$40M"), ("Liquidation preference", "1x")]:
    cells = table.add_row().cells
    cells[0].text, cells[1].text = term, value
doc.add_paragraph("Board seat for the lead investor", style="List Bullet")
doc.save(r"C:\Users\me\Documents\term_sheet_memo.docx")
```

To edit an existing document, change the text of its runs so fonts and styles
survive, cover table cells too, and save to a new name unless told to
overwrite. A phrase split across two runs is missed by a run-level edit, so the
check reads whole paragraphs.

```python
import re
from docx import Document

pattern = re.compile(r"Acme Corp")
doc = Document(r"C:\Users\me\Documents\contract.docx")
paragraphs = list(doc.paragraphs)
for table in doc.tables:
    for row in table.rows:
        for cell in row.cells:
            paragraphs.extend(cell.paragraphs)
hits = 0
for p in paragraphs:
    for run in p.runs:
        new, n = pattern.subn("[REDACTED]", run.text)
        if n:  # rewriting an untouched run would drop an inline picture
            run.text, hits = new, hits + n
doc.save(r"C:\Users\me\Documents\contract_redacted.docx")
left = [m for p in paragraphs for m in pattern.findall(p.text)]
print("replaced", hits, "left over", left)
```

Verify by reopening and printing the headings (`p.style.name.startswith("Heading")`),
the table count, and any text the user asked to be present or removed.

## Large data

A sheet holds at most 1,048,576 rows. Aggregate big inputs with pandas inside
`run_python` and write only the summary and the rows it is built from; read a
big .xlsx once with `load_workbook(path, read_only=True)` and
`iter_rows(values_only=True)`. For heavy jobs raise `run_python`'s `timeout`.
