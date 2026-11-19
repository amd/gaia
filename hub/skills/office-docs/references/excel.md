# Excel workbooks (openpyxl)

- Raw data on its own sheet, results on another. Totals inside the workbook
  are **formulas** over the data sheet, so it stays live when the user edits
  it.
- A pivot-style table is grouped rows of `SUMIFS`/`COUNTIFS` over the data
  sheet. The criteria range must be the column that holds the criteria values,
  and the sum range the column with the numbers — compute the column letters
  from the header row, never by counting by eye.
- Build every formula's references from the row and column indexes you wrote
  the data to (`get_column_letter`), not from hand-typed addresses. When you
  copy a formula pattern across columns, check which references must stay
  fixed (`$A2`, `Data!$C:$C`).
- A formula never references its own cell or a total that includes itself.
- Newer functions (XLOOKUP, IFS, STDEV.S, ...) must be written with the
  `_xlfn.` prefix, e.g. `=_xlfn.XLOOKUP(...)`, or Excel shows `#NAME?`.
- Store numbers as numbers, dates as `datetime`, never as formatted strings;
  apply `number_format` (`'#,##0'`, `'0.0%'`, `'yyyy-mm-dd'`) for display.
- Charts come from `openpyxl.chart` (`BarChart`, `LineChart`, `PieChart`),
  referencing cells — never a pasted image.
- openpyxl does not compute formulas. Run this skill's own tool,
  `office-docs/verify_workbook`, on the saved file. "What the check returns"
  below says how to read its result.

```python
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.utils import get_column_letter

rows = [("East", "Q1", 120), ("West", "Q1", 90), ("East", "Q2", 150), ("West", "Q2", 110)]
header = ["Region", "Quarter", "Sales"]

wb = Workbook()
data = wb.active
data.title = "Data"
data.append(header)
for row in rows:
    data.append(row)
last = data.max_row
col = {name: get_column_letter(i) for i, name in enumerate(header, start=1)}
region, sales = f"Data!${col['Region']}$2:${col['Region']}${last}", f"Data!${col['Sales']}$2:${col['Sales']}${last}"

summary = wb.create_sheet("Summary")
summary.append(["Region", "Sales"])
regions = sorted({r[0] for r in rows})
for i, name in enumerate(regions, start=2):
    summary.append([name, f"=SUMIFS({sales},{region},A{i})"])
total_row = len(regions) + 2
summary.append(["Total", f"=SUM(B2:B{total_row - 1})"])
summary[f"B{total_row}"].number_format = "#,##0"

chart = BarChart()
chart.title = "Sales by region"
chart.add_data(Reference(summary, min_col=2, min_row=1, max_row=total_row - 1), titles_from_data=True)
chart.set_categories(Reference(summary, min_col=1, min_row=2, max_row=total_row - 1))
summary.add_chart(chart, "D2")
path = r"C:\Users\me\Documents\sales_summary.xlsx"
wb.save(path)
print(path)
```

Then call `office-docs/verify_workbook(file_path=path)`.

## What the check returns

- `status: errors_found` and `ok: false`: `errors` names each broken cell
  (`#REF!`, `#NAME?`, a circular reference, a SUM over text, SUMIFS ranges of
  different sizes, or an error value the recalculation computed). Fix the code
  that builds the workbook, rebuild, and run it again until `ok` is true.
- `warnings` name cells whose result may not be what you meant: a criterion
  that matches no row, a division by an empty cell, arithmetic on text, a SUM
  over empty cells (fine for cells the user fills in). Read each one.
- `status: no_formulas`: every number is a typed value. Rewrite the totals as
  formulas, or say in your answer that they were computed in Python.
- `computed_values` holds the recalculated results when LibreOffice is
  installed, totals and aggregates first. Report numbers from it.
- `next` appears when nothing was recalculated (LibreOffice is not installed).
  There are no `computed_values` then: compute the same figures with pandas in
  `run_python`, report those, and say the workbook's formulas were not
  recalculated.

## Large data

A sheet holds at most 1,048,576 rows, and `verify_workbook` checks workbooks
of up to 500,000 cells. For inputs too large for a sheet, aggregate with
pandas inside `run_python`, write the aggregate and label it as computed (a
note cell such as "Computed in Python from orders.csv"). Read a big .xlsx once
with `load_workbook(path, read_only=True)` and `iter_rows(values_only=True)`.
For heavy jobs raise `run_python`'s `timeout`.
