# PowerPoint one-offs (python-pptx)

For a presentation (a deck someone will present, several slides telling one
story), load the `slide-decks` skill and follow it. This file covers quick
one-offs: a slide or two with a chart, or a short deck the user wants fast.
If `slide-decks` is not installed, use this file.

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
