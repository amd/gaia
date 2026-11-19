---
name: office-docs
description: Create or edit Excel and Word files with Python and check them - .xlsx workbooks, invoices, trackers, budgets and pivot-style summaries with live formulas and native charts; .docx memos, letters, reports and filled-in templates with real headings and tables. Use when the user asks for a spreadsheet, workbook, Excel file, Word document, memo or .docx/.xlsx file. For slides or a deck, use slide-decks when it is installed, otherwise this skill.
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
      - name: verify_document
        description: Confirm a .docx or .pptx is a real Word or PowerPoint file and count its headings, tables, pictures, slides and charts. Never writes the file.
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

The deliverable is a file the user opens in Excel, Word or PowerPoint, not a
description of one. Build it with code through `run_python`, then check the
saved file with this skill's tools: a file that opens is not yet a file that
is right.

## Hard rules

- **Save with the library** (`Workbook.save`, `Document.save`,
  `Presentation.save`). Never use write_file to create a .docx, .xlsx or
  .pptx, and never save text, CSV, Markdown or hand-written XML under one of
  those names.
- **Excel totals are formulas, not hard-coded values.** Totals inside the
  workbook are formulas over a data sheet (`=SUMIFS(...)`, `=SUM(...)`), so
  it stays live when the user edits it. For inputs too large for a sheet,
  write the pandas aggregate and label it as computed.
- **Verify before handing over.** Workbook: call
  `office-docs/verify_workbook(file_path=...)`, fix every error it names,
  rebuild and call it again until `ok` is true. Report numbers from its
  `computed_values`. When it returns `next` instead (LibreOffice is not
  installed, so nothing was recalculated), compute the same figures with
  pandas in `run_python`, report those, and say the workbook's formulas were
  not recalculated. Word or PowerPoint file: call
  `office-docs/verify_document(file_path=...)`; an error means the file is
  not a real .docx or .pptx, so build it again with the library. Check its
  counts against what you meant to build.
- **Numbers come from the source data**, read by the snippet. Never type
  figures you read off a page.
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
5. Verify the saved file with the tool, fix what it reports, and verify
   again.
6. Report only what the verification showed: the path, what is in the file,
   the key numbers as computed, and anything you could not do.

Example, a results row on the Summary sheet:

```python
summary.append([name, f"=SUMIFS({sales},{region},A{i})"])  # right: a live formula
summary.append([name, 270])                                 # wrong: a hard-coded value
```

For any slide or deck, load the `slide-decks` skill if it is among the
installed skills; otherwise read `references/powerpoint.md`.

## References (paths relative to this skill's folder)

- `references/excel.md` - read before building any workbook: formula
  references, `_xlfn.` functions, number formats, charts, a full example,
  what the check returns and large data.
- `references/word.md` - read before creating or editing a .docx: heading
  and list styles, table header rows, alt text and run-level edits.
- `references/powerpoint.md` - the fallback when `slide-decks` is not
  installed: a 16:9 base, font sizes, a native chart and alt text.
