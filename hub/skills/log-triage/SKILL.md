---
name: log-triage
description: Find what actually went wrong in log files. Use when the user asks for a root cause, an incident summary, anomalies, errors, failures or "what happened" in system, application, server, cluster, device or audit logs. Its triage_logs tool reads every file over its full range, keeps steady baseline noise apart from anomalies, finds bursts with exact first and last timestamps, and lists the hosts and ids an incident hit.
license: MIT
version: 1.1.0
metadata:
  gaia:
    security_tier: community
    tools:
      - name: triage_logs
        description: Parse, template and rank log files; return anomalies, bursts, baseline, coverage, entities and checks.
        parameters:
          paths:
            type: array
            required: true
          glob:
            type: string
            required: false
            default: ""
          timestamp_regex:
            type: string
            required: false
            default: ""
          level_regex:
            type: string
            required: false
            default: ""
          host_regex:
            type: string
            required: false
            default: ""
          entity_regex:
            type: string
            required: false
            default: ""
          bucket:
            type: string
            required: false
            default: ""
          top_n:
            type: integer
            required: false
            default: 10
          output_path:
            type: string
            required: false
            default: ""
        returns:
          type: object
    tools_required:
      - list_files
      - read_file
      - run_python
    provenance:
      source: starter-pack
---

# Log Triage

The loudest message in a log is usually routine. The incident is the rare,
severe message that hit many hosts at once, often in a few minutes of a
multi-day file. Counting by volume finds the noise; this skill finds the
incident.

## Call `triage_logs` first, then interpret

`log-triage/triage_logs(paths=[folder or files])` reads every file over its full
range (streaming, read-only, any size, `.gz` included) and returns one compact
result. Never read a large log into the conversation instead: you will see the
start and report it as the whole.

Read the result in this order:

1. **`checks`** — `ok` must be true (template and level counts add up to the
   parsed entries). If `parsed_share_ok` is false, a file in
   `files_below_parsed_share` was misread: look at its `unparsed_samples`,
   then call again with `timestamp_regex` (and `level_regex` / `host_regex` if
   needed) fitted to those lines. Do not report from a misread file.
2. **`coverage`** — every file with format, first and last timestamp, line,
   entry, continuation (stack-trace) and unparsed counts. `times_in` says
   whether times are UTC (offsets in the logs were converted) or as written.
3. **`anomalies`** — templates ranked by severity, then hosts hit, then rarity.
   Each has `why` (short window, new, spike against its own median,
   escalating, many hosts at once), exact `first` / `last` / `peak_at` with
   `file` and `line`, `hosts` and `host_sample`.
4. **`bursts`** — windows where errors and anomaly lines rose far above their
   median: exact `start` / `end`, `lines`, `hosts`, `first_line`, the
   templates found `only_here`, and `vs_quiet_window`: the same-length quiet
   stretch just before, with what `rose` and what `fell_silent` (a heartbeat
   or a host's routine traffic stopping is often the clearest clue).
5. **`baseline`** — templates steady across the range. Routine, even when they
   say ERROR or FATAL: a platform that logs thousands of FATAL lines a day is
   telling you its normal. Report them once as background; never add them to
   an anomaly or alert total.
6. **`timeline`**, **`clock_offsets`**, **`quiet_hosts`**, **`warnings`** —
   the cross-file order of first occurrences, steady offsets measured on ids
   that two files share (clock skew, a pipeline delay, or close to whole hours:
   a time zone written without an offset), hosts that stopped logging early,
   and everything the tool could not do. Repeat every warning that limits your
   answer.

Then:

- **Read before the first burst line.** `next_step` names it:
  `read_file(file_path, start_line=line - 30, end_line=line + 10)`. Causes come
  before their symptoms; a flood of timeouts after a hardware fault is the
  effect.
- **Quote, do not round.** Every timestamp, count and host number in your
  answer is copied from the result. When `clock_offsets` reports a skew, say
  that cross-file order inside that offset is uncertain.
- **Narrow when it helps.** `glob="*.log"` for a mixed folder, `bucket="1min"`
  for a short incident, `entity_regex` for the id the user asked about,
  `output_path` (a new `.csv` or `.txt` for the entity list, `.json` for full
  detail, in a folder you may write) when the user wants the full list.

## Lists of anomalous entities

When the user asks for a list of bad items (blocks, requests, sessions, jobs,
backends), **deliver the list from `triage_logs` output; do not rebuild it by
hand.** `entities` is built only from anomaly templates, inside each anomaly's
own window, never from baseline errors.

- `entities.list` is the ready list (up to 150 ids). For a longer one, call
  again with `output_path="<folder>/anomalous_entities.csv"` (or `.txt`, one
  id per line) and give the user that file. Say how many were flagged out of
  `total` (`share`) and which template put each group there (`by_template`).
- `kinds_used` says which id pattern the tool learned from the data (for
  example `blk_N` with `regex` `(blk_-?\d+)`), and `kinds_skipped` why other
  id-shaped tokens (peers, users, client addresses) were left out. If the user
  means a different id, call again with `entity_regex` set to that kind's
  `regex`.
- If `narrowed` is present, the broad anomaly templates were left out because
  they matched the baseline; the list comes from the narrower ones. Say so.
  A warning that most entities are flagged means the list is not the
  anomaly: report that instead of the list.
- If you must build a list yourself, use only the anomaly templates the tool
  lists (their `id`s and `template` text), never every WARN or ERROR line,
  and check your share against the tool's.

## Report shape: a blameless postmortem

Describe systems and events, not people. In this order:

1. **Summary** — what happened, in one or two sentences, with the exact window.
2. **Impact** — in user terms: which requests, jobs, sessions or hosts failed,
   how many (`lines`, `hosts`, flagged `entities`), for how long.
3. **Detection** — the first signal and when it appeared (`first` of the
   earliest anomaly, the burst `first_line`), and what would have caught it.
4. **Timeline** — UTC when `times_in` says so; the lines before the first
   anomaly, then the sequence across files. Use the `timeline` entries and
   each burst's `start` and `end` exactly as the tool printed them (same
   digits, same milliseconds); never round, re-derive or estimate them.
5. **Contributing factors** — plural. The trigger and what let it spread or
   last, each with its evidence, worded as likely unless the log states it.
   Name symptoms as symptoms.
6. **Mitigation and recovery** — what the logs show ended it (a restart, a
   failover, the burst `end`); if nothing in the logs shows recovery, say so.
7. **Action items** — each with an owner taken from the user's context, or
   marked "owner: to assign"; never invent a person.
8. **Confidence** — high, medium or low, and why: parse coverage, clock
   offsets, files or hosts with no data, a cause inferred rather than logged.

Close with **Baseline** (the top routine templates and why they are routine)
and **Limits** (unparsed lines, gaps, warnings).

Example (invented): *"Summary: disk controller faults on 31 of 40 nodes,
2024-03-06 13:13:00 to 13:16:58 UTC, 120 FATAL lines from a template seen
nowhere else in the 3.5-day log. Impact: 31 jobs aborted. The 3,002 request
timeouts are baseline: steady across the whole range, about 9 per 15 minutes."*

## Derive everything from the logs you were given

Do not fill in results from a public dataset you recognise, from memory, or
from a labels or expected-results file found beside the logs, unless the user
says it is an input. If you find such a file, mention it and leave it unused.

## Fallback: when `triage_logs` is not available or cannot parse

If the tool is not registered, or a file stays below the parsed share even
with `timestamp_regex`, do the same analysis yourself with `run_python`:

1. `list_files(path)`; every file is in scope over its full range.
2. Learn the line format: `read_file(file_path, start_line=1, end_line=40)`
   plus a slice from the middle.
3. Parse, template and rank (snippet below). Fit `LINE` to the sample; the
   snippet stops when under 95% of lines parse. Indented continuation lines
   (stack traces) are the only acceptable leftovers.
4. Rank by severity, then hosts hit, then rarity; baseline is `span_pct` near
   100 at a steady rate, whatever its level.
5. Find bursts (second snippet), compare each with the quiet window before
   it, and read around the first burst line.

```python
import os, re, tempfile
import pandas as pd

PATHS = [r"C:\Users\me\logs\app-01.log", r"C:\Users\me\logs\app-02.log"]
# Fit this to the sample you read. Example line:
# 2024-03-04 00:00:05,760 node-31 WARN cache miss for key 0x63ca828d
LINE = re.compile(r"^(?P<ts>\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d\S*)\s+(?P<host>\S+)\s+(?P<level>[A-Z]+)\s+(?P<msg>.*)$")
CONT = re.compile(r"^(\s|at |Caused by:|\.\.\. \d+ more|Traceback|\w+(\.\w+)*(Exception|Error)\b)")
MASKS = [
    (r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", "<ID>"),
    (r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b", "<IP>"),
    (r"\b0x[0-9a-fA-F]+\b", "<HEX>"),
    (r"(?<!\w)(?:/[\w.-]+){2,}", "<PATH>"),
    (r"\d+", "<N>"),
]
SEVERITY = {"TRACE": 0, "DEBUG": 0, "INFO": 1, "NOTICE": 1, "WARN": 2, "WARNING": 2,
            "ERROR": 3, "ERR": 3, "SEVERE": 3, "CRITICAL": 4, "CRIT": 4, "FATAL": 4,
            "ALERT": 4, "EMERG": 4, "PANIC": 4}

def template(msg):
    for pattern, token in MASKS:
        msg = re.sub(pattern, token, msg)
    return msg

rows, unparsed, cont = [], {}, 0
for path in PATHS:
    with open(path, encoding="utf-8", errors="replace") as f:
        for n, line in enumerate(f, 1):
            m = LINE.match(line.rstrip("\n"))
            if m:
                rows.append({**m.groupdict(), "file": os.path.basename(path), "line": n})
            elif CONT.match(line):
                cont += 1
            elif line.strip():
                unparsed.setdefault(path, []).append(n)
df = pd.DataFrame(rows)
bad = sum(len(v) for v in unparsed.values())
share = len(df) / max(1, len(df) + bad)
print(f"parsed {len(df)}, continuation {cont}, unparsed {bad} ({share:.1%} parsed)")
for path, lines in unparsed.items():
    print("unparsed", len(lines), "in", path, "e.g. lines", lines[:5])
assert share >= 0.95, "under 95% of lines parsed: fix LINE before going on"
# utc=True converts lines that carry an offset; lines without one are taken as written.
df["ts"] = pd.to_datetime(df["ts"].str.replace(",", "."), errors="coerce", utc=True, format="mixed")
df["sev"] = df["level"].str.upper().map(SEVERITY).fillna(1).astype(int)
df["tpl"] = df["msg"].map(template)
print(df.groupby("file")["ts"].agg(["min", "max", "size"]).to_string())  # every file, full range

span = df["ts"].max() - df["ts"].min()
bucket = next((b for b in ["1min", "5min", "15min", "1h", "6h", "1D"] if span / pd.Timedelta(b) <= 500), "7D")
df["bucket"] = df["ts"].dt.floor(bucket)
g = df.groupby("tpl").agg(count=("tpl", "size"), sev=("sev", "max"), hosts=("host", "nunique"),
                          first=("ts", "min"), last=("ts", "max"))
g["span_pct"] = ((g["last"] - g["first"]) / span * 100).round(0)
g["peak_per_" + bucket] = df.groupby(["tpl", "bucket"]).size().groupby(level=0).max()
assert g["count"].sum() == len(df), "template counts do not add up to parsed lines"
pd.set_option("display.width", 250, "display.max_colwidth", 70)
print(g.sort_values(["sev", "hosts", "count"], ascending=[False, False, True]).head(15).to_string())
df.to_pickle(os.path.join(tempfile.gettempdir(), "log_triage.pkl"))  # the burst step reads this
```

```python
import os, tempfile
import pandas as pd

df = pd.read_pickle(os.path.join(tempfile.gettempdir(), "log_triage.pkl"))
span = df["ts"].max() - df["ts"].min()
bucket = next((b for b in ["1min", "5min", "15min", "1h", "6h", "1D"] if span / pd.Timedelta(b) <= 500), "7D")
step = pd.Timedelta(bucket)
bad = df[df["sev"] >= 3].set_index("ts").sort_index()  # errors and worse; >= 2 adds warnings
counts = bad.resample(bucket).size()
threshold = max(10, 3 * counts.median(), counts.median() + 6 * counts.std())
hot = list(counts[counts >= threshold].index)
bursts = []  # adjacent hot buckets merge into one burst
for t in hot:
    if bursts and t - bursts[-1][1] <= step:
        bursts[-1][1] = t
    else:
        bursts.append([t, t])
overall = df["tpl"].value_counts()
for start, end in bursts:
    win = bad[(bad.index >= start) & (bad.index < end + step)].reset_index()
    print(f"\nBURST {win['ts'].min()} -> {win['ts'].max()}: {len(win)} lines, {win['host'].nunique()} hosts")
    t = win.groupby("tpl").agg(count=("tpl", "size"), sev=("sev", "max"), hosts=("host", "nunique"))
    t["share_of_all"] = (t["count"] / overall.reindex(t.index)).round(2)  # 1.0 = happens only here
    print(t.sort_values(["sev", "share_of_all"], ascending=False).head(5).to_string())
    length = end + step - start  # the quiet window: the same length, just before
    quiet = df[(df["ts"] >= start - length) & (df["ts"] < start)]["tpl"].value_counts()
    burst = df[(df["ts"] >= start) & (df["ts"] < end + step)]["tpl"].value_counts()
    both = pd.concat({"burst": burst, "quiet": quiet}, axis=1).fillna(0).astype(int)
    print("rose:\n", both[both["burst"] >= 2 * both["quiet"] + 3].head(5).to_string())
    print("fell silent:\n", both[(both["quiet"] >= 5) & (both["burst"] * 4 <= both["quiet"])].head(3).to_string())
```

No level field: map keywords (`fatal|panic|corrupt|crash|abort|out of memory`
gives 4, `error|fail|exception|timeout|refused` gives 3). No host field: use
the component, process or file name, so "hosts hit" still measures spread. No
year (`Mar  5 14:22:01`): take it from the file date and say you did.

## Fork this

Pin `timestamp_regex`, `host_regex` and `entity_regex` to your platform's
format, and keep a list of the templates your team already knows are harmless,
so the ranking starts from the unfamiliar.
