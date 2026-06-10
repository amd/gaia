# GAIA Website + Agent Hub Redesign — Design Spec

**Date:** 2026-06-09
**Scope:** Phase 1 — shared design tokens + full redesign of the marketing website (`website/`) and Agent Hub pages. Phase 2 (committed follow-up, separate spec): restyle the Agent UI (`src/gaia/apps/webui/`) against the same tokens.
**Status:** Approved by user (brainstorming session 2026-06-09).

## Goals

The current site looks generic, the hub reads as a file listing rather than a marketplace, the homepage doesn't sell GAIA, and the overall presentation is overwhelming. The redesign delivers:

1. **Minimal, calm, premium** — less information per screen, more identity.
2. **AMD brand DNA, elevated** — AMD's black/white foundations and geometry, executed with more restraint and polish than amd.com itself.
3. **Gold, not red** — AMD gold is the sole accent color across all surfaces.
4. **Consumer excitement** — energy comes from light and motion (aurora, living terminal), not from density.
5. **One product, three surfaces** — website, Agent Hub, and Agent UI share tokens, components, and experience; they interrelate (deep links, same catalog, same vocabulary).

## Non-goals (Phase 1)

- No framework change (stays Astro + Tailwind, fully static).
- No changes to the catalog data plumbing (`HUB_CATALOG_URL` build-time fetch, fixture mode, worker contract).
- No Agent UI code changes (Phase 2), beyond ensuring the token package is consumable by it.
- No new content surfaces (blog, showcase, docs site) — simplification, not expansion.

## 1. Design system (shared tokens)

**Single source of truth** consumed by both the Astro website and (Phase 2) the React/Vite Agent UI:

- `design/tokens.css` — CSS custom properties for both themes (`:root` = light, `[data-theme="dark"]` = dark, with `@media (prefers-color-scheme)` defaults).
- `design/tailwind-preset.mjs` — Tailwind preset mapping the variables to utilities. The website's `tailwind.config.mjs` and (Phase 2) the webui Tailwind config both use `presets: [gaiaPreset]`.

Both files live in `website/src/design/` for Phase 1 and move to a shared location when the Agent UI adopts them (the preset is plain ESM with no Astro dependency, so the move is mechanical).

### Palette

| Token | Dark | Light |
|---|---|---|
| `--bg` | `#0a0a0b` (pure black) | `#fcfcfa` (warm white) |
| `--surface` | `#111113` | `#ffffff` |
| `--border` | `#1f1f22` (hairline) | `#e8e8e5` |
| `--text` | `#f0f0ee` | `#1a1a1a` |
| `--muted` | `#8a8a8e` | `#8a8a85` |
| `--gold` (graphic) | `#E2A33E` | `#E2A33E` |
| `--gold-text` (accessible text) | `#E2A33E` | `#A87B2D` |
| `--gold-glow` | `rgba(226,163,62,0.18–0.55)` radial | warm champagne wash |

Rules: near-monochrome surfaces; **gold is the only accent** and appears exclusively as (a) light/glow, (b) eyebrow labels, (c) interactive moments (links, install actions, focus). Never decorative fills. No red anywhere.

### Typography

- **Inter** — UI and headlines. Medium/semibold weights, sentence case, tight tracking (`-0.02em` to `-0.03em` on display sizes).
- **JetBrains Mono** — strictly commands, terminal content, and version/checksum metadata.
- Quiet-minimal voice: no uppercase display type, no heavy editorial treatments.

### Geometry & motion

- Sharp-to-minimal radii (0–12px), hairline borders, generous whitespace.
- Motion vocabulary: aurora drift (slow CSS keyframes), terminal typing (small vanilla JS loop, no deps), gold pulse on copy, subtle hover lift on rows/cards. `prefers-reduced-motion` disables all of it.

### Theming mechanics

- Toggle in the nav; default follows `prefers-color-scheme`; persisted to `localStorage`; applied via `data-theme` on `<html>` with a tiny inline head script to avoid flash-of-wrong-theme.

## 2. Component recipes

Documented as Astro components in `website/src/components/`, designed so their markup/class recipes port 1:1 to React in Phase 2:

- **Eyebrow** — small uppercase tracked label in gold.
- **TerminalBlock** — dark surface, mono type, gold `$` prompt; optional auto-typing mode (the "living terminal").
- **InstallCommand** — click-to-copy command chip with gold pulse feedback.
- **AgentRow** — icon tile, name, one-line description, gold `install ›` affordance.
- **FeaturedCard** — large card with aurora glow treatment + inline install command.
- **AuroraBg** — positioned radial-gradient glow layers with drift animation.
- **ThemeToggle** — sun/moon, persists choice.
- **Nav / Footer** — minimal: wordmark ("GAIA" + quiet "by AMD"), Hub, Docs, GitHub; footer with license/links only.

## 3. Homepage (`/`)

Radically simplified to five beats, in order:

1. **Aurora hero** — gold glow drifting behind one headline ("AI that runs on your machine."), one subline (private, Ryzen AI), two CTAs: *Get started* (docs/install) and *Browse agents* (→ `/hub`).
2. **Living terminal** — auto-types `gaia agent install summarize`, shows the response, loops through 2–3 agents.
3. **Three pillars** — Private by default · Accelerated by Ryzen AI · Open source. One line each, no cards-of-cards.
4. **Featured agents strip** — 3 `FeaturedCard`s pulled from the catalog → each links to its hub page.
5. **Footer.**

Everything in the current homepage not serving these beats is cut.

## 4. Hub catalog (`/hub`)

Top to bottom:

1. **Featured spotlight** — one curated agent in a `FeaturedCard` with aurora treatment and inline install command. Curation: a `featured` list constant in the page (manual editorial pick), falling back to first verified agent.
2. **Pill filters + search** — category pills (All + categories present in catalog) and the existing search box. Existing client-side filter JS is retained, restyled to the new tokens.
3. **Clean rows** — every agent as an `AgentRow` in a single bordered list (no card grid). Row hover reveals the gold **Open in GAIA ›** affordance (the consumer action); the whole row links to the detail page. Deprecated agents sink to the bottom with muted styling, as today.

## 5. Agent detail (`/hub/<id>`)

- **Calm header** — icon tile, name, one-liner, security-tier badge (monochrome + gold for verified), and the action pair:
  - **Primary CTA: "Open in GAIA"** (gold) — `gaia://hub/install/<id>` deep link. The Agent UI is the consumer surface: it launches, installs the agent if needed, and runs it. A quiet "Don't have GAIA yet?" link beneath routes to Get started.
  - **Secondary: `InstallCommand`** — click-to-copy CLI command (gold pulse) for terminal users.
- **README** — rendered markdown in a readable measure (~70ch) column; existing hardened renderer unchanged.
- **Metadata sidebar** — quiet: version, size, author, license, models, requirements, platforms, permissions. Tabular hairline rows, mono for values.
- **Terminal moment** — a `TerminalBlock` showing `gaia agent install <id>` + first-run output (static text derived from the agent's interfaces; auto-typing on scroll-into-view).

## 6. Interrelation (website ↔ hub ↔ Agent UI)

- **The core consumer flow:** browse the Hub on the web → click an agent → **Agent UI opens via `gaia://hub/install/<id>`, installs, and runs that agent**. The website's job is discovery; the Agent UI's job is use. "Open in GAIA" is therefore the primary CTA on detail pages (and the row-hover affordance on `/hub`).
- Phase 2 must verify/complete the app side of the handoff: the Agent UI's `gaia://` protocol registration and its install-then-run handling of `hub/install/<id>`. The website emits the link regardless and degrades gracefully (Get started path) when the app isn't installed.
- The Agent UI hub panel (Phase 2) consumes the **same live catalog** (`/index.json` contract) and re-implements `AgentRow`/`FeaturedCard` recipes in React so browsing feels identical in-app.
- Install command strings, category names, icon vocabulary, and tier badges are identical across all three surfaces.
- **Docs site (Mintlify, `docs/`) brand alignment — in Phase 1 scope:** gold palette (`#A87B2D` primary / `#E2A33E` accents) replacing red in `docs/docs.json`, Inter typography, system light/dark default. Cross-links in both directions (website nav → docs; docs navbar "Main Site" → `https://amd-gaia.ai`). Mintlify theming limits mean palette/type/tone parity, not full component parity; deeper custom CSS is future work.
- Phase 2 entry points for the motion vocabulary: aurora in Agent UI onboarding/empty states; TerminalBlock in its install flows.

## 7. Implementation notes

- Stack unchanged: Astro 4 + Tailwind 3, static output, `serve dist` on Railway.
- Data layer unchanged: `getCatalog()` / `getAgent()` from `src/data/catalog.ts` (fixture or `HUB_CATALOG_URL`).
- Animations: pure CSS + one small vanilla JS module for terminal typing; no new runtime dependencies.
- Accessibility: WCAG AA contrast in both themes (hence `--gold-text` darkening on light), `prefers-reduced-motion` respected, focus states in gold.
- The existing `src/data/markdown.ts` renderer and its sanitization are reused as-is.

## 8. Error handling

Unchanged from current behavior (per repo No Silent Fallbacks): live-catalog fetch failure fails the build with an actionable error; fixture mode only when `HUB_CATALOG_URL` is unset. The theme script defaults to OS preference when `localStorage` is empty — that is schema-default behavior, not a fallback path.

## 9. Testing & verification

- `npm run build` (includes `astro check` typecheck) passes in fixture mode and live mode against the local worker stack.
- Visual verification on the local stack (worker :8791 → publish 11 agents → site :8792): homepage, `/hub`, and at least 2 detail pages reviewed in **both themes** before sign-off.
- Lighthouse sanity pass (performance + a11y) on the built site.
- Grep-level checks: no `#ED1C24`/red tokens remain; both `data-theme` values render.

## 10. Future work (out of scope here, captured for follow-up)

- **Agent UI restyle (Phase 2)** — committed follow-up; consumes the shared tokens and component recipes.
- **ISV distribution program** — the Hub is the intended channel for third-party ISVs to publish agents. The worker already provides the primitives (per-publisher bearer tokens, author scoping, version immutability, server-side checksums). A separate brainstorm/spec must cover: ISV onboarding and token issuance/rotation, security-tier policy (what earns `verified`), artifact review/signing, takedown/deprecation policy, and how third-party agents are presented in the Hub UI (badging, publisher pages). The redesign keeps `security_tier` badging visible so this slots in without rework.

## 11. Acceptance criteria

1. Both themes work with toggle + OS default, no flash of wrong theme.
2. Homepage reduced to the five beats; aurora + living terminal present and smooth.
3. Hub = spotlight + pills + rows; search/filter still work.
4. Detail pages: copyable install, Open in GAIA deep link, readable README, quiet sidebar.
5. All tokens come from the shared preset/variables — no hardcoded palette values in components.
6. Build passes in both catalog modes; site deploys on Railway unchanged (`serve dist`).
