# PowerPoint fallback (python-pptx)

Use this file only when the `slide-decks` skill is not installed. That skill
owns presentations: layout, colour, chart styling and its own checks. This
file is the minimum for a one-off: a slide or two, or a short deck.

- A title on every slide, and one message per slide.
- `Presentation()` is 4:3, and setting `slide_width` alone leaves every layout
  10 in wide. Use `widescreen()` below, which resizes the layouts too, or open
  the user's own template.
- Body text 18 pt or more, nothing below 14 pt. If text does not fit, cut
  words or split the slide.
- A chart is a native chart (`shapes.add_chart`), whose numbers can be edited
  in PowerPoint. Never paste a matplotlib picture of one.
- Alt text on every chart and picture: one sentence saying what it shows.
- Keep shapes inside the slide (`prs.slide_width`, `prs.slide_height`).

```python
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches, Pt


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


prs = widescreen()

slide = prs.slides.add_slide(prs.slide_layouts[1])  # title and content
slide.shapes.title.text = "The new onboarding takes three days"
body = slide.placeholders[1].text_frame
for i, point in enumerate(["Day 1: accounts and laptop", "Day 2: team introductions", "Day 3: first task"]):
    para = body.paragraphs[0] if i == 0 else body.add_paragraph()
    para.text = point
    para.font.size = Pt(24)

slide = prs.slides.add_slide(prs.slide_layouts[5])  # title only
slide.shapes.title.text = "Sign-ups rose each month"
months, signups = ["Jan", "Feb", "Mar"], (120, 150, 180)
chart_data = CategoryChartData()
chart_data.categories = months
chart_data.add_series("Sign-ups", signups)
frame = slide.shapes.add_chart(
    XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.8), Inches(1.6), Inches(11.7), Inches(5.2), chart_data
)
frame.chart.font.size = Pt(16)
frame.chart.has_legend = False  # one series: the slide title says what it is
# cNvPr holds the alt text PowerPoint and screen readers show.
frame._element[0][0].set("descr", f"Column chart: sign-ups rose from {signups[0]} to {signups[-1]}, {months[0]} to {months[-1]}.")

path = r"C:\Users\me\Documents\onboarding.pptx"
prs.save(path)
print(path)
```

Then call `office-docs/verify_document(file_path=path)`. It returns the number
of slides, each title, and how many native charts and pictures the deck holds.
An error means the file is not a real .pptx: build it again with the code
above. A chart counted under `pictures` instead of `charts` is an image:
rebuild it with `add_chart`.
