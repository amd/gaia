---
name: office-docs
description: Build real Office files with Python and check them before handing them over - Excel workbooks with live formulas and native charts, PowerPoint decks with native editable charts, Word documents with real headings and tables, and in-place .docx edits. Use when the user asks for a spreadsheet, workbook, financial model, summary or pivot-style table, a deck or slides, or a memo or report as a Word file.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: community
    requirements:
      python: ">=3.10"
      dependencies:
        - openpyxl>=3.1
    permissions:
      - program:run:soffice
      - scratch:write
    tools_required:
      - run_python
    pairs_with:
      - slide-decks
    tools:
      - name: verify_workbook
        description: Recalculate a copy of an .xlsx workbook and report every broken formula, with the computed values. Never writes the file.
        parameters:
          file_path:
            type: string
            required: true
        returns:
          type: object
        atomic: true
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
- openpyxl does not compute formulas. Run this skill's own tool,
  `office-docs/verify_workbook`, on the saved file: it recalculates a copy,
  returns the computed values, and names every cell with `#REF!`, `#VALUE!`,
  `#DIV/0!`, `#NAME?`, a circular reference, a SUM over text or empty cells, or
  a criterion that matches no row. Fix the code that builds the workbook,
  rebuild, and run it again until it reports no errors. Report numbers from its
  `computed_values`.

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

Then call `office-docs/verify_workbook(file_path=path)`.

## PowerPoint (python-pptx)

For a presentation (a deck someone will present, several slides telling one
story), load the `slide-decks` skill and follow it. This section covers quick
one-offs: a slide or two with a chart, or a short deck the user wants fast.
If `slide-decks` is not installed, use this section.

- A title on every slide and one message per slide; put the message in the
  title ("Revenue grew 17% in Q3"), not a label ("Revenue").
- **Start from a real 16:9 base.** `Presentation()` is 4:3 (10 in wide).
  Setting `slide_width` alone leaves every layout 10 in wide, so titles and
  body boxes sit in the left three quarters of the slide. Use `widescreen()`
  below, which also resizes the layouts, or open the user's own template.
- **Text you can read from the back of a room**: body text 18 pt or more,
  nothing below 14 pt (source lines and axis labels included). If text does
  not fit at that size, cut words or split the slide; never shrink the font.
- When the user asks for charts, they mean **native charts**
  (`shapes.add_chart`) whose numbers can be edited in PowerPoint. A matplotlib
  PNG is a picture of a chart, not a chart — never use one for that.
- Pick the chart type for the data: column/bar to compare categories, line for
  a trend over time, pie only for parts of one whole with few slices.
- **Declutter every chart**: no gridlines; with data labels on, hide the value
  axis; no chart title or legend for a single series (the slide title says
  what it is); no 3-D, shadows or gradients.
- **Colour with a purpose**, from the colour-blind-safe Okabe-Ito set:
  `#0072B2` blue, `#D55E00` vermillion, `#009E73` green, `#CC79A7` pink, in
  that order for series. Grey `#9CA3AF` is for context: when the title is
  about one bar or line, colour that one and make the rest grey.
- **Alt text on every chart and picture**: one sentence saying what it shows
  and the takeaway ("Column chart: revenue rose from 4.1 to 5.4 $M, Q1 to Q3").
- Every chart or figure slide names its source in a small text box; speaker
  notes carry the talking points.
- Keep shapes inside the slide (`prs.slide_width`, `prs.slide_height`).

```python
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION
from pptx.util import Inches, Pt

OKABE_ITO = ["0072B2", "D55E00", "009E73", "CC79A7"]  # series colours, in order
CONTEXT_GREY = "9CA3AF"


def widescreen():
    """A blank 16:9 deck whose layouts are 16:9 too, not just its slides."""
    prs = Presentation()
    scale = Inches(13.333) / prs.slide_width
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    # Layouts first: their placeholders inherit position from the master.
    for holder in [*prs.slide_layouts, prs.slide_master]:
        for ph in holder.placeholders:
            x, y, w, h = ph.left, ph.top, ph.width, ph.height
            ph.left, ph.top, ph.width, ph.height = int(x * scale), y, int(w * scale), h
    return prs


def set_alt_text(shape, text):
    shape._element[0][0].set("descr", text)  # cNvPr: what screen readers and PowerPoint's Alt Text show


prs = widescreen()

slide = prs.slides.add_slide(prs.slide_layouts[1])  # title and content
slide.shapes.title.text = "West launch drove the Q3 lift"
body = slide.placeholders[1].text_frame
for i, point in enumerate(["West opened in July", "Q3 revenue up 17% on Q2", "East flat"]):
    para = body.paragraphs[0] if i == 0 else body.add_paragraph()
    para.text = point
    para.font.size = Pt(24)

slide = prs.slides.add_slide(prs.slide_layouts[5])  # title only
slide.shapes.title.text = "Revenue grew 17% quarter over quarter"
quarters, revenue = ["Q1", "Q2", "Q3"], (4.1, 4.6, 5.4)
chart_data = CategoryChartData()
chart_data.categories = quarters
chart_data.add_series("Revenue ($M)", revenue)
frame = slide.shapes.add_chart(
    XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.8), Inches(1.6), Inches(11.7), Inches(4.9), chart_data
)
chart = frame.chart
chart.font.size = Pt(16)
chart.has_title = False  # the slide title carries the message
chart.has_legend = False
chart.value_axis.has_major_gridlines = False
chart.value_axis.visible = False
plot = chart.plots[0]
plot.gap_width = 60
plot.has_data_labels = True
plot.data_labels.number_format = '0.0'
plot.data_labels.number_format_is_linked = False
plot.data_labels.position = XL_LABEL_POSITION.OUTSIDE_END
for i, point in enumerate(plot.series[0].points):  # the bar the title is about in blue, the rest grey
    point.format.fill.solid()
    point.format.fill.fore_color.rgb = RGBColor.from_string(OKABE_ITO[0] if i == len(quarters) - 1 else CONTEXT_GREY)
set_alt_text(frame, f"Column chart: revenue rose from {revenue[0]} to {revenue[-1]} $M, {quarters[0]} to {quarters[-1]}.")

source = slide.shapes.add_textbox(Inches(0.8), Inches(6.7), Inches(11.7), Inches(0.5)).text_frame
source.text = "Source: finance_q3.xlsx, Revenue sheet"
source.paragraphs[0].runs[0].font.size = Pt(14)
slide.notes_slide.notes_text_frame.text = "The Q3 lift came from the West launch."

path = r"C:\Users\me\Documents\qbr.pptx"
prs.save(path)
print(path)
```

With several series, colour them in palette order instead:
`for s, hexcode in zip(plot.series, OKABE_ITO): s.format.fill.solid(); s.format.fill.fore_color.rgb = RGBColor.from_string(hexcode)`
(for a line chart, set `s.format.line.color.rgb`).

Verify by reopening, in a separate `run_python` call:

```python
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Pt

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".svg", ".emf", ".wmf")
prs = Presentation(r"C:\Users\me\Documents\qbr.pptx")
W, H = prs.slide_width, prs.slide_height
widest = max((ph.left + ph.width for lay in prs.slide_layouts for ph in lay.placeholders), default=W)
if widest < W * 0.85:
    print("LAYOUTS ARE NARROWER THAN THE SLIDE: rebuild with widescreen() or the user's template")
for i, s in enumerate(prs.slides, 1):
    problems = []
    title = s.shapes.title.text.strip() if s.shapes.title else ""
    if not title:
        problems.append("no title")
    charts = sum(1 for sh in s.shapes if sh.has_chart)
    pictures = sum(1 for sh in s.shapes if sh.shape_type == MSO_SHAPE_TYPE.PICTURE)
    for sh in s.shapes:
        if sh.left < 0 or sh.top < 0 or sh.left + sh.width > W or sh.top + sh.height > H:
            problems.append(f"off-slide: {sh.name}")
        alt = sh._element[0][0].get("descr", "").strip()  # add_picture fills it with the file name
        if (sh.has_chart or sh.shape_type == MSO_SHAPE_TYPE.PICTURE) and (not alt or alt.lower().endswith(IMAGE_EXT)):
            problems.append(f"no alt text: {sh.name}")
        sizes = []
        if sh.has_text_frame:
            sizes += [r.font.size for p in sh.text_frame.paragraphs for r in [p, *p.runs] if r.font.size]
        if sh.has_chart and sh.chart.font.size:
            sizes.append(sh.chart.font.size)
        if any(size < Pt(14) for size in sizes):
            problems.append(f"text under 14 pt: {sh.name}")
    notes = s.has_notes_slide and bool(s.notes_slide.notes_text_frame.text.strip())
    print(i, title or "(no title)", "| charts:", charts, "pictures:", pictures, "notes:", notes, "|", problems or "ok")
```

A chart slide that shows `charts: 0` and `pictures: 1` used an image — rebuild
it with `add_chart`. Fix every problem it lists and run it again.

## Word (python-docx)

A report uses real structure, not formatting that looks like structure, so
the reader gets a navigation pane, a table of contents, and a document a
screen reader can follow:

- **Headings are heading styles** (`add_heading(text, level=1..3)`), in order
  with no skipped levels, never bold or enlarged `Normal` paragraphs.
- **Lists are list styles** (`style="List Bullet"`, `"List Number"`), not
  typed dashes or numbers.
- **Tables have a header row**: bold, and marked to repeat on every page,
  which is also how assistive technology knows it is a header.
- **Every picture has alt text**: one sentence saying what it shows.

```python
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt


def header_row(table):
    row = table.rows[0]
    flag = OxmlElement("w:tblHeader")
    flag.set(qn("w:val"), "true")
    row._tr.get_or_add_trPr().append(flag)  # repeats on each page; read as a header
    for cell in row.cells:
        for run in (r for p in cell.paragraphs for r in p.runs):
            run.bold = True


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
header_row(table)
doc.add_paragraph("Board seat for the lead investor", style="List Bullet")
picture = doc.add_picture(r"C:\Users\me\Documents\cap_table.png", width=Inches(6))
picture._inline.docPr.set("descr", "Cap table after the round: founders 62%, investors 38%.")
path = r"C:\Users\me\Documents\term_sheet_memo.docx"
doc.save(path)
print(path)
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

Verify by reopening, in a separate `run_python` call, then check any text the
user asked to be present or removed:

```python
from docx import Document
from docx.oxml.ns import qn

doc = Document(r"C:\Users\me\Documents\term_sheet_memo.docx")
headings = [(p.style.name, p.text) for p in doc.paragraphs if p.style.name.startswith("Heading")]
levels = [int(name.split()[-1]) for name, _ in headings if name.split()[-1].isdigit()]
skipped = [b for a, b in zip([0, *levels], levels) if b > a + 1]
fake = [p.text[:40] for p in doc.paragraphs
        if p.style.name == "Normal" and p.runs and all(r.bold for r in p.runs) and len(p.text) < 80]


def has_header_row(table):
    props = table.rows[0]._tr.trPr if table.rows else None
    return props is not None and props.find(qn("w:tblHeader")) is not None


no_header = [i for i, t in enumerate(doc.tables, 1) if not has_header_row(t)]
no_alt = [i for i, s in enumerate(doc.inline_shapes, 1)
          if not s._inline.docPr.get("descr", "").strip()
          or s._inline.docPr.get("descr", "").lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp"))]
print("headings:", headings)
print("skipped levels:", skipped, "| bold paragraphs that look like headings:", fake)
print("tables:", len(doc.tables), "without a header row:", no_header, "| pictures without alt text:", no_alt)
```

## Large data

A sheet holds at most 1,048,576 rows. Aggregate big inputs with pandas inside
`run_python` and write only the summary and the rows it is built from; read a
big .xlsx once with `load_workbook(path, read_only=True)` and
`iter_rows(values_only=True)`. For heavy jobs raise `run_python`'s `timeout`.
