---
name: music-gen
description: Compose original music and sound effects as WAV files with local Lemonade models (ACE-Step for music, ThinkSound for SFX), then iterate on them. Use when the user asks for a song, a soundtrack, background or menu music, a jingle, a loop, a sound effect, a foley sound, or game/video audio assets — and when they want the last track changed rather than replaced.
license: MIT
version: 1.1.0
metadata:
  gaia:
    security_tier: community
    tools_required:
      - list_audio_generation_models
      - generate_music
      - generate_sound_effect
    provenance:
      source: starter-pack
---

# Music and sound effects

Generation runs locally and is slow: a short sound effect takes seconds to a
minute, a two-minute song can take several minutes. It also costs the
conversation. Loading a multi-GB audio model evicts the chat model, so the
reply after a track pauses while the chat model comes back. Make audio when the
user asked for audio, never to decorate an answer.

## Check what is downloaded first

Call `list_audio_generation_models()` before promising anything. It returns
only models already downloaded. If the one you need is missing, say so and let
the user decide whether to download it (the tool's error names the command).
Never pick a different model in its place without asking.

## Music or a sound effect?

- **Music** (`generate_music`, ACE-Step): anything with rhythm, harmony or a
  melody. Songs, soundtracks, menu loops, jingles, stingers longer than a few
  seconds.
- **Sound effect** (`generate_sound_effect`, ThinkSound or OpenMOSS): one
  physical event or ambience. A door, footsteps, rain, an explosion, a UI
  click, room tone.

A "magic spell sound" is a sound effect. A "spooky ambient track for the cave
level" is music. If a request mixes both ("battle music with sword clashes"),
make them separately and say so.

## Write the prompt for them

A bare "make some music" has no shape. Expand it yourself, then show the user
the prompt you used so they can correct it.

For **music**, name, roughly in this order: **genre**, **tempo** (a BPM or
slow/medium/fast), **instrumentation**, **mood**, and **structure** if it
matters. So "chill music" becomes "lo-fi hip hop, 75 BPM, mellow Rhodes piano,
soft vinyl crackle, brushed drums, relaxed late-night mood". Leave `lyrics`
empty for instrumental music. For vocals, write lyrics with `[verse]` and
`[chorus]` tags and set `vocal_language` (e.g. `en`, `de`).

For a **sound effect**, name the **source**, the **material** and the
**environment**. So "door sound" becomes "heavy oak door slams shut, iron
latch clicks, small stone room with a short echo". One event per prompt.

Leave `steps`, `cfg` and `seed` unset unless the user asks. `cfg` only applies
to ThinkSound; never send it with another model.

## Pick a duration on purpose

Always pass `duration` in seconds; an unset length is whatever the backend
defaults to.

- Sound effects: 1-5 s for one-shots (clicks, hits, doors), 10-30 s for
  ambience loops.
- Music: 10-30 s for stingers and loops, 60-120 s for a menu or level theme.
  Go longer only when the user asks.

**Ask before a long or expensive run.** Before any music over 60 s, or more
than three generations in one turn, say what you are about to make and roughly
how long it will take, and wait for a yes. A minute of music can take minutes
to generate.

## Save where the user will use it

By default the WAV lands in GAIA's audio cache. When the user is working in a
project (a game, a video edit, a podcast), pass `save_to` with that folder as
an absolute path, e.g. the project's `audio/` or `assets/sfx/` directory. Use a
descriptive file name ending in `.wav` (`save_to=".../assets/sfx/door_slam.wav"`)
when they will reference it from code. The tool refuses to overwrite an
existing file; pick a new name rather than deleting the old one.

## Iterate instead of starting over

When the user says "faster", "more drums", "less reverb" or "shorter", take
the prompt you used last time, change only what they asked about, and keep
the rest, including the `seed` if you set one. Rewriting the prompt from
scratch throws away the parts they liked. Save each version under a new name
(`theme_v2.wav`) so they can compare.

## Report only what the tool returned

Both tools return `{"status": "success", "audio_path": ...}` or
`{"status": "error", "error": ...}`. Only say a track was made when `status`
is `success` and `audio_path` is present. Then give the path, the length and
the prompt:

> Saved `assets/music/cave_theme.wav` (45 s, ACE-Step).
> Prompt: "dark ambient, 60 BPM, low drones, distant water drips, uneasy".
> Want it slower, or with a melody on top?

The chat shows a player for the file, so do not describe how it sounds; you
have not heard it.

On an error, pass the tool's message on. Do not retry with another model, a
shorter duration or fewer steps without asking. Common failures:

- **Not a downloaded model**: the model needs downloading; ask first.
- **Cannot reach Lemonade / timed out**: inference is not running or the model
  is still loading the first time. Say so; do not loop.
- **Invalid WAV data**: the backend failed mid-generation. Offer one retry.
- **save_to refused**: the folder is outside what the agent may write, or the
  file exists. Ask for another folder or name.

For a Ren'Py or other game project, save straight into its audio folder
(`game/audio/` for Ren'Py) and reference the relative path from its scripts.

## Fork this

Pin a house style (your game's genre, tempo range and palette of instruments)
into the prompt section, and every track comes out sounding like the same
soundtrack.
