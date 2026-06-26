# GAIA Presentation — shared assets

This directory is **not a skill** (no `SKILL.md`). It is the shared design system used by
the two presentation skills:

- [`gaia-technical-presentation`](../gaia-technical-presentation/SKILL.md) — engineering-audience slides
- [`gaia-executive-presentation`](../gaia-executive-presentation/SKILL.md) — leadership-audience slides

Both skills inline these files (verbatim) into the self-contained HTML they generate, so a
single source of truth produces a consistent look across both tiers.

```
assets/
  deck.css          design tokens, slide shell, placeholder/draft-banner styles, @media print profile
  fonts.css         base64-embedded Archivo + Hanken Grotesk (offline; no CDN)
  deck-viewer.js    screen-only viewer (arrow-key nav + page counter); disabled under @media print
  slide-blocks.html copy-paste slide patterns, tier-tagged ([both]/[technical]/[executive])
tools/
  build_fonts.py    regenerates assets/fonts.css from a reference deck bundle (deterministic)
```

Edit the design system here once; both skills pick it up. Do not duplicate these files into
the skill directories.

`assets/fonts.css` is committed and works on its own — you do **not** need to regenerate it to
use the skills. Run `python3 tools/build_fonts.py <reference-deck.html>` only to rebuild it
after changing the brand fonts. The `<reference-deck.html>` argument is required (the script
fails loudly without it): pass any self-contained deck bundle whose
`<script type="__bundler/manifest">` tag carries the base64 Archivo + Hanken Grotesk woff2
data — e.g. a deck exported from the GAIA roadmap/visualize bundler. Re-running against the
same bundle is byte-for-byte deterministic.
