# Cue scan

The phrase lists are the part to change: add the phrases your team actually
uses, in every language its meetings are held in. Each entry is a regular
expression; plain words work as they are.

```python
import re, sys
from pathlib import Path

sys.stdout.reconfigure(errors="backslashreplace")  # a console that cannot show a script prints \uXXXX instead of failing
FOLDER = Path(r"C:\Users\me\Documents\team-sync-notes")
COMMIT_CUES = [
    r"I'll", r"I will", r"I'm going to", r"I can (?:take|do|handle|send|look)", r"let me",
    r"leave it with me", r"on me", r"we'll", r"we will", r"we can take", r"will do", r"sure",
    r"can you", r"could you", r"please", r"action", r"AI:", r"to-?do", r"next steps?",
    r"follow[- ]up", r"owner", r"due", r"by (?:mon|tue|wed|thu|fri|end of|eod|next|\d)",
    # other languages, e.g.: r"me encargo", r"ich kümmere mich", r"je m'en occupe"
]
ASSIGN = re.compile(r"\b[A-Z]\w+ (?:to|will|is going to) \w+")  # "Dan to circulate", "Mei will ask"
DECISION_CUES = [
    r"decided", r"decision", r"agreed", r"we're going with", r"go with", r"approved",
    r"signed off", r"settled on", r"final (?:call|answer)", r"vote[ds]?",
]
STATUS_CUES = [
    r"done", r"finished", r"complete[d]?", r"sent", r"shared", r"closed", r"merged", r"shipped",
    r"resolved", r"still", r"waiting", r"blocked", r"pending", r"in progress", r"not yet",
    r"slipped", r"pushed", r"delayed", r"cancel+ed", r"dropped", r"no longer",
]
# False for languages written without spaces between words (Chinese, Japanese, Thai).
WORD_BOUNDARIES = True


def cue_regex(cues):
    body = "|".join(f"(?:{c})" for c in cues)
    return re.compile(rf"(?<!\w)(?:{body})(?!\w)" if WORD_BOUNDARIES else body, re.I)


COMMIT, DECISION, STATUS = cue_regex(COMMIT_CUES), cue_regex(DECISION_CUES), cue_regex(STATUS_CUES)


def text_of(p):
    kind = p.suffix.lower()
    if kind == ".docx":
        from docx import Document
        doc = Document(p)
        rows = [" | ".join(c.text for c in r.cells) for t in doc.tables for r in t.rows]
        return "\n".join([x.text for x in doc.paragraphs] + rows)
    if kind == ".pdf":
        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(p).pages)
    return p.read_text(encoding="utf-8", errors="replace")


for p in sorted(FOLDER.iterdir()):
    if p.suffix.lower() not in (".md", ".txt", ".docx", ".pdf", ".vtt", ".srt"):
        continue
    print(f"\n== {p.name}")
    for n, line in enumerate(text_of(p).splitlines(), 1):
        line = line.replace("\u2019", "'")  # transcripts often use curly apostrophes: I’ll
        tags = ("C" if COMMIT.search(line) or ASSIGN.search(line) else "")
        tags += ("D" if DECISION.search(line) else "") + ("S" if STATUS.search(line) else "")
        if tags:
            print(f"{n:4} {tags:3} {line.strip()[:160]}")
```

The scan over-matches on purpose. A flagged line is a prompt to decide, not an
item. For a meeting in a language the lists do not cover, the scan finds
little: say so, add a few phrases in that language if you know them, and rely
on reading every line. `ASSIGN` assumes English word order and capitalised
names; it adds nothing in other languages.
