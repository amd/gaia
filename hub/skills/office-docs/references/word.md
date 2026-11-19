# Word documents (python-docx)

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
doc.add_heading("Office Move Plan", level=1)
doc.add_paragraph("To: Facilities team | From: Operations")
doc.add_heading("Details", level=2)
table = doc.add_table(rows=1, cols=2, style="Table Grid")
table.rows[0].cells[0].text, table.rows[0].cells[1].text = "Item", "Detail"
for item, detail in [("Move date", "14 June"), ("Desks", "42")]:
    cells = table.add_row().cells
    cells[0].text, cells[1].text = item, detail
header_row(table)
doc.add_paragraph("Order new access cards", style="List Bullet")
picture = doc.add_picture(r"C:\Users\me\Documents\floor_plan.png", width=Inches(6))
picture._inline.docPr.set("descr", "Floor plan: 42 desks in three zones.")
path = r"C:\Users\me\Documents\office_move_memo.docx"
doc.save(path)
print(path)
```

Then call `office-docs/verify_document(file_path=path)`. An error means the
file is not a real .docx: build it again with the code above, never with
`write_file`. Compare its heading, table and picture counts with what you
built.

To edit an existing document, change the text of its runs so fonts and styles
survive, cover table cells too, and save to a new name unless told to
overwrite. A phrase split across two runs is missed by a run-level edit, so the
check reads whole paragraphs. This example renames a project throughout:

```python
import re
from docx import Document

pattern = re.compile(r"Project Atlas")
doc = Document(r"C:\Users\me\Documents\handbook.docx")
paragraphs = list(doc.paragraphs)
for table in doc.tables:
    for row in table.rows:
        for cell in row.cells:
            paragraphs.extend(cell.paragraphs)
hits = 0
for p in paragraphs:
    for run in p.runs:
        new, n = pattern.subn("Project Orion", run.text)
        if n:  # rewriting an untouched run would drop an inline picture
            run.text, hits = new, hits + n
doc.save(r"C:\Users\me\Documents\handbook_updated.docx")
left = [m for p in paragraphs for m in pattern.findall(p.text)]
print("replaced", hits, "left over", left)
```

`verify_document` counts what is in the file. For a report, also check its
structure in a separate `run_python` call (heading order, table header rows,
alt text), and any text the user asked to be present or removed:

```python
from docx import Document
from docx.oxml.ns import qn

doc = Document(r"C:\Users\me\Documents\office_move_memo.docx")
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
