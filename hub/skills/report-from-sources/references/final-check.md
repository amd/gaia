# Final requirements check

Do this before saying any deliverable is done.

1. **Turn the request into a numbered checklist.** One line for each thing the
   user named: every section, chart, table, column and metric; every ID, item
   or reference ("cite every ticket mentioned" means each one, by ID); every
   structural ask (a labelled input cell, a toggle or dropdown, a sheet in a
   given place); every file name, file type and folder; any length, audience or
   style limit. Always add three lines of your own: the as-of line in the
   header, the summary before the evidence, and the Conflicts and gaps section.
2. **Check each line against the saved file.** Reopen it; your memory of what
   you wrote is not evidence.
3. **Fix the gaps and check again.**
4. **Report the checklist** with each line marked met or not met. Say plainly
   what is not met and why. Never mark a line met that you did not verify.

Example request: *"Build a workbook from the three survey exports with a
Summary sheet, a chart of satisfaction by region, and a labelled input at the
top of Summary to switch between 2023 and 2024. Save it as survey_summary.xlsx
next to the exports."*

1. Uses all three exports
2. Sheet named Summary
3. Chart of satisfaction by region
4. Input cell at the top of Summary, with a label beside it
5. The input is a 2023/2024 dropdown, and the Summary formulas read it
6. File name survey_summary.xlsx
7. Saved in the exports folder

To check that every ID in the sources made it in, append this to the
`deliverable_lines` snippet in `references/coverage-checks.md` and run them
together (each `run_python` call starts fresh):

```python
source_text = Path(r"<source text file>").read_text(encoding="utf-8")
ID = re.compile(r"\b[A-Z]{2,}-\d+\b")  # fit the pattern to the IDs in front of you
missing = sorted(set(ID.findall(source_text)) - set(ID.findall("\n".join(lines))))
print("IDs missing from the deliverable:", missing or "none")
```

For a document source, take the text from `read_file` instead of `read_text`.

To check a workbook's inputs, dropdowns and charts:

```python
import re
from openpyxl import load_workbook

wb = load_workbook(r"<saved workbook>")  # absolute path of the file you built
formulas = [c.value for s in wb for row in s.iter_rows() for c in row
            if isinstance(c.value, str) and c.value.startswith("=")]
print("sheets:", wb.sheetnames, "| formulas:", len(formulas))
for ws in wb:
    print(f"[{ws.title}] {ws.max_row} rows, {len(ws._charts)} charts")
    for dv in ws.data_validations.dataValidation:
        for rng in str(dv.sqref).split():
            cell = ws[rng.split(":")[0]]
            label = ws.cell(cell.row, cell.column - 1).value if cell.column > 1 else None
            ref = re.compile(rf"(?<![A-Za-z$])\$?{cell.column_letter}\$?{cell.row}(?!\d)")
            users = sum(1 for f in formulas if ref.search(f))
            print(f"  input {rng} label={label!r} options={dv.formula1} value={cell.value!r} "
                  f"formulas reading it={users}")
```

An input that no formula reads (`formulas reading it=0`) is decoration, not a
toggle.
