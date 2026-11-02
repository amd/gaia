---
name: analyze-recording
description: Break down a long video or audio recording that is not a meeting — a class, lecture, tutorial, webinar, podcast, interview — from a file or a link such as YouTube, into a timestamped transcript, a complete structured list of what it contains, and answers to questions about it. Use whenever the user points at a recording and asks to list every topic, step, tip, recipe or question in it, wants timestamps or chapters, wants subtitles, or wants to ask what was said and when. For meeting minutes and action items use transcribe-meeting.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: community
    tools_required:
      - transcribe_media
      - refine_transcript
      - analyze_transcript
      - export_transcript
      - query_documents
      - read_file
    provenance:
      source: starter-pack
---

# Analyze Recording

A recording of any length becomes a timestamped transcript and a complete,
structured report. Each tool writes a file the next one reads. Nothing is passed
inline, because an hour of speech is over 50,000 characters and a list built from
a truncated part looks finished rather than truncated.

| # | Call | Produces |
|---|---|---|
| 1 | `transcribe_media(file_path, speakers=...)` | Raw transcript and its timings. Returns `transcript_path`. |
| 2 | `refine_transcript(transcript_path)` | A timestamped, speaker-labelled transcript, **already indexed**. Returns `refined_path`. |
| 3 | `analyze_transcript(refined_path, request, fields)` | A report with an overview and every matching item. Returns `analysis_path`. |

## Procedure

1. **Confirm the file exists, and set expectations.** If the path is not there,
   say so and quote it. Never describe a recording from its filename. Expect
   about an eighth of its length to transcribe and label on a fast PC, around
   8 minutes for an hour-long video, and longer on a slower one. The analysis
   then takes a few seconds per section on a cloud model and longer on a local
   one. Say this before you start. The Agent UI shows each stage's progress, so
   don't report percentages yourself.

2. **Transcribe.** `transcribe_media(file_path)`, where `file_path` may also be a
   link to a video such as a YouTube URL. If the user says one person
   presents, pass `speakers=1`, which skips voice separation and saves minutes.
   If they name a number of speakers, pass it. A long recording is transcribed
   in chunks; if a call fails partway, calling it again continues from the
   finished chunks.

3. **Refine.** `refine_transcript(transcript_path)`. It writes the transcript
   with a timestamp on every paragraph and indexes it, which is what makes
   questions answerable with times.

4. **Analyze the whole recording.** Call `analyze_transcript` once, with:
   - `request`: what to find, in the user's own words.
   - `fields`: what to report for each item, taken from what the user listed.

   Write both in the language spoken in the recording, translating if the user
   wrote in another one, then answer the user in their own language. A request in
   a different language from the recording can come back empty.

   A user who says *"list every recipe in this cooking class with the
   ingredients, the method and how long it takes"* gets:

   ```
   analyze_transcript(
     refined_path,
     request="every recipe taught in the class",
     fields=["ingredients", "method", "cooking time"],
   )
   ```

   The title and the start and end times are always included, so do not list
   them as fields. If the user wants chapters or a topic breakdown, the request
   is the chapters: `request="each distinct topic, in order"`.

5. **Report.** Give the `overview`, the `item_count` and every path: the raw
   transcript, the refined transcript and the report. A user who does not know
   the files exist pays for the compute twice. To show items in the chat, use
   `read_file` on the report. Never write items out from memory.

6. **Invite the follow-up.** Tell the user they can now ask about any part of the
   recording.

## Answering questions afterwards

The refined transcript is indexed, so use `query_documents` for a question about
a detail. Each passage starts with its `[MM:SS]` time: quote that time in your
answer so the user can jump to the moment. Do not transcribe again to answer a
question.

## Subtitles

`export_transcript(transcript_path, format)` writes `srt` or `vtt` subtitles, or
`txt` for timestamped text. Pass the refined file so speakers are labelled when
there are several.

## Never build the result from a fragment

- **Not from `preview`.** It is the first 1,200 characters of the recording.
- **Not from `query_documents`.** It returns the top few chunks, so a list built
  from it silently omits most of the recording.
- **Not from `summarize_document`.** A prose summary drops items and times. It is
  the right tool only when the user asked for a summary and nothing else. Do not
  call both: the report already opens with an overview.

## Trust the report's own markers

- `speech_warning` on a transcript means the recording has almost no speech, and
  the few words there may not have been said. Tell the user and stop.
- A time marked `~` could not be matched to a transcript line. It shows the
  section the item came from, not its exact moment. Say so if the user asks.
- `not stated` means the recording did not say. Never fill it in from general
  knowledge, even when the answer seems obvious.
- If `analyze_transcript` fails, report its error and call it again with the same
  arguments once the cause is fixed. Finished sections are kept. Never present a
  partial pass as the finished report.

## Without the agent

`scripts/analyze_recording.py` runs the same pipeline from one command, for batch
and scheduled jobs. It writes everything next to the recording and reuses
finished work when run again. Use the tools above during a conversation: they
index the transcript, and the script does not.

## Report shape

The report opens with what it covers, then an overview, then the numbered items
with their fields. This is the section to change when a reader wants a different
layout. Everything above it is about producing a complete and trustworthy result.

## Fork this

Keep the three calls. Change the `request` and `fields` to suit a recurring kind
of recording: a cooking channel wants ingredients and steps, a lecture series
wants concepts and worked examples, a sales webinar wants claims and prices.
