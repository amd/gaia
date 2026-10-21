---
name: meeting-actions
description: Pull every action item out of meeting notes, minutes or transcripts, including commitments made in passing ("I'll send it", "let me check"), with owner, due date and status, merged across meetings into one row per item, plus a separate log of decisions. Use when the user asks for action items, follow-ups, to-dos, owners, next steps, decisions, an action tracker, or what is still open from one or more meetings.
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
three times. A tracker that also takes in every decision and discussion point
buries the work in noise.

You produce two tables and a short list:

- **Action tracker**: things a person will do. One row per item.
- **Decision log**: things the meeting settled. Decisions go here, never in
  the tracker.
- **Questions for you**: items that need an owner or a date, for the user to
  answer.

## Procedure

1. **Read every meeting in full.** `list_files(path)` for a folder, then
   `read_file(file_path)` for each one, following `next_offset` until it is
   null. Put the meetings in date order, using the date in the heading first
   and the file name second.
2. **Find commitments** in each meeting. Keep all three kinds (recall):
   - **Explicit**: "Action:", "AI:", "TODO", "Next steps", "Owner:", action
     tables, "Sam to send the draft".
   - **Implicit, first person**: "I'll", "I will", "I'm going to", "let me
     check / find out / follow up", "I can take that", "leave it with me",
     "I'll get back to you", "we'll", "we can take that".
   - **Accepted requests**: "Can you update the doc?" answered "Sure" or
     "Will do". The person who said yes owns it.

   Then test every candidate (precision): **a named person (or group) took
   on a specific future task.** All four parts must hold: someone, took it on,
   a task you could check off, still to do. Not actions: ideas and
   hypotheticals ("we could", "maybe someone should"), questions nobody
   accepted, opinions, agenda items, summaries of what was said, and reports
   of finished work ("I sent it yesterday"), which are status updates for an
   existing item. See the borderline calls below.
3. **Find decisions** in the same pass: "we agreed", "decided", "we're going
   with", "approved", "signed off", "final answer is", a vote. A decision is
   settled; an action is work. "We decided to switch vendors" is a decision;
   "Dan will sign the new contract" is the action it creates. Log both.
4. **Run the cue scan** (below) with `run_python` as a safety net. Every line
   it flags `C` is either in your tracker or deliberately left out, every `D`
   is in the decision log or deliberately left out, and every `S` is a status
   update to attach to an item. The scan only points; your reading decides.
   Never drop an item you found by reading because the scan missed it.
5. **Fill in each action:**
   - **ID**: `A-001`, `A-002`, ... in order first raised. An ID never changes
     and is never reused: merged items keep the earlier ID, dropped items keep
     theirs. When updating a tracker the user already has, keep its IDs and
     continue its numbering.
   - **Action**: starts with a verb ("Send revised budget to finance").
   - **Owner**: the one person accountable for it. The speaker for "I'll" and
     "let me"; the named person for "Sam to"; `unassigned` when nobody took
     it. For "we", write the group and who said it: "team (raised by Priya)".
   - **Contributors**: others who were named as helping ("Lee will review",
     "with input from Ana"), only when stated. Never infer a role.
   - **Due**: as stated. Resolve relative dates against the meeting date and
     show both: "Friday (2024-05-10)". Leave it blank if no date was given.
     Do not invent one.
   - **Evidence**: meeting date and a short quote.
6. **Fill in each decision:** **ID** (`D-001`, ...), **Decision** (what was
   settled, as a statement), **Date**, **Decided by** (the person who made the
   call, or "group (consensus)"; "not stated" if the notes do not say),
   **Rationale** (the reason given, or blank; never supply one), **Source**
   (meeting and short quote). When a later meeting reverses a decision, add
   the new one and mark the old one "Superseded by D-00N".
7. **Merge across meetings.** Items about the same deliverable with the same
   owner are one row, even when the wording changes. Keep the date it was
   first raised, the date of its latest mention, and the status history.
   Merge across sources too: an item from the minutes that the user's own
   notes, an email or a chat restate is the same row, not a second one. A
   note to chase someone else's item ("chase Sam for the budget") is not a
   new action: it belongs on Sam's row, as a follow-up in Status or Evidence.
   Run the duplicate check (below) before saving.
8. **Set status only from what was said:**
   - `Open`: raised, no later mention.
   - `In progress`: a later meeting says it was started.
   - `Done`: someone says it was done, sent, merged or closed.
   - `Blocked`: waiting on something named.
   - `Dropped`: explicitly cancelled.
   - `Carried over`: mentioned again, still not done.

   `Done` needs a statement that it happened ("sent", "merged", "closed").
   A plan or a date for doing it ("we'll send it on the 9th") leaves it open.
   Never mark an item done because time passed or because a later meeting
   did not mention it.
9. **Count before and after.** Report how many commitments you found per
   meeting before merging and how many rows remain after. Every pre-merge
   item maps to exactly one row. Several actions per meeting is typical; far
   more than that usually means discussion or decisions were swept in, so
   re-test each row against step 2 before saving.
10. **List the questions for the user**: every open action with owner
    `unassigned` ("needs owner") or no due date ("needs date"), by ID, plus
    any "someone should..." that nobody took but that sounded important, as a
    question ("Nobody took the wiki update on 6 May. Track it?"). Ask; do not
    fill the gaps yourself.
11. **Deliver.** Tracker first, then the decision log, then the questions.
    Add the per-owner or overdue view (below) when the user asks who owes
    what or what is late. If the user asked for a file, save it with
    `write_file(file_path, content)` as Markdown or CSV where they asked;
    build a workbook (one sheet per table) with openpyxl in `run_python`.
    Then run the final check.

## Borderline calls (invented)

| Said in the meeting | Call | Why |
|---|---|---|
| Mei: "I'll have a look at the flaky test at some point." | **Action**, owner Mei, no date; on the "needs date" list | Vague timing, but a named person took on a specific task. Keep it (recall). |
| Priya: "Someone should really update the wiki." Nobody answers. | **Not an action**; ask the user | Nobody took it on. A row with a made-up owner would be false precision. |
| Dan: "Can you send me the slides?" Ana: "I'll try, this week is packed." | **Action**, owner Ana, noted "tentative" in Evidence | A hedged yes is still an accepted request. |
| "We agreed to drop the Friday release and ship on Tuesday." | **Decision** D-00N; an action only for the named work it creates ("Ravi to tell support about Tuesday") | Settling something is not work someone will do. |

## Cue scan

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

## Duplicate check and views

Keep the tracker as a list of dicts while you build it, then run this before
saving. It prints likely duplicates (same owner, or one row chasing the other's
owner, with most key words shared): merge each pair or say why they differ.
With `TODAY` set, it also prints the per-owner and overdue views.

```python
import re, difflib, itertools
from collections import defaultdict
from datetime import date

tracker = [  # one dict per row; due_iso is "" when no date was given
    {"id": "A-001", "task": "Send revised budget to finance", "owner": "Dan", "due_iso": "2024-05-10", "status": "Open"},
]
TODAY = date.today()  # or the date the user gives; say which date you used
CLOSED = ("Done", "Dropped")
STOP = {"the", "and", "for", "with", "will", "chase", "follow", "remind", "ping", "check"}


def words(t, names):
    return set(re.findall(r"\w{3,}", t.lower())) - STOP - names


def first_name(row):
    return (row["owner"].split() or [""])[0].lower()


def unowned(row):
    return row["owner"].strip().lower() in ("", "unassigned")


for ra, rb in itertools.combinations(tracker, 2):
    oa, ob = first_name(ra), first_name(rb)
    if oa != ob and not (ob and ob in ra["task"].lower()) and not (oa and oa in rb["task"].lower()):
        continue
    wa, wb = words(ra["task"], {oa, ob}), words(rb["task"], {oa, ob})
    jacc = len(wa & wb) / max(1, len(wa | wb))
    ratio = difflib.SequenceMatcher(None, ra["task"].lower(), rb["task"].lower()).ratio()
    if jacc >= 0.4 or ratio >= 0.6:
        print(f"possible duplicate: {ra['id']} / {rb['id']}: {ra['task'][:60]!r} ~ {rb['task'][:60]!r}")

by_owner = defaultdict(list)
for row in tracker:
    if not row["status"].startswith(CLOSED):
        by_owner["unassigned" if unowned(row) else row["owner"]].append(row)
for owner in sorted(by_owner):
    print(f"\n{owner}: " + "; ".join(f"{r['id']} {r['task']} (due {r['due_iso'] or '-'})" for r in by_owner[owner]))

overdue = [r for r in tracker if r["due_iso"] and date.fromisoformat(r["due_iso"]) < TODAY
           and not r["status"].startswith(CLOSED)]
print(f"\nOverdue as of {TODAY}:", [f"{r['id']} {r['owner']} due {r['due_iso']}" for r in overdue] or "none")
print("Needs owner:", [r["id"] for r in tracker if unowned(r) and not r["status"].startswith(CLOSED)])
print("Needs date:", [r["id"] for r in tracker if not r["due_iso"] and not r["status"].startswith(CLOSED)])
```

If the agent offers `extract_document_items`, you can use it for the first
pass over a long transcript. Ask for commitments "explicit or implied", with
owner, due and status fields, then do steps 5-10 on what it returns.

## Worked example (invented)

Meeting of 6 May:

```
Priya: I'll ask finance whether the old rate still applies.
Mei: Let me check the contract renewal date before Friday.
Dan: We could also look at a second vendor, maybe.
ACTION: Dan to circulate the draft budget by 10 May, Priya to review the numbers.
Priya: OK, agreed: the onboarding doc moves to the wiki, nobody reads the PDF.
Priya: Can you do the move, Mei?
Mei: Sure, I can take that.
```

Meeting of 13 May:

```
Priya: Finance confirmed the old rate holds until June, so that one's done.
Mei: Still waiting on legal for the renewal date.
Dan: I sent the draft budget on Thursday.
Dan: I'm going to set up a call with the second vendor next week.
```

**Action tracker**

| ID | Action | Owner | Contributors | Due | Status | First raised | Latest | Evidence |
|---|---|---|---|---|---|---|---|---|
| A-001 | Ask finance whether the old rate applies | Priya | | | Done | 6 May | 13 May | "so that one's done" |
| A-002 | Confirm contract renewal date | Mei | | Fri (2024-05-10) | Blocked (waiting on legal) | 6 May | 13 May | "Still waiting on legal" |
| A-003 | Circulate draft budget | Dan | Priya (review) | 2024-05-10 | Done | 6 May | 13 May | "I sent the draft budget on Thursday" |
| A-004 | Move onboarding doc to the wiki | Mei | | | Open | 6 May | 6 May | "Sure, I can take that" |
| A-005 | Set up call with second vendor | Dan | | week of 20 May | Open | 13 May | 13 May | "I'm going to set up a call" |

**Decision log**

| ID | Decision | Date | Decided by | Rationale | Source |
|---|---|---|---|---|---|
| D-001 | Onboarding doc moves to the wiki; the PDF is retired | 6 May | Priya (agreed in meeting) | Nobody reads the PDF | 6 May: "agreed: the onboarding doc moves to the wiki" |

**Questions for you**: A-004 has no due date. Want one set?

Four actions and one decision in the first meeting, one new action in the
second. Only one action was labelled ACTION. Dan's 6 May "we could" was an
idea; it became A-005 only when he committed on 13 May. The wiki move appears
twice, once as the decision (D-001) and once as Mei's work (A-004), and
that is right.

## Final check

Before handing over, list what the user asked for (which meetings, which
columns, a file name and folder, a filter such as "only mine" or "only open")
and check each against what you saved. Reopen the file rather than trusting
memory. Check that no decision sits in the tracker and every ID is unique. Say
plainly about anything you could not do, such as a meeting that would not
open or an owner the notes never name.

## Fork this

Change the columns to your team's tracker (priority, ticket link, project) and
point the cue lists at the phrases and languages your team actually uses ("I'll
own that", "AI for me"). The precision test, merge and status rules stay as
they are.
