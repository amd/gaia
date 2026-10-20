---
name: meeting-actions
description: Pull every action item out of meeting notes, minutes or transcripts, including commitments made in passing ("I'll send it", "let me check", "we can take that"), with owner, due date and status, merged across meetings into one row per item. Use when the user asks for action items, follow-ups, to-dos, owners, next steps, an action tracker, or what is still open from one or more meetings.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: community
    tools_required:
      - list_files
      - read_file
      - run_python
      - write_file
    pairs_with:
      - transcribe-meeting
    provenance:
      source: starter-pack
---

# Meeting Actions

Lines labelled "ACTION" are a minority of what people commit to in a meeting.
Most commitments are said in passing ("I'll ask finance", "let me check that"),
and the same item comes back in later meetings with a new status. A tracker
built from labelled lines alone misses most of the work and lists one item
three times.

## Procedure

1. **Read every meeting in full.** `list_files(path)` for a folder, then
   `read_file(file_path)` for each one, following `next_offset` until it is
   null. Put the meetings in date order, using the date in the heading first
   and the file name second.
2. **Find commitments of both kinds** in each meeting:
   - **Explicit**: "Action:", "AI:", "TODO", "Next steps", "Owner:", action
     tables, "Sam to send the draft".
   - **Implicit, first person**: "I'll", "I will", "I'm going to", "let me
     check / find out / follow up", "I can take that", "leave it with me",
     "I'll get back to you", "we'll", "we can take that".
   - **Accepted requests**: "Can you update the doc?" answered "Sure" or
     "Will do". The person who said yes owns it.

   Not commitments: ideas and hypotheticals ("we could", "maybe someone
   should"), questions nobody accepted, and reports of finished work ("I sent
   it yesterday"), which are status updates for an existing item.
3. **Run the cue scan** (below) with `run_python` as a safety net. Every line
   it flags `C` is either in your list or deliberately left out. Every line
   flagged `S` is a status update to attach to an item.
4. **Fill in each item:**
   - **Action**: starts with a verb ("Send revised budget to finance").
   - **Owner**: the speaker for "I'll" and "let me"; the named person for "Sam
     to"; `unassigned` when nobody took it. For "we", write the group and who
     said it: "team (raised by Priya)".
   - **Due**: as stated. Resolve relative dates against the meeting date and
     show both: "Friday (2024-05-10)". Leave it blank if no date was given.
     Do not invent one.
   - **Evidence**: meeting date and a short quote.
5. **Merge across meetings.** Items about the same deliverable with the same
   owner are one row, even when the wording changes. Keep the date it was
   first raised, the date of its latest mention, and the status history.
   Merge across sources too: an item from the minutes that the user's own
   notes, an email or a chat restate is the same row, not a second one. Before
   saving, compare every pair of rows with the same owner and similar wording
   and fold any duplicates together, keeping every source in Evidence.
6. **Set status only from what was said:**
   - `Open`: raised, no later mention.
   - `In progress`: a later meeting says it was started.
   - `Done`: someone says it was done, sent, merged or closed.
   - `Blocked`: waiting on something named.
   - `Dropped`: explicitly cancelled.
   - `Carried over`: mentioned again, still not done.

   Never mark an item done because time passed or because a later meeting
   did not mention it.
7. **Count before and after.** Report how many commitments you found per
   meeting before merging and how many rows remain after. Every pre-merge
   item maps to exactly one row.
8. **Deliver the tracker.** If the user asked for a file, save it with
   `write_file(file_path, content)` as Markdown or CSV where they asked; build
   a workbook with openpyxl in `run_python`. Then run the final check below.

If the agent offers `extract_document_items`, you can use it for the first
pass over a long transcript. Ask for commitments "explicit or implied", with
owner, due and status fields, then do steps 4-7 on what it returns.

## Cue scan

```python
import re
from pathlib import Path

FOLDER = Path(r"C:\Users\me\Documents\team-sync-notes")
COMMIT = re.compile(
    r"\b(I'll|I will|I'm going to|I can (?:take|do|handle|send|look)|let me|leave it with me|on me|"
    r"we'll|we will|we can take|will do|sure|can you|could you|please|action|to-?do|next steps?|"
    r"follow[- ]up|owner|due|by (?:mon|tue|wed|thu|fri|end of|eod|next|\d))", re.I)
ASSIGN = re.compile(r"\b[A-Z][a-z]+ (?:to|will|is going to) [a-z]+")  # "Dan to circulate", "Mei will ask"
STATUS = re.compile(
    r"\b(done|finished|complete[d]?|sent|shared|closed|merged|shipped|resolved|still|waiting|blocked|"
    r"pending|in progress|not yet|slipped|pushed|delayed|cancel+ed|dropped|no longer)\b", re.I)

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
        tags = ("C" if COMMIT.search(line) or ASSIGN.search(line) else "") + ("S" if STATUS.search(line) else "")
        if tags:
            print(f"{n:4} {tags:2} {line.strip()[:160]}")
```

The scan over-matches on purpose. A flagged line is a prompt to decide, not an
item.

## Worked example (invented)

Meeting of 6 May:

```
Priya: I'll ask finance whether the old rate still applies.
Mei: Let me check the contract renewal date before Friday.
Dan: We could also look at a second vendor, maybe.
ACTION: Dan to circulate the draft budget by 10 May.
Priya: Can you update the onboarding doc, Mei?
Mei: Sure, I can take that.
```

Meeting of 13 May:

```
Priya: Finance confirmed the old rate holds until June, so that one's done.
Mei: Still waiting on legal for the renewal date.
Dan: I sent the draft budget on Thursday.
Dan: I'm going to set up a call with the second vendor next week.
```

| # | Action | Owner | Due | Status | First raised | Latest | Evidence |
|---|---|---|---|---|---|---|---|
| 1 | Ask finance whether the old rate applies | Priya | | Done | 6 May | 13 May | "so that one's done" |
| 2 | Confirm contract renewal date | Mei | Fri (2024-05-10) | Blocked (waiting on legal) | 6 May | 13 May | "Still waiting on legal" |
| 3 | Circulate draft budget | Dan | 2024-05-10 | Done | 6 May | 13 May | "I sent the draft budget on Thursday" |
| 4 | Update onboarding doc | Mei | | Open | 6 May | 6 May | "Sure, I can take that" |
| 5 | Set up call with second vendor | Dan | week of 20 May | Open | 13 May | 13 May | "I'm going to set up a call" |

Four commitments in the first meeting, one new one in the second, five rows.
Only one of them was labelled ACTION. Dan's 6 May "we could" was an idea; it
became item 5 only when he committed on 13 May.

## Final check

Before handing over, list what the user asked for (which meetings, which
columns, a file name and folder, a filter such as "only mine" or "only open")
and check each against what you saved. Reopen the file rather than trusting
memory. Say plainly about anything you could not do, such as a meeting that
would not open or an owner the notes never name.

## Fork this

Change the columns to your team's tracker (priority, ticket link, project) and
point the scan at the phrases your team actually uses ("I'll own that", "AI
for me"). The merge and status rules stay as they are.
