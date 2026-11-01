---
name: image-gen
description: Turn a description into an image with a downloaded Lemonade model (Stable Diffusion or TheNoise), then iterate on it. Use for draw, sketch, paint, render, "make a picture of", "generate an image", concept art, game art, backgrounds, thumbnails, logos, wallpapers, mockups, and requests to change the last image rather than replace it.
license: MIT
version: 1.1.0
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
the first load can take a while. Select downloaded models, and plan the
prompt before generating rather than firing off four variations and picking one.

It also costs the conversation. Drawing loads the image model in place of the
chat model, so the reply after an image pauses while the chat model comes back.
Generate when the user actually asked for a picture — not to illustrate an
answer they did not ask to have illustrated.

A slide, deck, presentation, poster with real copy, or chart is not a picture.
Stable Diffusion cannot render readable text, so the user gets a blur of fake
letters. Those are files: ask whether they want PowerPoint (.pptx) or HTML if
they did not say, and build that instead.

## Check what is loaded before you promise anything

Call `list_sd_models()` first. It lists downloaded image models, including
TheNoise models. Select an exact model ID from its `models` list, and read
`image_defaults`. The `default_model` is only used when `model` is omitted.
Do not assume a model is resident or silently switch to a different model.

Legacy SD models include approximate time estimates. For TheNoise models,
no reliable device-independent estimate is available; do not invent one.

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

Leave `steps`, `cfg_scale`, and `size` unset unless you have a reason. The
legacy SD models use their historical defaults; TheNoise models use Lemonade's
per-model image defaults. Never force SDXL's 20 steps or 7.5 CFG onto a
4-step or 8-step distilled TheNoise model.

## Iterate instead of starting over

`get_generation_history()` returns this session's generations with the exact
prompt, model, size and seed of each. When the user says "same but at sunset"
or "make it wider", read the previous entry, change the one thing they asked
about, and keep everything else — including the `seed` if it was supplied.
When the server selected an unknown seed, iteration is not deterministic;
changing the prompt can produce a substantially different image.

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
  The server is fine. Tell them to pre-fetch it with the pull command the
  error names for their install, then retry, rather than restarting anything.
- **Invalid model or size** — choose a downloaded image model from
  `list_sd_models()`; TheNoise accepts backend-specific dimensions.

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
