---
name: dictation
description: Turn a document into an audio file the user can listen to — read exactly as written, or rewritten into a script that works by ear (tables, charts, footnotes and web addresses become spoken sentences). Use for "dictate this", "read this document to me", "turn this PDF into audio", "make an audiobook / audio version / MP3 of this file", or a document attached with a request to hear it.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: community
    tools_required:
      - dictate_document
      - text_to_speech
      - index_document
      - dump_document
    provenance:
      source: starter-pack
---

# Dictation

One call does the whole job. `dictate_document(file_path)` reads the document,
decides how to read it, records it with the local voice and saves the audio.
Do not `read_file` or index the document first, and do not paste its text into
`text_to_speech`: a long document passed through you gets shortened, and the
tool reads every page itself.

## Just start

A request like "turn this PDF into audio" needs no questions. Call the tool
with the document and nothing else; the defaults are the right ones:

- **The document** — a path the user typed, or the path inside the link of a
  file they attached. PDF, Word, Excel, text and Markdown work.
- **The form** — leave `form` out. The tool reads the document as written
  unless it finds tables, charts, footnotes, web addresses or reference codes,
  and then it rewrites those into sentences a listener can follow. It reports
  which it chose and why.
- **The voice and speed** — leave them out to use the ones the user already
  picked for reading aloud.
- **The format** — MP3 unless they asked for WAV.

Set a parameter only when the user said so: "word for word" or "exactly as
written" is `form="as_written"`; "make it easy to listen to" is
`form="listening"`; "pages 3 to 10" is `pages="3-10"`; "save it in my
Documents folder" is `output_path` (a folder, or a file ending in `.mp3` or
`.wav`).

Ask first in one case only: the user wants a legal, medical or quoted text
where the exact wording matters, and the document has tables or footnotes that
would be rewritten. Then ask whether they want it word for word or easy to
follow, and say what each loses.

## Report what was made

Read the result and tell the user, in two or three lines:

- that the audio is ready and how long it is (`duration_seconds`);
- which form it is and why (`form_reason`), in plain words;
- where their copy is (`saved_to`) when they named a place;
- where the listening script is (`script_path`) when one was written, so they
  can read or correct what was said.

The app shows a player for the audio on its own. `audio_path` is GAIA's own
copy that the player uses: never show it, link it or explain how to open it.
The only paths worth telling the user are `saved_to` and `script_path`. Never
say audio exists unless `status` is `success`.

## When the user wants changes

- **A different voice, speed or format** — call the tool again with that one
  parameter changed.
- **A correction to the script** ("it should say fifteen, not fifty") — read
  the script at `script_path`, fix that passage, and record the corrected text
  with `text_to_speech`, giving it an `output_path` ending in `.mp3`. Do not
  rewrite parts they did not mention.
- **Only part of it** — use `pages` for a PDF. For anything else, ask which
  section and record that section's text with `text_to_speech`.

## Long documents

The tool shows its progress while it writes the script and records, and a long
report takes several minutes. A document longer than about two hours of audio
is refused with the page range that fits in one: dictate exactly that range,
then call again from the next page, and tell the user up front that it will be
several files. Do not guess a smaller range.

## When it fails, say what failed

`dictate_document` returns `{"status": "error", "error": ...}` rather than
raising. Pass the message on in plain words, with what the user can do:

- **The voice model is not downloaded** — reading aloud has not been used on
  this PC yet. Tell them to press Read aloud on any reply once, which
  downloads the voice, and then ask again.
- **Cannot reach Lemonade** — the local model server is not running; the app
  restarts it, so ask them to try again in a moment.
- **No text layer / a scan** — the PDF is pictures of pages. Offer to read it
  with the vision model first: `index_document` it, save its text to a `.txt`
  file with `dump_document`, then `dictate_document` that file.
- **Not a document GAIA can dictate** — say which formats work.

Do not quietly switch to a different form, voice or a shorter text to get past
an error. If a different choice would help, say so and let the user pick.
