---
name: summarize
description: Summarize a document, meeting transcript, or email — pick a style that matches what the content actually is, and fold long input forward in sections instead of losing the back half. Use when the user pastes or points at a transcript, email, PDF, or long document and asks for a summary, action items, or key decisions.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: community
    tools_required:
      - index_document
      - summarize_document
      - list_indexed_documents
      - dump_document
      - read_file
    provenance:
      source: starter-pack
---

# Summarize

A summary that drops the back half of a long document is worse than no
summary — it looks complete and isn't. Everything here exists to stop that:
pick the right style for what the content *is*, and never lose earlier
material when the input is too long to read in one pass.

## Procedure

1. **Identify what you're summarizing before you pick a style.** Read the
   first portion and classify it:
   - **Transcript** — speaker labels, timestamps, back-and-forth dialogue.
   - **Email** — `From:`/`To:`/`Subject:` headers, a greeting/sign-off, one
     author's voice.
   - **Document / PDF / business content** — reports, decks, specs; prose
     without dialogue structure.
   When it's genuinely ambiguous, say which you picked and why in one clause
   rather than silently guessing — the style below changes with it.
2. **Get the full text.** For an indexed document, `index_document` it first
   if it isn't already (`list_indexed_documents` to check), then either call
   `summarize_document(file_path, summary_type)` or `dump_document` first if
   you need the raw extracted text. For pasted text or a plain text file,
   `read_file` is enough.

   `summary_type` accepts **only** `brief`, `detailed`, or `bullets` — anything
   else comes back as an error, not a summary. The named styles below are how
   *you* shape the output: pick the closest of those three, then write to the
   style the user actually asked for. For "what were the action items", that
   means `detailed` (so nothing is dropped) and then writing the list yourself
   from the text.
3. **Decide if it fits in one pass.** If the content is short enough for you
   to read and reason over directly, write the summary yourself using the
   style instructions below — don't reach for the section-by-section tool
   path for a two-page memo. For anything long, use `summarize_document`
   (which already sections and folds internally) or the manual fold-forward
   procedure below when the tool isn't available for the content you have.
4. **Write to the style, not a generic summary.** Match the length and
   structure rules exactly — a "brief" that runs to eight sentences failed the
   request even if every sentence is accurate.

## Styles

**Transcripts and emails** — conversational, outcome-focused:

Only `brief`, `detailed` and `bullets` are real `summary_type` values; the
rest name a shape you write yourself.

| Style | What to produce |
|---|---|
| brief / executive | 2–3 sentences on the key outcomes and decisions. |
| detailed | Full paragraph coverage of every major topic and outcome. |
| participants | Simple list of who was involved, with role/title if known. |
| action_items | Simple list of what was assigned, and to whom if stated. |
| key_decisions | Simple list of concrete decisions and outcomes — not discussion. |
| topics_discussed | Simple list of the subjects covered. |

**Documents, PDFs, and business content** — numbers before narrative:

| Style | What to produce |
|---|---|
| `brief` | At most 3 sentences, no bullets, only the 2–3 essential takeaways. No filler ("Overall", "In summary"). |
| `detailed` | 250+ words. Extract every number, percentage, dollar amount, date, and metric. Name competitors, partners, customers, technologies. Full paragraphs, no bullets. |
| `bullets` | At most 3 bullets total, each under 20 words, one idea per bullet. |
| executive | At most 5 sentences. Priority order: (1) quantitative metrics, (2) financial data, (3) strategic outcomes, (4) competitive differentiators. Always keep the specific numbers. Board-ready tone, no bullets. |

The rule underneath the table: for business documents, a metric always
outranks the marketing language around it. "Revenue grew 40% YoY to $12M" is
the sentence that survives a `brief`; "the company is scaling rapidly" is not.

## Folding a long document forward (map-reduce)

`summarize_document` already does this internally when it's available — call
it and you're done. Reach for the manual version only when you're working
from pasted text or a file too large to read in one pass and the tool isn't
an option:

1. Split the text into ordered chunks, breaking on paragraph boundaries where
   possible rather than mid-sentence.
2. Summarize the first chunk normally, in the target style.
3. For every chunk after that, summarize it **against the running summary**:
   given the summary so far and the new chunk, output only the facts in the
   new chunk that are not already captured — no restating, no rephrasing what
   came before, no transition filler like "Additionally". This is the whole
   point of folding forward instead of summarizing each chunk in isolation:
   isolated per-chunk summaries repeat themselves and drift from the style
   once concatenated; folding keeps one coherent summary that only grows by
   what's actually new.
4. Append each chunk's new-facts output to the running summary in order. The
   final running summary is the answer — do not re-summarize it again at the
   end, that just re-introduces the drift step 3 was avoiding.

## Fork this

Swap the document table's priority order for a different domain (legal:
obligations and deadlines before financial terms; incident review: root
cause and impact before remediation steps) and the same fold-forward
procedure carries a completely different kind of long input.
