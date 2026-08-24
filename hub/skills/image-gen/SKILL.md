---
name: image-gen
description: Turn a description into an image file with local Stable Diffusion, then iterate on it. Use when the user says draw, sketch, paint, render, "make a picture of", "generate an image", or asks for concept art, a thumbnail, a logo idea, a wallpaper, or a mockup — and when they want the last image changed rather than replaced.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: community
    tools_required:
      - generate_image
      - list_sd_models
      - get_generation_history
    provenance:
      source: starter-pack
---

# Image Generation

Generation runs locally and is slow — tens of seconds to minutes per image, and
the first call for a model downloads gigabytes. That changes the job: you get
few attempts, so spend the thinking *before* the call rather than firing off
four variations and picking one.

It also costs the conversation. Drawing loads the image model in place of the
chat model, so the reply after an image pauses while the chat model comes back.
Generate when the user actually asked for a picture — not to illustrate an
answer they did not ask to have illustrated.

## Check what is loaded before you promise anything

Call `list_sd_models()` first. It tells you which models exist and what each
costs, and the reported `default_model` is the one you get if you pass no
`model`. Do not assume a specific model is resident — naming one the machine
has not pulled turns a 20-second request into a multi-gigabyte download the
user did not agree to.

Tell the user the estimate before a slow model, not after: SDXL-Base-1.0 at
1024x1024 is on the order of minutes, the Turbo models are seconds.

## Build the prompt for them

A user asking for "a cat" has a picture in their head that "a cat" will not
produce. Expand it yourself rather than interrogating them — one round of
questions is fine, three is a worse experience than a decent first image.

A usable prompt names, roughly in this order: **subject**, **what it is doing
or how it is arranged**, **setting**, **style**, **lighting or mood**. So
"a red bicycle" becomes "a red bicycle leaning against a brick wall, morning
sunlight, shallow depth of field, photographic".

Then say the expanded prompt back to the user with the result. They cannot
correct a prompt they never saw, and "make it warmer" is only meaningful if
they know what you asked for.

## The default is a few-step model — do not over-tune it

SDXL-Turbo is the default and it is distilled to converge in about **4 steps**
with **CFG around 1.0**. The knobs that matter on a normal model do nothing
useful here:

- Raising `steps` to 30 costs seven times the wall clock and does not improve
  the image.
- Raising `cfg_scale` degrades it — Turbo models are trained for guidance-free
  sampling.
- Long negative-prompt boilerplate ("blurry, low quality, watermark, extra
  fingers…") is wasted. Spend those words describing what you *do* want.

Leave `steps`, `cfg_scale`, and `size` unset unless you have a reason; the tool
fills in the right values per model. Reach for `SDXL-Base-1.0` only when the
user explicitly wants photorealism and has accepted the wait.

## Iterate instead of starting over

`get_generation_history()` returns this session's generations with the exact
prompt, model, size and seed of each. When the user says "same but at sunset"
or "make it wider", read the previous entry, change the one thing they asked
about, and keep everything else — including the `seed`. Reusing the seed is
what makes the second image recognisably the same picture rather than an
unrelated one that happens to match the words.

Rewriting the prompt from scratch throws away everything that was already
working, and the user has to re-explain the parts they liked.

## When it fails, say what failed

`generate_image` returns `{"status": "error", "error": ...}` rather than
raising. Read it and pass the actual message to the user.

**Do not quietly retry with a different model, a smaller size, or fewer
steps.** A user who asked for a photorealistic 1024px render and silently
received a 512px Turbo sketch has been given the wrong thing and told nothing.
If a fallback would genuinely help, propose it and let them choose.

The common failures and what to say:

- **Cannot reach Lemonade Server** — inference is not running. Tell them to
  start it; nothing here works until it is up.
- **Timed out** — usually the first use of a model, downloading several GB.
  The server is fine. Tell them to pre-fetch it (`lemonade-server pull
  <model>`) and retry, rather than restarting anything.
- **Invalid model or size** — you passed something outside the supported set.
  Call `list_sd_models()` and pick from what it returned.

## Reporting a generated image

Give them the path. It is the only part of the result they can act on:

> Saved to `~/.gaia/cache/sd/images/a_red_bicycle_..._SDXL-Turbo_....png` (18s).
>
> Prompt used: "a red bicycle leaning against a brick wall, morning sunlight,
> shallow depth of field, photographic" — say the word if you want it warmer,
> wider, or at a different time of day.

Never describe an image you did not generate, and never claim a file exists
because the call was made — check `status` first.

## Fork this

Pin the style clause in step two to your own house look (brand palette, flat
vector, isometric) and the skill stops needing to be told it every time.
