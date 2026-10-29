# Settling the brief and pulling key facts

## When to ask (usually never)

Saved preferences (theme, brand colours, density, notes, template) apply
automatically. What this request says wins: pass it as `prefs`, `theme` or
`template_path`.

Ask **one** short question **only** for a material ambiguity: two
instructions that conflict, or a deck for an outside audience with no brand
(template or colours) known. Use `request_user_input` with 3-4 choices, a
default and `timeout_seconds` of 60 or less. If nobody answers, go on with
the default. Never ask about anything a default covers.

> *"Make a 6-slide pitch for the Lakeside Clinics partnership"* (no template,
> outside audience) → `request_user_input(message="This deck goes outside the
> team. Use a brand template, or a built-in theme?", choices=["Light corporate
> theme", "Minimal theme", "Dark theme", "I'll give a template path"],
> default_if_no_response="Light corporate theme", timeout_seconds=45)`.
>
> *"Turn these notes into slides for our team meeting"* → no question.

## Key-facts ledger

When the deck is built from documents or data, do this before writing
slides. Find the files with `list_files`, read each document in full with
`read_file` (follow `next_offset`), and compute numbers from data with
`run_python`. Never retype a number from a page. For each source:

| Fact | Value | Source |
|---|---|---|
| Headline numbers (totals, rates, counts) | Revenue $12.4M | finance_q3.xlsx |
| Change vs the prior period, as the source states it | +12% vs Q2 | finance_q3.xlsx |
| Notable events and their counts (closures, incidents, launches) | 3 site closures | board_memo.docx |
| Risks, caveats, missed targets | single supplier for part B | board_memo.docx |

Every ledger row goes on a slide, in the source's own framing ("fell 8% to
$4.2M"), and into `required_items`. When several files cover the same thing,
use the newest one for the requested period. `report-from-sources` has the
longer version of this step.
