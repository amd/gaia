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
      - refine_transcript
      - index_document
      - summarize_document
      - query_documents
    provenance:
      source: starter-pack
---

# Transcribe Meeting

A recording becomes a usable brief in three tool calls. Each one produces a file
the next one reads — nothing is passed inline, because a 46-minute meeting is
~135,000 characters against a 60,000-character tool-result budget, and a summary
built from the truncated half looks finished rather than truncated.

| # | Call | Produces |
|---|---|---|
| 1 | `transcribe_media(file_path)` | Raw transcript at `~/.gaia/transcripts/<name>.txt`. Returns `transcript_path`, never the text. |
| 2 | `refine_transcript(transcript_path)` | `<name>.transcript.md` — mis-hearings repaired, split into speaker turns. Returns `refined_path`, `speakers`, `sections`. |
| 3 | `index_document(refined_path)`, then `summarize_document(refined_path, summary_type='detailed')` | The brief. |

## Procedure

1. **Confirm the file exists, and set expectations.** If the path is not there,
   say so and quote it — never describe a recording from its filename.
   Transcription runs at roughly 9x realtime, so a 46-minute recording is about
   5 minutes, and refinement afterwards takes substantially longer again. Say
   that before you start, not after the user has waited.

2. **Transcribe.** `transcribe_media(file_path)`. Pass `output_path` to write
   elsewhere, `language` to skip auto-detection. The transcript is written
   before anything downstream runs, so it survives a later failure; if the call
   errors, report its message as given — it names the missing component and the
   command that installs it.

   `preview` is the first 1200 characters, for identifying the recording only.
   `low_confidence_spans` and `low_confidence_span_count` are informational —
   step 3 does the correcting. Do not repair them yourself.

3. **Refine — one call, and it is not your work to redo.**
   `refine_transcript(transcript_path)` walks the transcript section by section,
   fixes mis-hearings, labels the speaker turns and writes the result. You do
   not hand-correct wording, do not work out the speakers yourself, and do not
   write a transcript file. Summarizing the raw transcript instead produces a
   brief with no owners on the action items and mis-heard names in the facts.

4. **Report both paths, every time.** State the raw `transcript_path` *and* the
   `refined_path` in your reply — even when the user only asked for action
   items, even if a later step fails. Transcription costs minutes of compute; a
   user who does not know the files exist pays for it twice.

5. **Index, then summarize.** `index_document(refined_path)` is required before
   `summarize_document` — and it is also what lets the user ask questions about
   this meeting afterwards, so never skip it. Then
   `summarize_document(refined_path, summary_type='detailed')`, which folds the
   whole transcript forward in sections so the brief covers the entire meeting.

6. **Invite the follow-up.** Close by telling the user they can ask questions
   about the meeting and you will answer from the indexed transcript. They will
   not discover this on their own.

## Never build the brief from a fragment

- **Not from `preview`.** 1200 characters tells you which recording this is and
  nothing else — not the topic, not the participants, not the decisions, and
  certainly not the action items, which cluster at the end.
- **Not from `query_documents`.** It returns only the top few matching chunks,
  so a summary built from it silently omits most of the meeting.
  `query_documents` *is* the right tool for a later follow-up question about a
  specific detail.

If a stage did not run, say which one and what the brief is therefore missing.
Do not present a partial pass as the finished product.

## Speaker names are inferred, not identified

Nothing in this pipeline does voice identification. `refine_transcript` works
out who is speaking from the conversation itself — self-introductions, direct
address, role cues — so present the names as best-effort and say so. Speakers it
could not name stay `Speaker A`, `Speaker B`; never upgrade one to a real name
yourself. A confidently wrong name propagates into the brief, into who owns each
action item, and into the indexed transcript that answers questions weeks later.

## Output shape

`refine_transcript` writes:

```markdown
# Transcript — staff-meeting

Source: C:\recordings\staff-meeting.mp4

## Speakers

- Priya Raman
- Dan Okafor
- Speaker C

## Transcript

Priya Raman: Thanks everyone for joining...
Dan Okafor: The migration finished Tuesday...
```

Shape your brief from `summarize_document`'s output as: what the meeting was for
and what came out of it (2–3 sentences); key facts stated rather than inferred;
action items with an owner each, `unknown` when the transcript does not say; and
anything unresolved or blocked.

## Fork this

The three calls stay as they are — the file-not-inline pipeline, the speaker
honesty, and refinement before summarization are what make any output
trustworthy. Change only the brief's sections: an incident review wants
Timeline, Root Cause, Impact and Remediation; a customer call wants Asks,
Objections, Commitments and Next Steps.
