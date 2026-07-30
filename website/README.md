# GAIA Website

Developer-focused landing page and Agent Hub for GAIA - Local AI Agents framework.

Serves `amd-gaia.ai` (landing page + `/hub`). The `/docs` tab is Mintlify, a
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

## Hub catalog data (`HUB_CATALOG_URL`)

The Agent Hub pages (`/hub`) are built **entirely from the live hub catalog**,
fetched at build time from `${HUB_CATALOG_URL}/index.json` (the agent-hub Worker,
`workers/agent-hub/`).

**`HUB_CATALOG_URL` is required. There is no bundled fixture and no offline
path.** If it is unset, or the fetch fails, or the shape is wrong, the build
**fails loudly** — it never falls back to stale data, so the site can never drift
from what is actually published. This is deliberate; do not add a fallback.

```bash
HUB_CATALOG_URL=https://hub.amd-gaia.ai npm run build
```

Consequences worth knowing before you debug a red build:

- **A hub outage blocks every website build** — local, CI, and deploy alike.
  A failing build here is often a hub problem, not a website problem. Check
  `GET https://hub.amd-gaia.ai/health` first.
- The entry shape is defined by `workers/agent-hub/schemas/index.schema.json`
  and mirrored by the `Agent` interface in `src/data/catalog.ts`.

### The "in development" list is a committed snapshot

Unpublished agents (those in `hub/agents/<id>/` but not yet in the live catalog)
are listed from `src/data/in-development.json` — a **committed snapshot** of the
agent manifests, not a build-time read of `../hub/`. The Railway deploy uploads
only `website/`, so `hub/` does not exist in the production build context.

**Whenever an agent is added, removed, or renamed under `hub/agents/`, regenerate
the snapshot from a full checkout and commit the result:**

```bash
cd website
npm run generate:agents    # rewrite the snapshot
npm run check:agents       # verify it matches the manifests (also run in CI)
```

See `src/data/inDevelopment.ts` and `scripts/generate-in-development.mjs` for the
full rationale.

## Project Structure

```
website/
├── public/                          # Static assets
│   ├── favicon.ico
│   ├── gaia-icon.png
│   └── robots.txt
├── scripts/
│   └── generate-in-development.mjs  # Regenerates the in-development snapshot
├── src/
│   ├── components/                  # 21 Astro components
│   │   ├── AgentIcon.astro
│   │   ├── AgentRow.astro
│   │   ├── CodePanel.astro
│   │   ├── ComingSoonRow.astro
│   │   ├── DocTabs.astro
│   │   ├── DownloadButton.astro
│   │   ├── EntryPoints.astro
│   │   ├── Eyebrow.astro
│   │   ├── FeaturedCard.astro
│   │   ├── FileTree.astro
│   │   ├── Footer.astro
│   │   ├── Header.astro
│   │   ├── InstallCard.astro
│   │   ├── InstallMethods.astro
│   │   ├── Marquee.astro
│   │   ├── SidebarCard.astro
│   │   ├── StarField.astro
│   │   ├── StatBlock.astro
│   │   ├── Terminal.astro
│   │   ├── ThemeToggle.astro
│   │   └── WayCard.astro
│   ├── data/
│   │   ├── catalog.ts               # Live hub catalog access (build-time fetch)
│   │   ├── fileTree.ts              # Nested tree from package file listings
│   │   ├── in-development.json      # Committed snapshot of unpublished agents
│   │   ├── inDevelopment.ts         # Snapshot loader
│   │   ├── markdown.ts              # Dependency-free Markdown → semantic HTML
│   │   └── *.test.ts                # Vitest unit tests
│   ├── design/
│   │   ├── global.css               # Owns the @tailwind directives
│   │   ├── tailwind-preset.mjs
│   │   └── tokens.css               # Design tokens
│   ├── layouts/
│   │   └── Layout.astro             # Base HTML layout
│   ├── pages/
│   │   ├── hub/
│   │   │   ├── [id].astro           # Agent detail page (one per catalog entry)
│   │   │   └── index.astro          # Agent Hub listing
│   │   └── index.astro              # Landing page
│   ├── scripts/
│   │   └── starfield.js
│   └── env.d.ts
├── astro.config.mjs
├── postcss.config.mjs
├── railway.json                     # Railway build + start commands
├── tailwind.config.mjs
├── tsconfig.json
└── package.json
```

## Design System

`src/design/tokens.css` is the single source of truth (shared with the Agent UI);
`src/design/tailwind-preset.mjs` maps those variables to `g-*` Tailwind
utilities. Read those files rather than trusting a copy — the headlines only:

- **Accent**: graphic gold `#E7A33C` (`--g-gold`) — *not* AMD red
- **Ground**: dark `#08080a` is the designed default; light `#fbfaf7` is derived
- **Font (Display)**: Space Grotesk — headings and the wordmark only
- **Font (UI)**: Inter
- **Font (Code)**: JetBrains Mono

Theme is set pre-paint by an inline script in `src/layouts/Layout.astro`, which
toggles `[data-theme="dark"]`. Code panels stay dark in both themes.

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

Both `deploy_website.yml` and `website-ci.yml` are path-filtered to `website/**`.
A commit that only adds an agent under `hub/agents/` therefore **neither validates
the in-development snapshot nor redeploys the site** — so a newly published agent
will not appear on `/hub` on its own.

Release workflows work around this by dispatching the deploy explicitly after a
successful publish (see the "Redeploy the website to publish the new catalog
entry" step in `.github/workflows/release_agent_email.yml`). You can do the same
by hand:

```bash
gh workflow run deploy_website.yml --ref main
```

### Rollback

To take the Astro site off the apex, delete the `amd-gaia.ai/*` Worker route (in
the Cloudflare dashboard, or `npx wrangler triggers`) — traffic falls back to the
zone origin, Mintlify. `/docs` keeps working; `/` and `/hub` disappear. This is
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

- [ ] `og-image.png` (1200x630) - Social share image
- [ ] Integration logos (VS Code, Blender, Jira, Docker)
- [ ] GAIA logo SVG (if different from favicon)

## License

MIT License - Copyright (C) 2024-2026 Advanced Micro Devices, Inc.
