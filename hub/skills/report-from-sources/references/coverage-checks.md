# Checking coverage against the saved file

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
