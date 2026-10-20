# GAIA Website

Developer-focused landing page for GAIA - Local AI Agents framework.

Serves `amd-gaia.ai` (the landing page and its downloads). The retired `/hub`
pages redirect to `/#install` (`public/serve.json`). The `/docs` tab is Mintlify, a
separate origin — see [Deployment](#deployment) for how the two are stitched
together.

## Tech Stack

- **Framework**: [Astro](https://astro.build) (static, fast)
- **Styling**: [Tailwind CSS](https://tailwindcss.com)
- **Hosting**: Railway (production), behind a Cloudflare Worker that owns the apex

## Development

```bash
# Install dependencies
npm install

# Start dev server (HUB_CATALOG_URL is REQUIRED — see below)
HUB_CATALOG_URL=https://hub.amd-gaia.ai npm run dev

# Build for production
HUB_CATALOG_URL=https://hub.amd-gaia.ai npm run build

# Preview production build
npm run preview

# Unit tests
npm test
```

## Download data (`HUB_CATALOG_URL`)

The download links on the home page are built from what the download server
(`hub.amd-gaia.ai`, the `workers/agent-hub/` Worker) actually published, fetched
at build time from `${HUB_CATALOG_URL}/agents/<id>/manifest.json`.

**`HUB_CATALOG_URL` is required. There is no bundled fixture and no offline
path.** If it is unset, or the fetch fails, or the shape is wrong, the build
**fails loudly** — it never falls back to stale filenames. This is deliberate;
do not add a fallback. A download-server outage therefore blocks every website
build; check `GET https://hub.amd-gaia.ai/health` first.

## Project Structure

```
website/
├── public/                          # Static assets
│   ├── favicon.ico
│   ├── gaia-icon.png
│   ├── robots.txt
│   └── serve.json                   # `serve` redirects (retired /hub → /#install)
├── scripts/
├── src/
│   ├── components/                  # Astro components (Downloads, Header, Footer, …)
│   ├── data/
│   │   ├── catalog.ts               # Published-binary lookup for the downloads
│   │   ├── github.ts                # Build-time GitHub / npm stats
│   │   └── *.test.ts                # Vitest unit tests
│   ├── design/
│   │   ├── global.css               # Owns the @tailwind directives
│   │   ├── tailwind-preset.mjs
│   │   ├── tokens.css               # Design tokens
│   │   └── tokens.contrast.test.ts  # Contrast floors, enforced
│   ├── layouts/
│   │   └── Layout.astro             # Base HTML layout
│   ├── pages/
│   │   └── index.astro              # Landing page
│   └── env.d.ts
├── .railwayignore                   # Excluded from the `railway up` upload
├── astro.config.mjs
├── postcss.config.mjs
├── railway.json                     # Railway build + start commands
├── tailwind.config.mjs
├── tsconfig.json
└── package.json
```

## Design System

The contract is the "The website" section of
[`docs/spec/gaia-design-language.mdx`](../docs/spec/gaia-design-language.mdx).
`src/design/tokens.css` implements it; `src/design/tailwind-preset.mjs` maps
those variables to `g-*` Tailwind utilities. Read those files rather than
trusting a copy — the headlines only:

- **Dark-only**: black `#000000` ground, white type. There is no theme toggle.
- **Accent**: AMD red `#ED1C24` at display size (`--g-brand`), lifted to
  `#FF4B51` for text (`--g-accent`) and deepened to `#D3121A` for button fills
- **Release candidates**: AMD orange `#F26522` (`--g-rc`), and nothing else
- **Font (Display)**: Antonio — headings, buttons, figures, the wordmark
- **Font (UI)**: Titillium Web, standing in for AMD's Klavika
- **Font (Code)**: JetBrains Mono
- **Background**: `src/scripts/starfield.ts`, a canvas of streaking stars. It
  holds a still frame under `prefers-reduced-motion` or when a visitor pauses it
  from the footer.

## Deployment

Production is **Railway** — service `website` in the Railway project
`gaia-agent-hub`. It is *not* Cloudflare Pages.

### The apex is owned by a Cloudflare Worker

`amd-gaia.ai` is served by the `website-router` Cloudflare Worker, which splits
traffic by path:

| Path      | Origin                       |
| --------- | ---------------------------- |
| `/docs*`  | Mintlify (the docs site)     |
| everything else | Railway (this Astro site) |

**Deploying this site alone does not put it on `amd-gaia.ai`.** Railway serves it
at its own hostname; the Worker is what maps the apex onto it.

The Worker passes the Railway public hostname as a `WEBSITE_ORIGIN` variable —
`website-production-82ab.up.railway.app` at the time of writing. **Rotating the
Railway hostname breaks the apex until that variable is updated and the Worker
redeployed.** The Worker's source is being brought into the repo under
`workers/website-router/`; until that lands, its configuration lives only in
Cloudflare, so check the dashboard to confirm the current origin.

`hub.amd-gaia.ai` is a *separate* Worker (`workers/agent-hub/`) on its own custom
domain, and is not affected by the apex route.

### Automated deploys

`.github/workflows/deploy_website.yml` drives `railway up --service website --ci`.
Railway builds from `railway.json` (NIXPACKS, `npm install && npm run build`,
served by `serve dist`) — it does **not** auto-detect Astro.

Two pieces of configuration are required, and a missing one is a hard failure, not
a degraded deploy:

| What | Where | Why |
| ---- | ----- | --- |
| `RAILWAY_TOKEN` | GitHub repo secret | A Railway **project** token for `gaia-agent-hub`, production environment (Railway → project → Settings → Tokens). |
| `HUB_CATALOG_URL=https://hub.amd-gaia.ai` | Railway service variable on `website` | The build has no fixture fallback, so without it the deploy **fails**. Set it in Railway → Variables. |

> Both live outside this repo (GitHub secrets and the Railway dashboard), so they
> cannot be verified from a checkout. Treat the table as the required
> configuration and confirm it in the respective dashboard when a deploy fails.

### Deploys only fire on `website/**` changes

Both `deploy_website.yml` and `website-ci.yml` are path-filtered. The download
links are resolved at build time, so a release that publishes new binaries
dispatches the deploy explicitly (see the release workflows). You can do the
same by hand:

```bash
gh workflow run deploy_website.yml --ref main
```

### Rollback

To take the Astro site off the apex, delete the `amd-gaia.ai/*` Worker route (in
the Cloudflare dashboard, or `npx wrangler triggers`) — traffic falls back to the
zone origin, Mintlify. `/docs` keeps working; `/` disappears. This is
the fastest way to revert a bad website deploy without touching Railway.

### Manual deploy

```bash
HUB_CATALOG_URL=https://hub.amd-gaia.ai npm run build
# Upload contents of dist/ to your hosting provider
```

## Assets Needed

Open TODO — these are not yet in `public/`, which currently holds only
`favicon.ico`, `gaia-icon.png`, and `robots.txt`. Nothing references them, so
nothing is broken; they are polish items, not blockers.

- [ ] `og-image.png` (1200x630) - Social share image. Until it exists, share cards use the square `gaia-icon.png` with `twitter:card=summary`
- [ ] Integration logos (VS Code, Blender, Jira, Docker)
- [ ] GAIA logo SVG (if different from favicon)

## License

MIT License - Copyright (C) 2024-2026 Advanced Micro Devices, Inc.
