# Fallback: when triage_logs is not available or cannot parse

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
