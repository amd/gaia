# GAIA Website + Hub Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-skin and restructure the GAIA website + Agent Hub to the approved minimal black/white/AMD-gold design system with light+dark themes, aurora + living-terminal energy, and "Open in GAIA" as the primary consumer flow.

**Architecture:** Shared design tokens (CSS variables, RGB triplets) + a Tailwind preset consumed by the Astro site now and the React Agent UI in Phase 2. New primitive components (AuroraBg, TerminalBlock, InstallCommand, Eyebrow, AgentRow, FeaturedCard, ThemeToggle) compose three rebuilt pages. Data layer (`src/data/catalog.ts`, `markdown.ts`) unchanged.

**Tech Stack:** Astro 4, Tailwind 3, vanilla JS (no new deps). Spec: `docs/superpowers/specs/2026-06-09-website-hub-redesign-design.md`.

**Verification model:** This is a static site — "tests" are `npm run build` (runs `astro check` typecheck) plus grep assertions on `dist/` output, and visual checks on the local stack. Each task ends with a passing build + commit.

**Pre-condition (Task 0):** The working tree contains verified-but-uncommitted Agent Hub deployment work (worker catalog enrichment, Dockerfile/railway config, publish pipeline). Commit it on its own branch first so redesign commits are clean.

---

### Task 0: Branch hygiene

**Files:** none (git only)

- [ ] **Step 1: Commit the in-flight Agent Hub work on its own branch** (already validated: worker vitest 52 pass, pytest 94 pass, lint pass, E2E publish verified)

```bash
cd /Users/kovtcharov/Work/gaia3
git checkout -b feat/agent-hub-railway
git add workers/agent-hub website/railway.json website/README.md website/package.json website/package-lock.json \
  website/src/data/catalog.ts website/src/data/markdown.ts \
  src/gaia/hub/packager.py src/gaia/hub/publisher.py \
  util/publish_agents_to_hub.py tests/unit/test_publish_agents_to_hub.py \
  tests/unit/test_hub_packager.py tests/unit/test_hub_publisher.py
git commit -m "feat(hub): Railway deployment, enriched catalog contract, batch publish pipeline"
```

- [ ] **Step 2: Create the redesign branch from there**

```bash
git checkout -b feat/website-redesign
```

Note: `docs/guides/npu.mdx` and `uv.lock` changes in the tree are unrelated — leave them unstaged. Do NOT commit `docs/plans/typescript-sdk.mdx`.

---

### Task 1: Design tokens + Tailwind preset

**Files:**
- Create: `website/src/design/tokens.css`
- Create: `website/src/design/tailwind-preset.mjs`
- Modify: `website/tailwind.config.mjs`

- [ ] **Step 1: Write `website/src/design/tokens.css`**

```css
/* Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved. */
/* SPDX-License-Identifier: MIT */

/* GAIA shared design tokens — single source of truth for website + Agent UI.
   Values are RGB triplets so Tailwind opacity modifiers work
   (rgb(var(--g-bg) / <alpha-value>)). Light is :root; dark overrides via
   [data-theme="dark"], set pre-paint by the inline script in Layout.astro. */

:root {
  --g-bg: 252 252 250;        /* warm white */
  --g-surface: 255 255 255;
  --g-border: 232 232 229;
  --g-text: 26 26 26;
  --g-muted: 122 122 117;
  --g-gold: 226 163 62;       /* #E2A33E — graphic gold (stripes, glows, icons) */
  --g-gold-text: 154 111 38;  /* #9A6F26 — AA-contrast gold for text on light */
  --g-glow: 0.20;             /* aurora opacity scale */
}

[data-theme='dark'] {
  --g-bg: 10 10 11;           /* #0a0a0b pure black */
  --g-surface: 17 17 19;
  --g-border: 31 31 34;
  --g-text: 240 240 238;
  --g-muted: 142 142 146;
  --g-gold: 226 163 62;
  --g-gold-text: 226 163 62;  /* gold passes AA on black */
  --g-glow: 0.38;
}

/* Motion vocabulary */
@keyframes g-drift {
  from { transform: translate3d(-4%, 0, 0) scale(1); opacity: 0.85; }
  to   { transform: translate3d(4%, -2%, 0) scale(1.1); opacity: 1; }
}
@keyframes g-blink { 50% { opacity: 0; } }
@keyframes g-pulse {
  0%   { box-shadow: 0 0 0 0 rgb(var(--g-gold) / 0.45); }
  100% { box-shadow: 0 0 0 14px rgb(var(--g-gold) / 0); }
}
@keyframes g-rise {
  from { opacity: 0; transform: translateY(12px); }
  to   { opacity: 1; transform: translateY(0); }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
}
```

- [ ] **Step 2: Write `website/src/design/tailwind-preset.mjs`**

```js
// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// GAIA Tailwind preset — maps the CSS variables in tokens.css to utilities.
// Consumed by the website now and the Agent UI (src/gaia/apps/webui) in
// Phase 2 via `presets: [gaiaPreset]`. Plain ESM, no framework dependency.

/** @type {import('tailwindcss').Config} */
export default {
  theme: {
    extend: {
      colors: {
        'g-bg': 'rgb(var(--g-bg) / <alpha-value>)',
        'g-surface': 'rgb(var(--g-surface) / <alpha-value>)',
        'g-border': 'rgb(var(--g-border) / <alpha-value>)',
        'g-text': 'rgb(var(--g-text) / <alpha-value>)',
        'g-muted': 'rgb(var(--g-muted) / <alpha-value>)',
        'g-gold': 'rgb(var(--g-gold) / <alpha-value>)',
        'g-gold-text': 'rgb(var(--g-gold-text) / <alpha-value>)',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'Consolas', 'monospace'],
      },
      animation: {
        'g-drift': 'g-drift 7s ease-in-out infinite alternate',
        'g-blink': 'g-blink 1s steps(1) infinite',
        'g-pulse': 'g-pulse 0.5s ease-out 1',
        'g-rise': 'g-rise 0.5s ease-out both',
      },
    },
  },
};
```

- [ ] **Step 3: Replace `website/tailwind.config.mjs`** (old `gaia-*` palette removed — pages still referencing it keep building because Tailwind ignores unknown classes; Tasks 2–7 remove every usage and Task 8 grep-asserts none remain)

```js
// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import gaiaPreset from './src/design/tailwind-preset.mjs';

/** @type {import('tailwindcss').Config} */
export default {
  presets: [gaiaPreset],
  content: ['./src/**/*.{astro,html,js,jsx,md,mdx,svelte,ts,tsx,vue}'],
  plugins: [],
};
```

- [ ] **Step 4: Verify build still passes**

Run: `cd website && npm run build`
Expected: `[build] Complete!` (pages still use old literal classes; nothing references tokens yet)

- [ ] **Step 5: Commit**

```bash
git add website/src/design website/tailwind.config.mjs
git commit -m "feat(website): shared design tokens + Tailwind preset (black/white/AMD-gold, both themes)"
```

---

### Task 2: Theme mechanics + Layout + Nav + Footer

**Files:**
- Modify: `website/src/layouts/Layout.astro`
- Create: `website/src/components/ThemeToggle.astro`
- Modify: `website/src/components/Header.astro` (full rewrite)
- Modify: `website/src/components/Footer.astro` (restyle to tokens)

- [ ] **Step 1: Update `Layout.astro`** — import tokens, pre-paint theme script, token body classes. Keep the existing head metadata block (canonical/OG/Twitter/fonts) as-is except `theme-color`. Replace the `<html>` opening, add the script + stylesheet import, and replace `<body>`:

```astro
---
// (existing frontmatter unchanged)
import '../design/tokens.css';
---
<!doctype html>
<html lang="en" class="scroll-smooth">
  <head>
    <!-- existing meta block unchanged, except: -->
    <meta name="theme-color" content="#0a0a0b" media="(prefers-color-scheme: dark)" />
    <meta name="theme-color" content="#fcfcfa" media="(prefers-color-scheme: light)" />
    <!-- Pre-paint theme: avoids flash of wrong theme. localStorage empty -> OS preference. -->
    <script is:inline>
      (() => {
        const stored = localStorage.getItem('gaia-theme');
        const dark = stored ? stored === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
        document.documentElement.dataset.theme = dark ? 'dark' : 'light';
      })();
    </script>
  </head>
  <body class="bg-g-bg text-g-text font-sans antialiased min-h-screen transition-colors duration-200">
    <a href="#main-content" class="sr-only focus:not-sr-only focus:absolute focus:top-4 focus:left-4 focus:z-[100] focus:px-4 focus:py-2 focus:bg-g-gold focus:text-black focus:rounded-md focus:outline-none">
      Skip to main content
    </a>
    <slot />
  </body>
</html>
```

- [ ] **Step 2: Create `ThemeToggle.astro`**

```astro
---
// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
---
<button
  id="theme-toggle"
  type="button"
  aria-label="Toggle color theme"
  class="p-2 rounded-md text-g-muted hover:text-g-text border border-transparent hover:border-g-border transition-colors"
>
  <!-- sun (shown in dark mode) -->
  <svg data-icon="sun" class="w-4 h-4 hidden" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32 1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></svg>
  <!-- moon (shown in light mode) -->
  <svg data-icon="moon" class="w-4 h-4 hidden" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
</button>

<script>
  const btn = document.getElementById('theme-toggle')!;
  const sun = btn.querySelector('[data-icon="sun"]')!;
  const moon = btn.querySelector('[data-icon="moon"]')!;
  const render = () => {
    const dark = document.documentElement.dataset.theme === 'dark';
    sun.classList.toggle('hidden', !dark);
    moon.classList.toggle('hidden', dark);
  };
  btn.addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    localStorage.setItem('gaia-theme', next);
    render();
  });
  render();
</script>
```

- [ ] **Step 3: Rewrite `Header.astro`** — minimal nav: wordmark + "by AMD", Hub, Docs, GitHub, ThemeToggle. Keep the existing GitHub SVG path and the existing mobile-menu pattern, restyled; drop Discord from the top nav (it stays in the footer). Key markup:

```astro
---
import ThemeToggle from './ThemeToggle.astro';
---
<header class="fixed top-0 inset-x-0 z-50 bg-g-bg/80 backdrop-blur-md border-b border-g-border">
  <nav class="max-w-6xl mx-auto px-6 h-16 flex items-center justify-between">
    <a href="/" class="flex items-baseline gap-2">
      <span class="text-base font-bold tracking-tight text-g-text">GAIA</span>
      <span class="text-[10px] text-g-muted font-normal">by AMD</span>
    </a>
    <div class="flex items-center gap-1 sm:gap-2">
      <a href="/hub" class="px-3 py-2 text-sm text-g-muted hover:text-g-text transition-colors">Agent Hub</a>
      <a href="https://amd-gaia.ai/docs" target="_blank" rel="noopener noreferrer" class="px-3 py-2 text-sm text-g-muted hover:text-g-text transition-colors">Docs</a>
      <a href="https://github.com/amd/gaia" target="_blank" rel="noopener noreferrer" aria-label="GitHub" class="px-3 py-2 text-g-muted hover:text-g-text transition-colors"><!-- existing GitHub svg, w-4 h-4 --></a>
      <ThemeToggle />
    </div>
  </nav>
</header>
```

- [ ] **Step 4: Restyle `Footer.astro`** — swap every `gaia-*` class for the `g-*` equivalents (`text-gaia-muted`→`text-g-muted`, `border-gaia-border`→`border-g-border`, `bg-gaia-bg`→`bg-g-bg`, accent→`text-g-gold-text`). Content (license, GitHub, Discord, PyPI links) unchanged.

- [ ] **Step 5: Build + verify both themes render**

Run: `cd website && npm run build && grep -c 'data-theme' dist/index.html`
Expected: build completes; grep ≥ 1 (inline script present)

- [ ] **Step 6: Commit**

```bash
git add website/src/layouts/Layout.astro website/src/components/ThemeToggle.astro website/src/components/Header.astro website/src/components/Footer.astro
git commit -m "feat(website): light/dark theming, minimal nav, pre-paint theme script"
```

---

### Task 3: Primitive components (Eyebrow, AuroraBg, TerminalBlock, InstallCommand)

**Files:**
- Create: `website/src/components/Eyebrow.astro`
- Create: `website/src/components/AuroraBg.astro`
- Create: `website/src/components/TerminalBlock.astro`
- Create: `website/src/components/InstallCommand.astro`

- [ ] **Step 1: `Eyebrow.astro`**

```astro
---
interface Props { class?: string }
const { class: className = '' } = Astro.props;
---
<div class={`text-[11px] font-bold tracking-[0.14em] uppercase text-g-gold-text ${className}`}>
  <slot />
</div>
```

- [ ] **Step 2: `AuroraBg.astro`** — positioned glow layers; parent needs `relative overflow-hidden`.

```astro
---
// Gold aurora glow. Drift animation is disabled automatically by the
// prefers-reduced-motion rule in tokens.css.
interface Props { class?: string }
const { class: className = '' } = Astro.props;
---
<div class={`pointer-events-none absolute inset-0 ${className}`} aria-hidden="true">
  <div
    class="absolute inset-0 animate-g-drift"
    style="background:
      radial-gradient(ellipse 55% 45% at 28% 85%, rgb(var(--g-gold) / calc(var(--g-glow) * 1.4)), transparent 70%),
      radial-gradient(ellipse 45% 38% at 76% 18%, rgb(var(--g-gold) / calc(var(--g-glow) * 0.7)), transparent 70%);"
  ></div>
</div>
```

- [ ] **Step 3: `TerminalBlock.astro`** — static or auto-typing terminal. Lines are `{ prompt?: boolean; text: string; muted?: boolean }`.

```astro
---
interface Line { prompt?: boolean; text: string; muted?: boolean }
interface Props { lines: Line[]; typing?: boolean; class?: string }
const { lines, typing = false, class: className = '' } = Astro.props;
---
<div
  class={`g-terminal rounded-lg border border-g-border bg-[#0c0c0e] p-5 font-mono text-[13px] leading-relaxed text-left shadow-2xl ${className}`}
  data-typing={typing ? 'true' : undefined}
  data-lines={typing ? JSON.stringify(lines) : undefined}
>
  <div class="flex gap-1.5 mb-4" aria-hidden="true">
    <span class="w-2.5 h-2.5 rounded-full bg-[#2a2a2e]"></span>
    <span class="w-2.5 h-2.5 rounded-full bg-[#2a2a2e]"></span>
    <span class="w-2.5 h-2.5 rounded-full bg-[#2a2a2e]"></span>
  </div>
  <div class="g-terminal-body min-h-[5.5rem]">
    {!typing && lines.map((l) => (
      <div class={l.muted ? 'text-[#71717a]' : 'text-[#e8e8e6]'}>
        {l.prompt && <span class="text-g-gold select-none">$ </span>}{l.text}
      </div>
    ))}
  </div>
</div>

<script>
  // Auto-typing: starts when scrolled into view; respects reduced motion.
  const els = document.querySelectorAll<HTMLElement>('[data-typing="true"]');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  els.forEach((el) => {
    const lines = JSON.parse(el.dataset.lines || '[]') as { prompt?: boolean; text: string; muted?: boolean }[];
    const body = el.querySelector('.g-terminal-body')!;
    const renderAll = () => {
      body.innerHTML = lines
        .map((l) => `<div class="${l.muted ? 'text-[#71717a]' : 'text-[#e8e8e6]'}">${l.prompt ? '<span class="text-g-gold select-none">$ </span>' : ''}${l.text}</div>`)
        .join('');
    };
    if (reduced) { renderAll(); return; }
    let started = false;
    const io = new IntersectionObserver((entries) => {
      if (!entries.some((e) => e.isIntersecting) || started) return;
      started = true;
      io.disconnect();
      let li = 0, ci = 0;
      const rows: HTMLElement[] = [];
      const cursor = document.createElement('span');
      cursor.className = 'animate-g-blink text-g-gold';
      cursor.textContent = '▋';
      const tick = () => {
        if (li >= lines.length) { cursor.remove(); return; }
        const line = lines[li];
        if (ci === 0) {
          const row = document.createElement('div');
          row.className = line.muted ? 'text-[#71717a]' : 'text-[#e8e8e6]';
          if (line.prompt) row.innerHTML = '<span class="text-g-gold select-none">$ </span>';
          body.appendChild(row);
          rows.push(row);
          row.appendChild(cursor);
        }
        const row = rows[li];
        if (ci < line.text.length) {
          cursor.before(document.createTextNode(line.text[ci]));
          ci++;
          setTimeout(tick, line.prompt ? 34 : 8);
        } else {
          li++; ci = 0;
          setTimeout(tick, 420);
        }
      };
      tick();
    }, { threshold: 0.4 });
    io.observe(el);
  });
</script>
```

- [ ] **Step 4: `InstallCommand.astro`** — click-to-copy with gold pulse.

```astro
---
interface Props { command: string; class?: string }
const { command, class: className = '' } = Astro.props;
---
<button
  type="button"
  data-copy={command}
  class={`g-copy group inline-flex items-center gap-3 rounded-md border border-g-border bg-g-surface px-4 py-2.5 font-mono text-[13px] text-g-text hover:border-g-gold/50 transition-colors ${className}`}
>
  <span class="text-g-gold select-none" aria-hidden="true">$</span>
  <span>{command}</span>
  <span class="g-copy-label text-[11px] text-g-muted group-hover:text-g-gold-text transition-colors select-none">copy</span>
</button>

<script>
  document.querySelectorAll<HTMLButtonElement>('.g-copy').forEach((btn) => {
    btn.addEventListener('click', async () => {
      await navigator.clipboard.writeText(btn.dataset.copy || '');
      btn.classList.remove('animate-g-pulse');
      void btn.offsetWidth; // restart animation
      btn.classList.add('animate-g-pulse');
      const label = btn.querySelector('.g-copy-label')!;
      label.textContent = 'copied';
      setTimeout(() => (label.textContent = 'copy'), 1500);
    });
  });
</script>
```

- [ ] **Step 5: Build check** — components compile even when unused.

Run: `cd website && npm run build`
Expected: `[build] Complete!`

- [ ] **Step 6: Commit**

```bash
git add website/src/components/Eyebrow.astro website/src/components/AuroraBg.astro website/src/components/TerminalBlock.astro website/src/components/InstallCommand.astro
git commit -m "feat(website): primitive components — eyebrow, aurora, living terminal, copy command"
```

---

### Task 4: Catalog components (AgentRow, FeaturedCard)

**Files:**
- Create: `website/src/components/AgentRow.astro`
- Create: `website/src/components/FeaturedCard.astro`
- (Leave `AgentCard.astro` in place until Task 6 removes its last usage, then delete it there.)

- [ ] **Step 1: `AgentRow.astro`** — keeps the `data-*` attributes the hub filter JS reads (`data-id/name/description/tags/category/language/tier/deprecated`), same as today's `AgentCard`.

```astro
---
import AgentIcon from './AgentIcon.astro';
import { securityTierLabel, type Agent } from '../data/catalog';

interface Props { agent: Agent }
const { agent } = Astro.props;
---
<a
  href={`/hub/${agent.id}`}
  class={`agent-row group flex items-center gap-4 px-4 py-4 border-b border-g-border last:border-b-0 hover:bg-g-surface transition-colors ${agent.deprecated ? 'opacity-50' : ''}`}
  data-id={agent.id}
  data-name={agent.name.toLowerCase()}
  data-description={agent.description.toLowerCase()}
  data-tags={agent.tags.join(' ').toLowerCase()}
  data-category={agent.category}
  data-language={agent.language}
  data-tier={agent.security_tier}
  data-deprecated={agent.deprecated ? 'true' : 'false'}
>
  <span class="flex-none w-10 h-10 rounded-lg border border-g-border bg-g-surface flex items-center justify-center text-g-muted group-hover:text-g-gold-text group-hover:border-g-gold/40 transition-colors">
    <AgentIcon name={agent.icon} class="w-5 h-5" />
  </span>
  <span class="min-w-0 flex-1">
    <span class="flex items-baseline gap-2.5">
      <span class="text-[15px] font-semibold text-g-text">{agent.name}</span>
      {agent.security_tier === 'verified' && (
        <span class="text-[10px] font-bold tracking-wide uppercase text-g-gold-text">{securityTierLabel(agent.security_tier)}</span>
      )}
    </span>
    <span class="block text-[13px] text-g-muted truncate">{agent.description}</span>
  </span>
  <span class="flex-none text-[12px] font-medium text-g-gold-text opacity-0 group-hover:opacity-100 transition-opacity hidden sm:block">
    Open in GAIA ›
  </span>
</a>
```

- [ ] **Step 2: `FeaturedCard.astro`** — aurora spotlight with inline install.

```astro
---
import AgentIcon from './AgentIcon.astro';
import AuroraBg from './AuroraBg.astro';
import InstallCommand from './InstallCommand.astro';
import { type Agent } from '../data/catalog';

interface Props { agent: Agent; eyebrow?: string }
const { agent, eyebrow = 'Featured' } = Astro.props;
---
<a
  href={`/hub/${agent.id}`}
  class="relative block overflow-hidden rounded-xl border border-g-border bg-g-surface p-7 sm:p-9 hover:border-g-gold/40 transition-colors"
>
  <AuroraBg />
  <div class="relative">
    <div class="text-[11px] font-bold tracking-[0.14em] uppercase text-g-gold-text mb-4">{eyebrow}</div>
    <div class="flex items-center gap-3 mb-2">
      <span class="w-9 h-9 rounded-lg border border-g-border bg-g-bg/60 flex items-center justify-center text-g-gold-text">
        <AgentIcon name={agent.icon} class="w-5 h-5" />
      </span>
      <span class="text-xl font-bold tracking-tight text-g-text">{agent.name}</span>
    </div>
    <p class="text-[14px] text-g-muted max-w-md mb-6">{agent.description}</p>
    <span class="inline-flex items-center rounded-md border border-g-gold/40 px-4 py-2 font-mono text-[12px] text-g-gold-text">
      $ gaia agent install {agent.id}
    </span>
  </div>
</a>
```

(Note: the spotlight uses a static command chip, not `InstallCommand` — the whole card is a link; nested buttons inside anchors are invalid HTML. Remove the unused `InstallCommand` import if the linter flags it.)

- [ ] **Step 3: Build check**

Run: `cd website && npm run build`
Expected: `[build] Complete!`

- [ ] **Step 4: Commit**

```bash
git add website/src/components/AgentRow.astro website/src/components/FeaturedCard.astro
git commit -m "feat(website): AgentRow + FeaturedCard catalog components"
```

---

### Task 5: Homepage rebuild

**Files:**
- Modify: `website/src/pages/index.astro` (full rewrite — 1112 lines → ~150)

- [ ] **Step 1: Rewrite `index.astro`** to exactly five beats:

```astro
---
// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import Layout from '../layouts/Layout.astro';
import Header from '../components/Header.astro';
import Footer from '../components/Footer.astro';
import Eyebrow from '../components/Eyebrow.astro';
import AuroraBg from '../components/AuroraBg.astro';
import TerminalBlock from '../components/TerminalBlock.astro';
import FeaturedCard from '../components/FeaturedCard.astro';
import { getCatalog } from '../data/catalog';

const agents = await getCatalog();
const featured = agents.filter((a) => !a.deprecated).slice(0, 3);

const demoLines = [
  { prompt: true, text: 'gaia agent install summarize' },
  { muted: true, text: '✓ installed — runs on your NPU' },
  { prompt: true, text: 'gaia summarize quarterly-report.pdf' },
  { muted: true, text: 'Reading 48 pages… done. Summary written to summary.md' },
];
---

<Layout title="GAIA — AI that runs on your machine">
  <Header />
  <main id="main-content">

    <!-- 1. Aurora hero -->
    <section class="relative overflow-hidden border-b border-g-border">
      <AuroraBg />
      <div class="relative max-w-6xl mx-auto px-6 pt-40 pb-24 text-center">
        <Eyebrow class="mb-5">Local AI · Ryzen AI</Eyebrow>
        <h1 class="text-4xl sm:text-6xl font-bold tracking-tight leading-[1.08]">
          AI that runs<br />on your machine.
        </h1>
        <p class="mt-5 text-lg text-g-muted max-w-xl mx-auto">
          Open-source agents that work entirely on your hardware. Private by default. No API keys. No cloud.
        </p>
        <div class="mt-9 flex items-center justify-center gap-3">
          <a href="https://amd-gaia.ai/docs" class="rounded-md bg-g-gold px-6 py-3 text-sm font-semibold text-black hover:brightness-110 transition">
            Get started
          </a>
          <a href="/hub" class="rounded-md border border-g-border px-6 py-3 text-sm font-semibold text-g-text hover:border-g-gold/50 transition-colors">
            Browse agents
          </a>
        </div>
      </div>
    </section>

    <!-- 2. Living terminal -->
    <section class="max-w-2xl mx-auto px-6 -mt-12 relative z-10">
      <TerminalBlock lines={demoLines} typing />
    </section>

    <!-- 3. Three pillars -->
    <section class="max-w-5xl mx-auto px-6 py-24 grid sm:grid-cols-3 gap-10 text-center">
      <div>
        <h2 class="text-base font-semibold">Private by default</h2>
        <p class="mt-2 text-sm text-g-muted">Your documents and prompts never leave your machine.</p>
      </div>
      <div>
        <h2 class="text-base font-semibold">Accelerated by Ryzen AI</h2>
        <p class="mt-2 text-sm text-g-muted">NPU and GPU acceleration on AMD hardware, out of the box.</p>
      </div>
      <div>
        <h2 class="text-base font-semibold">Open source</h2>
        <p class="mt-2 text-sm text-g-muted">MIT-licensed, built in the open with the community.</p>
      </div>
    </section>

    <!-- 4. Featured agents -->
    <section class="max-w-6xl mx-auto px-6 pb-28">
      <div class="flex items-baseline justify-between mb-6">
        <Eyebrow>Agent Hub</Eyebrow>
        <a href="/hub" class="text-sm text-g-gold-text hover:underline">All agents →</a>
      </div>
      <div class="grid sm:grid-cols-3 gap-5">
        {featured.map((agent) => <FeaturedCard agent={agent} eyebrow={agent.category} />)}
      </div>
    </section>

  </main>
  <Footer />
</Layout>
```

- [ ] **Step 2: Build + content assertions**

Run:
```bash
cd website && npm run build
grep -c 'g-terminal' dist/index.html        # expect ≥ 1
grep -c 'AI that runs' dist/index.html      # expect ≥ 1
```
Expected: build completes; both greps ≥ 1

- [ ] **Step 3: Commit**

```bash
git add website/src/pages/index.astro
git commit -m "feat(website): homepage rebuilt — aurora hero, living terminal, five beats"
```

---

### Task 6: Hub catalog rebuild

**Files:**
- Modify: `website/src/pages/hub/index.astro` (full rewrite of markup; adapt the existing `<script is:inline>` filter to the new selectors)
- Delete: `website/src/components/AgentCard.astro` (after last usage is gone)

- [ ] **Step 1: Rewrite `hub/index.astro`**

Structure (frontmatter keeps `getCatalog()` + `distinct()` from `../../data/catalog`):

```astro
---
import Layout from '../../layouts/Layout.astro';
import Header from '../../components/Header.astro';
import Footer from '../../components/Footer.astro';
import Eyebrow from '../../components/Eyebrow.astro';
import FeaturedCard from '../../components/FeaturedCard.astro';
import AgentRow from '../../components/AgentRow.astro';
import { getCatalog, categoryLabel, distinct } from '../../data/catalog';

const agents = await getCatalog();
// Editorial pick: first non-deprecated verified agent, else first agent.
const spotlight = agents.find((a) => !a.deprecated && a.security_tier === 'verified') ?? agents[0];
const categories = distinct(agents, 'category');
---

<Layout title="Agent Hub — gaia" description="Browse and install local AI agents for GAIA.">
  <Header />
  <main id="main-content" class="max-w-4xl mx-auto px-6 pt-32 pb-28">

    <Eyebrow class="mb-3">Agent Hub</Eyebrow>
    <h1 class="text-3xl sm:text-4xl font-bold tracking-tight">Agents for your machine.</h1>
    <p class="mt-3 text-g-muted">Install with one command — or open directly in GAIA.</p>

    <!-- Featured spotlight -->
    <div class="mt-10">
      <FeaturedCard agent={spotlight} eyebrow="Featured" />
    </div>

    <!-- Filters -->
    <div class="mt-12 flex flex-wrap items-center gap-2">
      <button type="button" data-filter="all" class="hub-pill hub-pill-active">All</button>
      {categories.map((c) => (
        <button type="button" data-filter={c} class="hub-pill">{categoryLabel(c)}</button>
      ))}
      <input
        id="hub-search"
        type="search"
        placeholder="Search agents…"
        class="ml-auto w-full sm:w-56 rounded-md border border-g-border bg-g-surface px-3 py-1.5 text-sm text-g-text placeholder:text-g-muted focus:border-g-gold/60 focus:outline-none"
      />
    </div>

    <!-- Rows -->
    <div id="hub-list" class="mt-6 rounded-xl border border-g-border overflow-hidden">
      {agents.map((agent) => <AgentRow agent={agent} />)}
    </div>
    <p id="hub-empty" class="hidden mt-8 text-center text-sm text-g-muted">No agents match your search.</p>

  </main>
  <Footer />
</Layout>

<style>
  .hub-pill {
    @apply rounded-full border border-g-border px-3.5 py-1.5 text-[13px] text-g-muted hover:text-g-text transition-colors;
  }
  .hub-pill-active {
    @apply bg-g-gold text-black border-g-gold font-semibold hover:text-black;
  }
</style>

<script is:inline>
  const rows = Array.from(document.querySelectorAll('.agent-row'));
  const pills = Array.from(document.querySelectorAll('[data-filter]'));
  const search = document.getElementById('hub-search');
  const empty = document.getElementById('hub-empty');
  let category = 'all';

  function apply() {
    const q = (search.value || '').trim().toLowerCase();
    let visible = 0;
    rows.forEach((row) => {
      const matchesCat = category === 'all' || row.dataset.category === category;
      const matchesQ =
        !q ||
        row.dataset.name.includes(q) ||
        row.dataset.description.includes(q) ||
        row.dataset.tags.includes(q);
      const show = matchesCat && matchesQ;
      row.classList.toggle('hidden', !show);
      if (show) visible++;
    });
    empty.classList.toggle('hidden', visible > 0);
  }

  pills.forEach((pill) =>
    pill.addEventListener('click', () => {
      category = pill.dataset.filter;
      pills.forEach((p) => p.classList.toggle('hub-pill-active', p === pill));
      apply();
    })
  );
  search.addEventListener('input', apply);
</script>
```

- [ ] **Step 2: Delete the now-unused card component**

```bash
grep -rn "AgentCard" website/src && echo "STILL USED — do not delete" || git rm website/src/components/AgentCard.astro
```
Expected: no remaining references; file removed.

- [ ] **Step 3: Build + assertions**

Run:
```bash
cd website && npm run build
grep -o 'class="agent-row' dist/hub/index.html | wc -l   # expect = agent count (11 live / fixture count)
grep -c 'Featured' dist/hub/index.html                    # expect ≥ 1
```

- [ ] **Step 4: Commit**

```bash
git add -A website/src/pages/hub/index.astro website/src/components/AgentCard.astro
git commit -m "feat(website): hub catalog — featured spotlight, pill filters, clean rows"
```

---

### Task 7: Agent detail rebuild

**Files:**
- Modify: `website/src/pages/hub/[id].astro` (rewrite markup; keep frontmatter data plumbing: `getStaticPaths` over `getCatalog()`, `renderMarkdown(agent.readme)`, requirement rows, `gaia://hub/install/${agent.id}` deep link)

- [ ] **Step 1: Rewrite `[id].astro`** body. Frontmatter additions/keeps:

```astro
---
import Layout from '../../layouts/Layout.astro';
import Header from '../../components/Header.astro';
import Footer from '../../components/Footer.astro';
import Eyebrow from '../../components/Eyebrow.astro';
import AgentIcon from '../../components/AgentIcon.astro';
import InstallCommand from '../../components/InstallCommand.astro';
import TerminalBlock from '../../components/TerminalBlock.astro';
import {
  getCatalog, getAgent, categoryLabel, languageLabel,
  securityTierLabel, formatBytes, platformLabel, type Agent,
} from '../../data/catalog';
import { renderMarkdown } from '../../data/markdown';

export async function getStaticPaths() {
  const agents = await getCatalog();
  return agents.map((a) => ({ params: { id: a.id } }));
}

const { id } = Astro.params;
const agent = (await getAgent(id!))!;
const installCmd = `gaia agent install ${agent.id}`;
const deepLink = `gaia://hub/install/${agent.id}`;
const readmeHtml = renderMarkdown(agent.readme);
const terminalLines = [
  { prompt: true, text: installCmd },
  { muted: true, text: `✓ ${agent.name} ${agent.latest_version} installed` },
  { prompt: true, text: `gaia ${agent.id}` },
];
---
```

Page structure:

```astro
<Layout title={`${agent.name} — Agent Hub — gaia`} description={agent.description}>
  <Header />
  <main id="main-content" class="max-w-5xl mx-auto px-6 pt-32 pb-28">

    <a href="/hub" class="text-sm text-g-muted hover:text-g-text transition-colors">← Agent Hub</a>

    <!-- Header -->
    <header class="mt-8 flex flex-col sm:flex-row sm:items-start gap-6">
      <span class="flex-none w-16 h-16 rounded-xl border border-g-border bg-g-surface flex items-center justify-center text-g-gold-text">
        <AgentIcon name={agent.icon} class="w-8 h-8" />
      </span>
      <div class="min-w-0 flex-1">
        <div class="flex flex-wrap items-center gap-3">
          <h1 class="text-3xl font-bold tracking-tight">{agent.name}</h1>
          <span class="text-[10px] font-bold tracking-wide uppercase text-g-gold-text border border-g-gold/40 rounded px-2 py-0.5">
            {securityTierLabel(agent.security_tier)}
          </span>
        </div>
        <p class="mt-2 text-g-muted max-w-2xl">{agent.description}</p>
        {agent.deprecated && (
          <p class="mt-3 text-sm text-g-muted border border-g-border rounded-md px-3 py-2">
            Deprecated{agent.deprecation_message ? ` — ${agent.deprecation_message}` : ''}
          </p>
        )}
        <div class="mt-6 flex flex-wrap items-center gap-3">
          <a href={deepLink} class="rounded-md bg-g-gold px-6 py-3 text-sm font-semibold text-black hover:brightness-110 transition">
            Open in GAIA
          </a>
          <InstallCommand command={installCmd} />
        </div>
        <p class="mt-2 text-xs text-g-muted">
          Don't have GAIA yet? <a href="https://amd-gaia.ai/docs" class="text-g-gold-text hover:underline">Get started →</a>
        </p>
      </div>
    </header>

    <!-- Body: README + sidebar -->
    <div class="mt-14 grid lg:grid-cols-[minmax(0,1fr)_280px] gap-12">
      <article class="readme min-w-0 max-w-[70ch]">
        <h2 class="sr-only">About {agent.name}</h2>
        <div set:html={readmeHtml} />
        <div class="mt-10">
          <TerminalBlock lines={terminalLines} typing />
        </div>
      </article>

      <aside class="space-y-8">
        <section>
          <Eyebrow class="mb-3">Details</Eyebrow>
          <dl class="text-[13px]">
            <!-- one row pattern, repeated for each pair -->
            <div class="flex justify-between py-2 border-b border-g-border">
              <dt class="text-g-muted">Version</dt><dd class="font-mono">{agent.latest_version}</dd>
            </div>
            <div class="flex justify-between py-2 border-b border-g-border">
              <dt class="text-g-muted">Size</dt><dd class="font-mono">{formatBytes(agent.download_size_bytes)}</dd>
            </div>
            <div class="flex justify-between py-2 border-b border-g-border">
              <dt class="text-g-muted">Author</dt><dd>{agent.author}</dd>
            </div>
            <div class="flex justify-between py-2 border-b border-g-border">
              <dt class="text-g-muted">Language</dt><dd>{languageLabel(agent.language)}</dd>
            </div>
            <div class="flex justify-between py-2 border-b border-g-border">
              <dt class="text-g-muted">Category</dt><dd>{categoryLabel(agent.category)}</dd>
            </div>
            <div class="flex justify-between py-2 border-b border-g-border">
              <dt class="text-g-muted">Min GAIA</dt><dd class="font-mono">{agent.min_gaia_version || '—'}</dd>
            </div>
          </dl>
        </section>

        <section>
          <Eyebrow class="mb-3">Requirements</Eyebrow>
          <dl class="text-[13px]">
            <div class="flex justify-between py-2 border-b border-g-border">
              <dt class="text-g-muted">Memory</dt><dd class="font-mono">{agent.requirements.min_memory_gb} GB</dd>
            </div>
            <div class="flex justify-between py-2 border-b border-g-border">
              <dt class="text-g-muted">NPU</dt><dd>{agent.requirements.npu}</dd>
            </div>
          </dl>
          <div class="mt-3 flex flex-wrap gap-1.5">
            {agent.requirements.platforms.map((p) => (
              <span class="text-[11px] text-g-muted border border-g-border rounded px-2 py-0.5">{platformLabel(p)}</span>
            ))}
          </div>
        </section>

        {agent.models.length > 0 && (
          <section>
            <Eyebrow class="mb-3">Models</Eyebrow>
            <ul class="text-[12px] font-mono text-g-muted space-y-1.5">
              {agent.models.map((m) => <li>{m}</li>)}
            </ul>
          </section>
        )}

        {agent.permissions.length > 0 && (
          <section>
            <Eyebrow class="mb-3">Permissions</Eyebrow>
            <ul class="text-[12px] font-mono text-g-muted space-y-1.5">
              {agent.permissions.map((p) => <li>{p}</li>)}
            </ul>
          </section>
        )}
      </aside>
    </div>

  </main>
  <Footer />
</Layout>

<style is:global>
  /* README typography on tokens (replaces the old .readme styles) */
  .readme h1, .readme h2, .readme h3 { @apply font-semibold tracking-tight mt-8 mb-3; }
  .readme h1 { @apply text-2xl; }
  .readme h2 { @apply text-xl; }
  .readme h3 { @apply text-base; }
  .readme p { @apply text-[14.5px] leading-relaxed text-g-muted my-3; }
  .readme a { @apply text-g-gold-text hover:underline; }
  .readme code { @apply font-mono text-[12.5px] bg-g-surface border border-g-border rounded px-1 py-0.5; }
  .readme pre { @apply bg-[#0c0c0e] text-[#e8e8e6] border border-g-border rounded-lg p-4 overflow-x-auto my-4; }
  .readme pre code { @apply bg-transparent border-0 p-0 text-[12.5px]; }
  .readme ul { @apply list-disc pl-5 my-3 text-[14.5px] text-g-muted space-y-1; }
  .readme blockquote { @apply border-l-2 border-g-gold/50 pl-4 text-g-muted italic my-4; }
</style>
```

- [ ] **Step 2: Build + assertions**

Run:
```bash
cd website && npm run build
grep -c 'Open in GAIA' dist/hub/summarize/index.html   # expect ≥ 1 (fixture mode: use an id from src/data/index.json)
grep -c 'gaia://hub/install/' dist/hub/summarize/index.html  # expect ≥ 1
```

- [ ] **Step 3: Commit**

```bash
git add 'website/src/pages/hub/[id].astro'
git commit -m "feat(website): agent detail — Open in GAIA primary CTA, readable README, quiet sidebar"
```

---

### Task 8: Full verification (both catalog modes, both themes) + token purity

**Files:** none (verification only) — plus any fixes it surfaces.

- [ ] **Step 1: Token purity greps** — no legacy palette anywhere in `website/src`:

```bash
cd website
grep -rn "gaia-bg\|gaia-card\|gaia-accent\|gaia-text\|gaia-muted\|gaia-border" src/ && echo "FAIL: legacy tokens remain" || echo "OK"
grep -rni "#ED1C24\|#ff3d44" src/ && echo "FAIL: red remains" || echo "OK"
```
Expected: both `OK`. Fix any hits (including `src/data/catalog.ts`'s `securityTierClasses` helper — restyle its class strings to `g-*` tokens or remove it if no longer referenced).

- [ ] **Step 2: Fixture-mode build**

```bash
cd website && npm run build
```
Expected: `[catalog] HUB_CATALOG_URL not set — using the bundled fixture catalog`, `[build] Complete!`

- [ ] **Step 3: Live-mode build against the local stack** (worker on :8791 with the 11 published agents; restart it first if not running — see `workers/agent-hub/README.md` local dev section):

```bash
curl -sf http://localhost:8791/health   # must return ok; if not, restart the worker stack first
cd website && HUB_CATALOG_URL=http://localhost:8791 npm run build
grep -o 'class="agent-row' dist/hub/index.html | wc -l   # expect 11
```

- [ ] **Step 4: Serve + visual pass in BOTH themes**

```bash
cd website && npx serve dist -l 8792
```
Check `http://localhost:8792/`, `/hub`, `/hub/summarize`, `/hub/blender` — in dark and light (toggle), confirm: no flash of wrong theme on reload, aurora drift animates, terminal types on scroll, copy pulses gold, rows hover-reveal "Open in GAIA ›". Screenshot each page in both themes for the user.

- [ ] **Step 5: Reduced-motion check** — in devtools, emulate `prefers-reduced-motion: reduce`; reload: terminal renders full text instantly, no drift.

- [ ] **Step 6: Commit any fixes; final commit**

```bash
git add -A website/src
git commit -m "fix(website): verification fixes — token purity, both catalog modes"
```

---

### Task 9: Docs site (Mintlify) brand alignment + cross-links

**Files:**
- Modify: `docs/docs.json` (theme colors, fonts, appearance, navbar link)

The docs site must feel like the same product: gold instead of red, Inter/JetBrains Mono, light+dark. Mintlify can't reproduce aurora effects — palette/type/tone alignment is the goal here; deeper custom CSS is future work.

- [ ] **Step 1: Update `docs/docs.json` theme keys** (leave navigation untouched):

```json
{
  "colors": {
    "primary": "#A87B2D",
    "light": "#E2A33E",
    "dark": "#E2A33E"
  },
  "appearance": {
    "default": "system"
  },
  "fonts": {
    "heading": { "family": "Inter" },
    "body": { "family": "Inter" }
  }
}
```

(`colors.primary` is used on light backgrounds → the AA-contrast gold `#A87B2D`; `light`/`dark` variants are used on dark backgrounds → graphic gold `#E2A33E`. Verify the exact font config key shape against the Mintlify schema — `npx mint dev` will reject invalid keys.)

- [ ] **Step 2: Fix the cross-links so the surfaces interrelate**

In `docs/docs.json` navbar: change `"Main Site"` href from `https://gaia.amd.com` to `https://amd-gaia.ai` (the Astro site's canonical domain per `website/astro.config.mjs`).
In `website/src/components/Header.astro` and the homepage Get-started CTA: confirm the Docs links point at the live docs URL (`https://amd-gaia.ai/docs`) — already the case from Task 2/5; just verify.

- [ ] **Step 3: Validate the docs config**

Run: `cd docs && npx mint dev --port 3333` (Ctrl-C after it serves) — or `npx mint broken-links` if a full dev run is impractical.
Expected: config parses, site serves with gold accents in both modes. NEVER use port 4001.

- [ ] **Step 4: Commit**

```bash
git add docs/docs.json
git commit -m "feat(docs): align Mintlify theme with gold design system + fix main-site link"
```

---

## Self-review (done at plan time)

- **Spec coverage:** §1 tokens→Task 1; theming→Task 2; §2 recipes→Tasks 3–4; §3 homepage→Task 5; §4 hub→Task 6; §5 detail + §6 interrelation (Open in GAIA primary, deep link, row affordance)→Tasks 6–7; §9 verification→Task 8; docs-site brand alignment + cross-links→Task 9 (added per user). Phase-2/ISV items are spec future-work, intentionally absent here.
- **Type consistency:** `Agent` fields used (`icon`, `tags`, `security_tier`, `requirements.npu`, `permissions`, `models`, `deprecation_message`) all exist in `src/data/catalog.ts`. `TerminalBlock` `Line` shape used identically in Tasks 3, 5, 7.
- **Placeholder scan:** none; the only elided blocks are explicitly "keep existing unchanged" references (Layout head metadata, GitHub SVG path).
