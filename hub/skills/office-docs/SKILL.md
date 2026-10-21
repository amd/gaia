---
name: office-docs
description: Build real Office files with Python and check them before handing them over - Excel workbooks with live formulas and native charts, PowerPoint decks with native editable charts, Word documents with real headings and tables, and in-place .docx edits. Use when the user asks for a spreadsheet, workbook, financial model, summary or pivot-style table, a deck or slides, or a memo or report as a Word file.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: community
    requirements:
      python: ">=3.10"
      dependencies:
        - openpyxl>=3.1
    permissions:
      - program:run:soffice
      - scratch:write
    tools_required:
      - run_python
    pairs_with:
      - slide-decks
    tools:
      - name: verify_workbook
        description: Recalculate a copy of an .xlsx workbook and report every broken formula, with the computed values. Never writes the file.
        parameters:
          file_path:
            type: string
            required: true
        returns:
          type: object
        atomic: true
    provenance:
      source: starter-pack
---

# Office Docs

The deliverable is a file the user opens in Excel, PowerPoint or Word, not a
description of one. Build it with code through `run_python`, then prove it
works by checking the saved file: a file that opens is not yet a file that is
right.

## Hard rules

- **Excel results are formulas, not hard-coded values.** Raw data goes on its
  own sheet; every total, ratio and pivot-style figure is a formula over it
  (`=SUMIFS(...)`, `=SUM(...)`), so the workbook stays live when the user
  edits it. Never compute a result in Python and write the number into the
  cell.
- **Verify before handing over.** For a workbook call
  `office-docs/verify_workbook(file_path=...)`, fix every error it names,
  rebuild and call it again; report numbers from its `computed_values`. For a
  .pptx or .docx, reopen the saved file in a separate `run_python` call with
  the check in its reference file.
- **Compute every number from the source data** inside the snippet. Never
  type figures you read off a page.
- **The real file or nothing.** Never substitute CSV, Markdown or
  hand-written XML and call it the requested file.
- **Native charts**: `openpyxl.chart` in Excel, `shapes.add_chart` in
  PowerPoint. A matplotlib picture is not a chart.

## Procedure

1. Check the libraries once per session:

   ```python
   import importlib.util, sys
   need = {"openpyxl": "openpyxl", "pptx": "python-pptx", "docx": "python-docx"}
   missing = [pkg for mod, pkg in need.items() if importlib.util.find_spec(mod) is None]
   print(sys.executable, "MISSING:", missing or "none")
   ```

   If one is missing, stop: say the file was not created and ask the user to
   run `gaia init --profile chat`.
2. Read the reference for the file type (below) before writing code.
3. Save to an absolute path the user can reach (the folder they named, or
   the one their sources are in) and print it.
4. Keep each `run_python` call small: build a big file in steps, each call
   opening the file the previous one saved.
5. Verify the saved file, fix what it reports, and verify again.
6. Report only what the verification showed: the path, what is in the file,
   the key numbers as computed, and anything you could not do.

Example, a results row on the Summary sheet:

```python
summary.append([name, f"=SUMIFS({sales},{region},A{i})"])  # right: a live formula
summary.append([name, 270])                                 # wrong: a hard-coded value
```

For a presentation of several slides, load the `slide-decks` skill instead.

## References (paths relative to this skill's folder)

- `references/excel.md` - read before building any workbook: formula
  references, `_xlfn.` functions, number formats, charts, a full example and
  large data.
- `references/powerpoint.md` - read before a one-off slide or short deck
  when `slide-decks` is not available: 16:9 base, font sizes, chart styling,
  alt text and the reopen check.
- `references/word.md` - read before creating or editing a .docx: heading
  and list styles, table header rows, alt text, run-level edits and the
  reopen check.
