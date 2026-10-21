---
name: report-from-sources
description: Build a report, deck, brief, memo, workbook or chart from the user's own files without missing what the sources stress. Use when the user asks to turn documents, spreadsheets or exports into a report, slides, a one-pager, a summary, charts or a dashboard. Pins the newest file per dataset, keeps a key-facts ledger per source, puts source and period on every figure, and checks every requested item before handing over.
license: MIT
version: 1.1.0
metadata:
  gaia:
    security_tier: community
    requirements:
      python: ">=3.10"
      dependencies:
        - openpyxl>=3.1
        - python-pptx>=1.0
    tools_required:
      - list_files
      - read_file
      - run_python
    tools:
      - name: inventory_data_files
        description: List a folder's data files with sheets, header rows, units and period coverage, and rank copies of one dataset by vintage.
        parameters:
          folder:
            type: string
            required: true
          recursive:
            type: boolean
            required: false
            default: false
        returns:
          type: object
        atomic: true
      - name: series_from_workbook
        description: Read named series for chosen periods from a spreadsheet, CSV or JSON file, matched by label and cited to their cells.
        parameters:
          file_path:
            type: string
            required: true
          labels:
            type: array
            required: true
          sheet:
            type: string
            required: false
            default: ""
          periods:
            type: string
            required: false
            default: last 12
          orientation:
            type: string
            required: false
            default: auto
          filters:
            type: string
            required: false
            default: ""
        returns:
          type: object
        atomic: true
      - name: chart_into_workbook
        description: Chart named series from a source file into an .xlsx as a native Excel chart with a source footnote, verified on reopen.
        parameters:
          output_path:
            type: string
            required: true
          file_path:
            type: string
            required: true
          labels:
            type: array
            required: true
          sheet:
            type: string
            required: false
            default: ""
          periods:
            type: string
            required: false
            default: last 12
          chart_type:
            type: string
            required: false
            default: line
          title:
            type: string
            required: false
            default: ""
          y_axis_title:
            type: string
            required: false
            default: ""
          number_format:
            type: string
            required: false
            default: ""
          secondary_labels:
            type: array
            required: false
          source_note:
            type: string
            required: false
            default: ""
          filters:
            type: string
            required: false
            default: ""
        returns:
          type: object
        atomic: false
    pairs_with:
      - office-docs
      - data-explore
    provenance:
      source: starter-pack
---

# Report From Sources

A deliverable can read well and still be wrong by omission (it skips the
failure count the source led with, or charts last year's file when this
year's sat beside it) or by addition (a figure no source contains). This is
the order of work that prevents both. Use it with whatever builds the file.

## Hard rules

- **Use the newest data vintage.** Call `inventory_data_files(folder)` first.
  `datasets[].note` names the newest copy of each dataset: use it unless the
  user named a period. Older copies are for comparison only, labelled so.
- **Keep a key-facts ledger per source** before building: headline numbers,
  changes the source states, notable events (a small count of rare events is
  often the most important fact), risks, and what the source stresses.
  Every ledger row goes into the deliverable.
- **Every number is sourced.** Read series with `series_from_workbook` and
  compute other figures with `run_python`. Never retype a number from a page
  or chart a look-alike series. Say which figures you computed. Every chart
  and table names its source, period and units.
- **Nothing from outside the sources** unless it is labelled as such.
- **Check the saved file before handing over.** Your memory of what you
  wrote is not evidence.

## Procedure

1. **Inventory.** Show a table of every file: what it covers, its period or
   as-of date, and its use (primary, comparison, facts). A file under
   `not_read` needs the fallback in `references/inventory.md`.
2. **Ledger.** Read each document in full with `read_file`, following
   `next_offset` until it is null. Record each fact, its value, stated or
   computed, and where it is. When two sources disagree, keep both rows.
3. **Build answer first**: an as-of header line ("As of 30 Sep 2024; data
   through 2024Q3"), a summary with the 3-5 figures that carry it, the
   evidence, then a **Conflicts and gaps** section ("None found" if empty).
   Keep the source's framing ("fell 8% to $4.2M"). For a workbook chart call
   `chart_into_workbook`; for a slide pass `chart_input` from
   `series_from_workbook` unchanged.
4. **Check coverage both ways** on the reopened file: every ledger row is in
   it, and every number in it traces to the ledger or a named computation.
5. **Final requirements check.** Turn the request into a numbered checklist
   (every section, chart, metric, ID, file name, folder and structural ask,
   plus the as-of line, summary first, and Conflicts and gaps). Check each
   line against the saved file, fix the gaps, and report each line as met or
   not met.

Ledger example:

| Fact | Value | Kind | Source |
|---|---|---|---|
| Sites closed this year | 3 (2 in Q3) | stated | board_memo.docx, Operations |
| Q3 sales vs Q2 | +12% | computed: 4.6 / 4.1 - 1 | sales_2024_q3.xlsx, Summary!C7:D7 |

## References (paths relative to this skill's folder)

- `references/inventory.md` - read when files share a dataset, the vintage
  is unclear, or the tool could not parse a file (fallback script).
- `references/ledger.md` - read when building the ledger, for what each row
  records and how to handle conflicting sources.
- `references/building.md` - read before writing the deliverable, and when
  `series_from_workbook` returns `ambiguous` or `not_found`.
- `references/coverage-checks.md` - read at step 4 for the scripts that list
  missing facts and untraced numbers in a saved .docx, .pptx or .xlsx.
- `references/final-check.md` - read at step 5 for a worked checklist and
  scripts that check IDs and workbook inputs.
