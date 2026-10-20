---
name: log-triage
description: Find what actually went wrong in log files. Use when the user asks for a root cause, an incident summary, anomalies, errors, failures or "what happened" in system, application, server, cluster, device or audit logs. Covers each file's full time range, groups messages into templates, ranks by severity, rarity and number of hosts hit rather than by volume, finds bursts, and gives exact first and last timestamps.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: community
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
multi-day file. Counting by volume finds the noise; this procedure finds the
incident.

## Procedure

1. **Inventory every log.** `list_files(path)` for the folder. Every file the
   user gave you is in scope, and every file is analysed over its full time
   range, not the first page.
2. **Learn the line format from a sample.** `read_file(file_path,
   start_line=1, end_line=40)`, plus a slice from the middle of the file.
   Never read a whole large log into the conversation: you will see the start
   and report it as the whole.
3. **Parse, template and rank with `run_python`** (snippet below). Fit the
   `LINE` pattern to the sample. If more than a few percent of lines do not
   parse, fix the pattern before going on. Indented continuation lines (stack
   traces) are the only acceptable leftovers.
4. **Rank templates by severity, then hosts hit, then rarity**, never by count.
   Read the table this way:
   - **Baseline**: present across most of the range (`span_pct` near 100) at a
     steady rate, whatever its level. Routine, even when it says "error" or
     "fatal": a platform that logs thousands of FATAL lines every day is
     telling you its normal, not an incident. Never count baseline lines as
     alerts or add them to an anomaly total; report them once as background.
   - **Anomaly**: a high-severity template, a template that appears in only a
     short window (`span_pct` near 0), a high `peak` against its own normal
     rate, or a template that hit many distinct hosts at once.
5. **Find the bursts** (second snippet): time buckets where errors jump far
   above their median. For each burst, record the exact first and last
   timestamp, how many lines, how many distinct hosts, and which templates
   occur only there (`share_of_all` near 1.0).
6. **Read around the first burst line.** With the burst's `file` and `line`,
   `read_file(file_path, start_line=line - 30, end_line=line + 10)` shows what
   happened just before. The cause comes before its symptoms. A flood of
   timeouts after a hardware fault is the effect, not the root cause.
7. **Check the numbers add up.** Template counts sum to the parsed line count
   (the snippet asserts this). Per-level counts sum to the same total. Hosts
   in a burst are no more than total hosts. Every timestamp you quote is
   copied from the output, not rounded.
8. **Derive everything from the logs you were given.** Do not fill in results
   from a public dataset you recognise, from memory, or from a labels or
   expected-results file found beside the logs, unless the user says it is an
   input. If you find such a file, mention it and leave it unused.

## Parse, template, rank

```python
import os, re, tempfile
import pandas as pd

PATHS = [r"C:\Users\me\logs\app-01.log", r"C:\Users\me\logs\app-02.log"]
# Fit this to the sample you read. Example line:
# 2024-03-04 00:00:05,760 node-31 WARN cache miss for key 0x63ca828d
LINE = re.compile(r"^(?P<ts>\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d)\S*\s+(?P<host>\S+)\s+(?P<level>[A-Z]+)\s+(?P<msg>.*)$")
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

rows, unparsed = [], {}
for path in PATHS:
    with open(path, encoding="utf-8", errors="replace") as f:
        for n, line in enumerate(f, 1):
            m = LINE.match(line.rstrip("\n"))
            if m:
                rows.append({**m.groupdict(), "file": os.path.basename(path), "line": n})
            elif line.strip():
                unparsed.setdefault(path, []).append(n)
df = pd.DataFrame(rows)
df["ts"] = pd.to_datetime(df["ts"].str.replace("T", " "), errors="coerce")
df["sev"] = df["level"].str.upper().map(SEVERITY).fillna(1).astype(int)
df["tpl"] = df["msg"].map(template)
print("parsed", len(df), "| bad timestamps", int(df["ts"].isna().sum()), "| hosts", df["host"].nunique())
for path, lines in unparsed.items():
    print("unparsed", len(lines), "in", path, "e.g. lines", lines[:5])
print(df.groupby("file")["ts"].agg(["min", "max", "size"]).to_string())  # every file, full range
print(df["level"].value_counts().to_string())

span = df["ts"].max() - df["ts"].min()
bucket = next((b for b in ["1min", "5min", "15min", "1h", "6h", "1D"] if span / pd.Timedelta(b) <= 500), "7D")
df["bucket"] = df["ts"].dt.floor(bucket)
g = df.groupby("tpl").agg(count=("tpl", "size"), sev=("sev", "max"), hosts=("host", "nunique"),
                          first=("ts", "min"), last=("ts", "max"))
g["span_pct"] = ((g["last"] - g["first"]) / span * 100).round(0)
g["peak_per_" + bucket] = df.groupby(["tpl", "bucket"]).size().groupby(level=0).max()
assert g["count"].sum() == len(df), "template counts do not add up to parsed lines"
print(len(g), "templates; ranked by severity, then hosts hit, then rarity:")
pd.set_option("display.width", 250, "display.max_colwidth", 70)
print(g.sort_values(["sev", "hosts", "count"], ascending=[False, False, True]).head(15).to_string())
df.to_pickle(os.path.join(tempfile.gettempdir(), "log_triage.pkl"))  # the burst step reads this
```

Adapting it:

- **No level field?** Map keywords instead: lines matching
  `fatal|panic|corrupt|crash|abort|out of memory` get 4, `error|fail|exception|timeout|refused` get 3.
- **No host field?** Use the component, process, or device ID as `host`, so
  "hosts hit" still measures spread.
- **No year in the timestamp** (`Mar  5 14:22:01`)? Add the year from the file
  name or context and say you did.
- **Very large files** (millions of lines): raise `run_python`'s `timeout`.

## Bursts

```python
import os, tempfile
import pandas as pd

df = pd.read_pickle(os.path.join(tempfile.gettempdir(), "log_triage.pkl"))
span = df["ts"].max() - df["ts"].min()
bucket = next((b for b in ["1min", "5min", "15min", "1h", "6h", "1D"] if span / pd.Timedelta(b) <= 500), "7D")
step = pd.Timedelta(bucket)
bad = df[df["sev"] >= 3].set_index("ts").sort_index()  # errors and worse; >= 2 adds warnings
counts = bad.resample(bucket).size()
threshold = max(10, 5 * counts.median())
hot = list(counts[counts >= threshold].index)
print(f"bucket {bucket}, median {counts.median():.0f}/bucket, threshold {threshold:.0f}, hot buckets {len(hot)}")

bursts = []  # adjacent hot buckets merge into one burst
for t in hot:
    if bursts and t - bursts[-1][1] <= step:
        bursts[-1][1] = t
    else:
        bursts.append([t, t])
overall = df["tpl"].value_counts()
for start, end in bursts:
    win = bad[(bad.index >= start) & (bad.index < end + step)].reset_index()
    print(f"\nBURST {start} -> {end + step}: {len(win)} lines, {win['host'].nunique()} hosts")
    t = win.groupby("tpl").agg(count=("tpl", "size"), sev=("sev", "max"), hosts=("host", "nunique"),
                               first=("ts", "min"), last=("ts", "max"))
    t["share_of_all"] = (t["count"] / overall.reindex(t.index)).round(2)  # 1.0 = happens only here
    t = t.join(win.sort_values("ts").groupby("tpl")[["file", "line"]].first())  # where it starts
    print(t.sort_values(["sev", "share_of_all"], ascending=False).head(5).to_string())
```

No hot buckets does not mean no incident: re-run with `sev >= 2`, a smaller
bucket, or a single template's rows.

## Report shape

- **Coverage**: each file with its first and last timestamp and line count,
  plus the unparsed count.
- **Incident(s)**: what happened, in one line each. Exact first and last
  timestamp, severity, line count, hosts hit (count plus a few names).
- **Timeline**: the few lines before the first incident line, then the
  sequence.
- **Likely cause**: the evidence for it, worded as likely unless the log
  states it outright. Name symptoms as symptoms.
- **Baseline**: the top routine templates and why they are routine (steady
  across the whole range).
- **Limits**: unparsed lines, gaps in time, hosts or files with no data.

Example (invented): *"Incident: disk controller faults on 31 of 40 nodes,
2024-03-06 13:13:00 to 13:16:58, 120 FATAL lines, a template seen nowhere else
in the 3.5-day log. The 3,002 request timeouts are baseline: steady across the
whole range, about 9 per 15 minutes."*

## Fork this

Pin `LINE`, `SEVERITY` and the host field to your platform's log format, and
add the templates your team already knows are harmless to a baseline list, so
the ranking starts from the unfamiliar.
