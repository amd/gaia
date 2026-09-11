# Testing meeting transcription on a fresh machine

Branch: `kalin/transcription-pipeline` ([PR #3597](https://github.com/amd/gaia/pull/3597))

You point the agent at a recording and ask for a summary. It transcribes the
audio, works out who spoke from the audio itself, saves a speaker-labelled
transcript, indexes it, and gives you a brief with action items — then answers
follow-up questions about the meeting from the transcript.

Everything runs locally. No cloud, no API key, no HuggingFace account.

**Budget about 45 minutes**, most of it unattended downloads.

---

## 1. Prerequisites

| | Version | Check |
|---|---|---|
| Python | 3.10+ | `python --version` |
| Go | 1.26+ | `go version` |
| git | any | `git --version` |
| Windows Terminal | recommended | see the colour note in §5 |

Go is only needed to build the terminal UI. On Windows:

```bash
winget install GoLang.Go
winget install Python.Python.3.12
```

You need an AMD machine with a GPU or NPU for reasonable speed. It runs on CPU
but transcription will be slow.

---

## 2. Clone and install

```bash
git clone https://github.com/amd/gaia.git
cd gaia
git checkout kalin/transcription-pipeline
git pull                      # the branch moves; start from the tip

python -m pip install uv
uv venv
# Windows:        .venv\Scripts\activate
# macOS / Linux:  source .venv/bin/activate
uv pip install -e ".[dev]"
```

> **Already cloned?** `git pull` first. This branch is still being worked on,
> and a stale checkout is the second most common reason something here does not
> behave as described (the first is §5).

## 3. Install Lemonade and pull the models

```bash
gaia init --profile gaia
```

This installs Lemonade Server and downloads the chat model
(`Gemma-4-E4B-it-GGUF`, ~4 GB). It takes a while on a first run.

Confirm it worked:

```bash
gaia init --profile gaia --check
```

## 4. Build the terminal UI

**Windows** — the `.exe` matters. Go does not add it for you when you pass
`-o`, and a file without it will not launch from a `.bat` or a shortcut:

```bash
cd tui
go build -o bin/gaia-tui.exe ./cmd/gaia
cd ..
```

**macOS / Linux:**

```bash
cd tui
go build -o bin/gaia-tui ./cmd/gaia
cd ..
```

---

## 5. Run it

> **This is the step people get wrong.** The terminal UI does not run the agent
> itself — it spawns a separate `gaia-agent` program and talks to it over a
> pipe. So the agent you get is whichever `gaia-agent` it finds first, and
> `uv pip install -e ".[dev]"` in step 2 is what points that at **your clone**.
> If the agent says it cannot transcribe, you are talking to a different build;
> re-run step 2 from inside this checkout.

The repo ships launch scripts that set the environment for you.

**Windows** — from the repo root:

```bat
scripts\dev\run-tui.bat
```

**macOS / Linux:**

```bash
./scripts/dev/run-tui.sh
```

Launch it from **Windows Terminal**, not a bare `cmd.exe` window. A console
started any other way reports no colour support and the UI renders flat grey —
it looks broken but is not.

If you would rather run the binary directly:

```bash
./tui/bin/gaia-tui
```

On a machine with **several** GAIA clones, `gaia-agent` resolves to whichever
one was `pip install -e`'d last — which may not be this one. Check with
`where gaia-agent` (Windows) or `which gaia-agent`, and re-run step 2 here if
it points elsewhere.

The first launch runs a readiness check, then drops you into chat.

---

## 6. Transcribe something

Type this, with a real path to an audio or video file:

```
Summarize this meeting: C:\path\to\recording.mp4
```

**Use a 3–10 minute recording for your first run.** Anything shorter does not
give speaker separation enough to work with; a 46-minute meeting takes about
11–12 minutes end to end.

`.mp4`, `.mkv`, `.mov`, `.m4a`, `.mp3` and `.wav` all work.

### What you should see

1. `Decoding <file> — 40% of 5m 00s`
2. `Transcribing 5m 00s of audio with Whisper-Large-v3-Turbo — around 33s to go`
3. `Identifying speakers in 5m of audio...`
4. A summary with key points and action items, and the paths to two files.

**The first run also downloads** the speaker-identification engine
(`sherpa-onnx`, ~40 MB) and two models (~33 MB). One time only; later runs skip
it. If your machine has no `ffmpeg`, Windows and macOS install it
automatically — on Linux it stops and prints the exact `apt`/`dnf`/`pacman`
command to run.

### Then ask follow-up questions

This is the part worth trying, and people do not discover it on their own:

```
What did they say about pricing?
Who owns the follow-up on the security review?
Was a deadline mentioned?
```

Answers come from the indexed transcript and quote it directly, with the source
file cited.

### Your files

Both land in `~/.gaia/transcripts/` (`C:\Users\<you>\.gaia\transcripts\`):

| File | What it is |
|---|---|
| `<name>.txt` | Raw transcript, exactly as heard |
| a `.md` file | Speaker-labelled, the one that gets summarized |
| `<name>.txt.timing.json` | Segment timings and voice spans |

The agent picks the name of the speaker-labelled file, so it varies between
runs — it has come out as both `<name>.transcript.md` and
`refined_meeting_summary.md`. **It always tells you both paths in its reply**;
read them from there rather than guessing.

---

## 7. What is expected to be imperfect

**Speaker names.** Voice *separation* comes from the audio and is reliable. The
*names* are inferred from what was said — a self-introduction, or someone
addressed by name. With no such evidence a voice stays `Speaker 1`, `Speaker 2`.
That is deliberate: a confidently wrong name would propagate into the brief and
into who owns each action item.

**Short interjections get absorbed.** On a 90-second clip where a second person
says three words, it reports one speaker.

**Speaker count can be off by one or two** on longer recordings. On a
46-minute, 4-person meeting it found exactly 4; treat that as the good case
rather than the guaranteed one.

**It is not instant.** Measured on a 46-minute recording, roughly 4x realtime:

| Stage | Time |
|---|---:|
| Decode to WAV | 4 s |
| Transcribe (Whisper-Large-v3-Turbo) | 6.0 min |
| Identify speakers | 4.1 min |
| Name speakers + summarize | 1-2 min |
| **End to end** | **~11-12 min** |

Transcription and speaker identification are 99% of it; decoding is free.
A 5-minute clip is proportionally quicker — about 90 seconds.

---

## 8. If something goes wrong

| Symptom | Cause |
|---|---|
| "I can't transcribe files" | The TUI found a different `gaia-agent`. Re-run step 2 in this checkout — see §5 |
| Speakers all come back as one | Speaker identification could not start; the reply says why |
| Nothing happens for 60–90 s on the first question | Model loading. Only the first turn pays this |
| "ffmpeg is required..." | Run the command it prints, then retry |
| "Lemonade Server is not reachable" | `lemonade-server serve`, or re-run `gaia init --profile gaia` |
| Everything grey, no colour | Launched outside Windows Terminal — see §5 |
| One speaker on a short clip | Expected. Try a longer recording |

Logs: `~/.gaia/logs/`. For a bug report, `gaia diagnostics` bundles logs and
system info.

---

## 9. Useful feedback

- Were the **action items** right, and did they have the correct owners?
- Did **follow-up answers** match what was actually said?
- Was the **speaker count** close, and were any real names picked up?
- Where did it feel slow, or look stuck with no explanation?
- Anything in the transcript that was **wrong but stated confidently** — that
  matters more than anything else here.
