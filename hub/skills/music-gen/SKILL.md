---
name: music-gen
description: Generate original local music and sound effects (WAV) with Lemonade ACE-Step and ThinkSound for games, interactive stories, videos, and audio assets.
license: MIT
version: 1.0.0
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

Call `list_audio_generation_models()` first to inspect **downloaded** models.
Do not guess a model name or silently download a multi-GB model. Missing models
should be reported so the user can choose whether to install them in Lemonade.

Generating audio can load a multi-GB generator and evict the chat model from
GPU memory. **Only generate when the user explicitly requests audio**, and
expect the chat model to reload afterwards. Do not pre-generate assets.

For **music**, call `generate_music` with an ACE-Step model. Describe style,
character, instrumentation, mood and tempo in `prompt`. Leave `lyrics` blank for
instrumental background music; for vocals, use `[verse]`, `[chorus]` etc. and
set `vocal_language` if known. Keep `steps` unset unless explicitly requested.

For **sound effects**, use `generate_sound_effect` with ThinkSound (or a supported
OpenMOSS SFX model). Describe the physical sound and acoustic environment. `cfg`
is a ThinkSound-specific knob and should be left unset otherwise.

Both tools write WAV files and return an absolute `audio_path`. Check
`status == "success"` and that path before reporting a completed asset.
If using Ren'Py, copy the generated files into `game/audio/` through an
approved file operation, and reference the relative paths from Ren'Py scripts.
Do not fabricate success, silently substitute a model, or retry indefinitely.
