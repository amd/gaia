# Key-facts ledger per source

Read each source in full with `read_file(file_path)`, following `next_offset`
until it is null. A skim or a search result misses exactly the line that
matters. For each source, write down:

- **Headline numbers**: totals, rates, counts, balances.
- **Changes the source states** against a prior period: from, to, and by how
  much ("revenue $4.6M, down 8% from $5.0M").
- **Notable events**: counts of failures, closures, incidents, outages, recalls,
  defaults, departures. A small count of rare events is often the most
  important fact on the page.
- **Risks and caveats**: warnings, exceptions, restatements, missed targets.
- **What the source itself emphasises**: its summary or highlights, the first
  paragraph, bold text, "notably", "for the first time", "record".

Each row gets the fact, the value, whether the source **states** it or you
**computed** it, and where it is (file, plus page, sheet, section or cell):

| Fact | Value | Kind | Source |
|---|---|---|---|
| Sites closed this year | 3 (2 in Q3) | stated | board_memo.docx, Operations section |
| Q3 sales | 4.6 ($M) | stated | sales_2024_q3.xlsx, Summary!D7 (from `series_from_workbook`) |
| Q3 sales vs Q2 | +12% | computed: 4.6 / 4.1 - 1 | sales_2024_q3.xlsx, Summary!C7:D7 |
| Supplier risk flagged | single source for part B | stated | board_memo.docx, Risks |

A computed row names its formula and the stated rows it uses. When two sources
give different values for the same fact, keep both rows; they go in the
"Conflicts and gaps" section of the deliverable, with which one you used and why.

Keep the ledger in your working reply. Do not save it as an extra file in the
user's folder unless they ask for one.

## Fit it to your field

Pin the ledger to your field's standing questions, for example churn, pipeline and
headcount for a monthly business review, and the ledger becomes a checklist no
report can skip.
