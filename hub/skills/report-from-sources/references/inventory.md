# Inventory the inputs

**Call `inventory_data_files(folder)` first, then interpret its result.** One
call returns, for every spreadsheet, CSV and JSON file, each sheet's layout,
header rows (multi-row and merged headers), units, the periods it covers
(first, last, and last with data) and its series names. It groups files that
hold the same dataset and ranks them by vintage: `datasets[].note` names the
newest copy and the older ones ("two versions of the same series: X (through
2026Q2, newest) and Y (through 2025Q2)"). Documents come back under
`narrative_sources`; read those for the ledger (`references/ledger.md`).

From that result, record for every file what it covers and which period it is
for, and show the table in your reply before you build anything:

| File | Covers | Period / as-of | Found in | Use |
|---|---|---|---|---|
| sales_2023.xlsx | Regional sales | Jan-Dec 2023 | Month column | prior-year comparison |
| sales_2024_q3.xlsx | Regional sales | Jan-Sep 2024 | sheet title | **primary** |
| board_memo.docx | Quarter commentary | as of 30 Sep 2024 | first paragraph | facts ledger |

- The tool ranks by the data the file holds (last period with data), then an
  "as of" line in it, then the period or revision in the file name, then the
  modified time. Use the newest unless the user named a period; then use the
  file that covers it. Older files are for comparison only, and are labelled
  as such.
- When the note says two copies cover the same periods, say which one you used
  and why (later revision, "final" over "draft"), and compare their values.
- A file under `not_read`, or a sheet with layout `unknown`, the tool could
  not parse. Open it with the fallback below.

**Fallback when the tool can't parse a file.** List the folder with
`list_files(path)`; this lists every spreadsheet's sheets, row counts and date
ranges:

```python
import re
from datetime import datetime
from pathlib import Path
import pandas as pd

FOLDER = Path(r"<sources folder>")  # the absolute path you inventoried
DATEISH = re.compile(r"date|month|year|period|quarter|week|day|as.?of", re.I)
for p in sorted(FOLDER.iterdir()):
    if not p.is_file():
        continue
    print(f"\n== {p.name}  (modified {datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d})")
    years = sorted(set(re.findall(r"(?:19|20)\d\d", p.stem)))
    if years:
        print("   years in file name:", years)
    if p.suffix.lower() in (".csv", ".xlsx", ".xlsm", ".xls"):
        sheets = {"csv": pd.read_csv(p)} if p.suffix.lower() == ".csv" else pd.read_excel(p, sheet_name=None)
        for name, df in sheets.items():
            print(f"   [{name}] {len(df)} rows; columns: {list(df.columns)[:12]}")
            for col in df.columns:
                if DATEISH.search(str(col)):
                    vals = df[col].dropna()
                    if len(vals):
                        print(f"     {col}: {vals.min()} .. {vals.max()}")
```

For documents, the period comes from reading them for the ledger.
