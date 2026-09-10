---
name: transcribe-meeting
description: Transcribe and summarize a meeting recording — speaker-attributed transcript, corrected mis-hearings, then a brief with decisions and action items. Use whenever the user points at an audio or video file (.mp4, .mkv, .mov, .m4a, .mp3, .wav) or a Teams/Zoom transcript export and asks to transcribe it, summarize it, take notes, write minutes, say what was discussed, who said what, what was decided, or what the action items and follow-ups are.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: community
    tools_required:
      - transcribe_media
      - index_document
      - summarize_document
      - query_documents
      - read_file
      - write_file
    provenance:
      source: starter-pack
---

# Transcribe Meeting

Three things go wrong when a model is handed a meeting recording, and this
procedure exists to stop all three.

A transcript with anonymous speakers is a wall of text nobody can act on. A
transcript an LLM has rewritten end to end is worse: it reads clean and quietly
attributes things nobody said. And a summary written from the opening minutes of
a 46-minute meeting is worse still — it is confident, well-formed, and missing
most of what happened.

## What `transcribe_media` gives you

**It does not return the transcript.** It writes the transcript to a file and
returns the path plus a short preview:

| Field | What it is |
|---|---|
| `transcript_path` | Where the transcript was written. This is the artifact. |
| `preview` | The **first 1200 characters only**. Identification, not content. |
| `character_count`, `segment_count` | How much text is in the file. |
| `audio_duration`, `language`, `model` | What was transcribed, and how. |
| `low_confidence_spans` | Up to **25** spans the recognizer was unsure about. |
| `low_confidence_span_count` | How many there really are — often more than 25. |
| `next_step` | The instruction to follow. It says the same thing this file does. |

The reason is measured, not stylistic. A 46-minute meeting serialises to roughly
135,000 characters against a 60,000-character tool-result budget. Returned
inline, it was silently truncated and about two-thirds of the meeting never
reached the summarizer — which then produced a fluent, complete-looking brief of
a meeting it had mostly not read. Returning a path makes that failure impossible.

## Procedure

1. **Identify the input shape before doing anything expensive.**
   - A media file (`.mp4`, `.mkv`, `.mov`, `.m4a`, `.mp3`, `.wav`) — run the full
     pipeline from step 2.
   - An **already-named transcript** — a Teams or Zoom export arrives with real
     names and timestamps on every line. It needs no ASR and no speaker
     inference: skip to step 4 and summarize it from the file. Steps 2–3 would
     only add cost and risk.
   - Ambiguous text file — read the first page. Lines shaped
     `Name  0:04:12` or `[00:04:12] Name:` mean it is already named. Prose with
     no speaker attribution is a document, not a meeting — hand it to the
     `document-brief` or `summarize` skill instead.

   If the path does not exist, say so and quote the path. Never describe a
   recording from its filename.

2. **Transcribe.** `transcribe_media(file_path)` writes the transcript to
   `~/.gaia/transcripts/<name>.txt` and returns the fields above. Pass
   `output_path` to put it somewhere else, `language` to skip auto-detection.

   The file is written before any later stage runs, so the expensive part
   survives a failure downstream. If the call fails, report its message as
   given — it names the missing component and the command that installs it.

3. **Tell the user where the transcript was saved. Always.** State the full
   `transcript_path` in your reply, every time, even when the user only asked
   for action items and even if a later step fails. Transcription costs minutes
   of compute; a user who does not know the file exists will pay for it twice.

4. **Read the transcript from the file — never from `preview`.**
   `preview` is the first 1200 characters. It is there so you can confirm you
   transcribed the right recording. **Nothing about the meeting may be concluded
   from it** — not the topic, not the participants, not the decisions, and
   certainly not the action items, which cluster at the end of meetings that the
   preview never reaches.

   To actually read the content, use one of these:

   | Goal | Call |
   |---|---|
   | A summary or brief | `index_document(transcript_path)`, then `summarize_document(transcript_path, summary_type=...)` — it folds long input forward in sections and does not lose the back half. |
   | A specific question | `query_documents(query)` after indexing — answers quote the transcript. |
   | A manual pass | `read_file(transcript_path)`, working through it in sections. |

   > **Hard rule.** If you have not read the whole transcript through one of the
   > tools above, you have not summarised the meeting and must not say you have.
   > Say what you actually have — "transcribed and saved; here is the brief"
   > only after a real pass, otherwise "transcribed and saved at `<path>`; want
   > me to summarise it?" A summary built from truncated input does not look
   > truncated. It looks finished, which is exactly why this rule is absolute
   > rather than a preference.

5. **Name the speakers.** The transcript is continuous text, so attribution is
   inferred from content, not from voiceprints. Working through the file, map
   turns from:
   - self-introductions ("Priya here, I'll run the agenda");
   - direct address — when one speaker says "thanks, Dan", the speaker who just
     talked or answers next is likely Dan;
   - role cues — who sets the agenda, who reports status, who signs off.

   Emit a `Speaker key (inferred)` block naming the evidence for each mapping.

   **Never assert a name the transcript cannot support.** Unknown stays unknown:
   `Speaker C = unknown — asks two pricing questions, never named`. A
   confidently wrong name does not stay contained; it propagates into the brief,
   into who owns each action item, and into the indexed transcript that answers
   questions weeks later.

6. **Correct only the low-confidence spans — this constraint is the point.**
   For each entry in `low_confidence_spans`, look at that span plus the
   `context` around it, decide whether it is a mis-hearing, and repair it only
   when the context makes the intended word obvious. Where it does not, keep the
   original and mark it `[unclear: as heard]`.

   Do **not** scan the rest of the transcript for other errors. Do **not**
   smooth grammar, drop filler words, or improve readability. Do **not** rewrite
   a segment because it reads awkwardly.

   Every flagged span carries a measured per-word confidence; everything else
   came back high-confidence and is more likely correct than your guess about
   it. Handing yourself a whole transcript and "fixing the errors" invites
   fabrication across text that was already right, because outside the flagged
   spans there is no signal to correct against — any change there is invention.
   Bounded edits keep every correction anchored to evidence.

   `low_confidence_spans` is capped at 25 entries; `low_confidence_span_count`
   is the real total. When it is larger, correct the 25 you were given and say
   so — *"corrected 6 of the 25 spans reported, of 58 flagged overall."* Do not
   go hunting for the rest in the transcript; they were not measured for you,
   and guessing at them is the fabrication this step exists to prevent.

7. **Write the named transcript, then index it.** `write_file` the named,
   corrected transcript beside the source as `<basename>.transcript.md`, using
   the layout below, then `index_document` on it so follow-ups — "what did Priya
   say about pricing?" — answer from the transcript instead of from a summary of
   it. Report this path too; it is a second artifact, not a replacement for the
   raw one from step 3.

8. **Summarize the named transcript — never the raw one.** Run
   `summarize_document` over the file written in step 7, so the brief is built
   from corrected text with speakers attached. It folds long input forward in
   sections and does not lose the back half. Delegate the section wording to the
   `summarize` skill rather than writing a competing summarizer prompt here —
   two summarization procedures drift apart and the user gets whichever one
   happened to load.

## Stage order is not negotiable

Correction (step 6) and speaker naming (step 5) both produce a **better file**,
and both run *before* summarization (step 8). Three artifacts, in this order:

| # | Stage | Produces | Why it must come first |
|---|---|---|---|
| 1 | Transcribe | `~/.gaia/transcripts/<name>.txt` | The raw record. Never edited in place. |
| 2 | Correct + name | `<basename>.transcript.md` | A mis-heard product name or a wrong owner becomes a wrong action item. Fixing it after the brief is written means fixing it twice — and the brief is what the user acts on. |
| 3 | Summarize | The brief | Built from stage 2, so every fact it states traces to corrected, attributed text. |

Summarizing the raw transcript skips both improvements and produces a brief
whose action items have no owners and whose key facts may contain mis-hearings.
If a stage genuinely cannot run, say which one and what the brief is therefore
missing — do not quietly summarize the raw file and present it as the finished
product.

## Output shape

The transcript file:

```markdown
# Staff meeting — 2026-03-04
Source: C:\recordings\staff-meeting.mp4 · 46m12s · 4 speakers

## Speaker key (inferred)
- **Priya Raman** — introduces herself at 00:12, runs the agenda (host)
- **Dan Okafor** — addressed as "Dan" at 04:31, answers for platform (engineering)
- **unknown (Speaker C)** — asks two pricing questions, never named

## Transcript
[00:12] Priya Raman: Thanks everyone for joining...
[00:31] Dan Okafor: The migration finished Tuesday...
```

The brief, written by `summarize`:

| Section | What it holds |
|---|---|
| Executive Brief | 2–3 sentences: what the meeting was for and what came out of it. |
| Key Facts | Numbers, dates, names, commitments — stated, not inferred. |
| Scope | What was in and explicitly out of scope. |
| Action Items | Who owes what, by when. Owner `unknown` when the transcript doesn't say. |
| Insights | What the discussion revealed that no single statement says. |
| Risks / Blockers | What is stalled, contested, or unresolved. |

## Fork this

Swap the brief table for your own meeting type and the same pipeline carries a
different output: an incident review wants Timeline, Root Cause, Impact, and
Remediation; a customer call wants Asks, Objections, Commitments, and Next Steps.
Steps 1–7 stay exactly as they are — reading from the file, the speaker honesty,
and the bounded-edit rule are what make any of those outputs trustworthy.
