# Duplicate check and views

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
owner, due and status fields, then fill, merge and check what it returns
as usual.

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
