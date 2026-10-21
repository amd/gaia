# When the slide-decks tools are not offered

Run the same engine through `run_python`. Still no hand-written python-pptx:

```python
import sys; sys.path.insert(0, r"<this skill's folder>")
from slidedeck.markdown import outline_from_markdown
from slidedeck.build import build_deck
print(build_deck(outline_from_markdown(open(r"C:\Users\me\deck.md", encoding="utf-8").read()), r"C:\Users\me\deck.pptx"))
```

The result is the same as the tool's, including `verify`; fix every issue it
lists and build again.
