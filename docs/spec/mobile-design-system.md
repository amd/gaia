# Mobile Design System — GAIA Agent UI

**Status:** Draft (target: `docs/spec/mobile-design-system.md`)
**Owners:** ui-ux-designer, frontend-developer
**Consumers:** Issues #893 (M1 tunnel), #895 (M2 mobile-responsive), #896 (M3 mobile voice), #898 (M5 PWA)
**Repo paths referenced:** `src/gaia/apps/webui/`

---

## 1. Purpose & non-goals

The Agent UI today is a desktop-first React/Vite/Electron app
(`src/gaia/apps/webui/`). With Issue #893 (M1) it becomes reachable from
a phone via tunnel. M2/M3/M5 turn that reachable surface into a
*usable* mobile surface.

This document is the single source of truth for **how GAIA looks and
behaves on a phone**. Anything inconsistent with this doc in code or
in other specs is a bug against this doc.

**In-scope:** breakpoints, navigation, modals, touch targets, voice
button placement, safe areas/keyboard, accessibility, the new
`components/mobile/` inventory, agent consumption guidance.

**Out of scope:** desktop-only redesigns, new agents, backend API
changes (ASR codec list lives in #896, not here).

---

## 2. Stack reality (read this before designing)

The current `src/gaia/apps/webui/` is **not** Tailwind. It uses:

- **Vanilla CSS** with CSS custom properties in
  `src/gaia/apps/webui/src/styles/index.css` (palette, radii, fonts,
  durations all live there as `--bg-primary`, `--radius-md`,
  `--ease`, etc.).
- **Per-component CSS files** (`Sidebar.css`, `ChatView.css`, …)
  imported next to the `.tsx`.
- **Lucide-react** for icons (`lucide-react` 0.312).
- **Zustand** for state (`stores/chatStore.ts`).
- **No CSS-in-JS, no Tailwind, no PostCSS plugins** beyond Vite's
  defaults (see `vite.config.ts`).

Implication: this spec defines tokens as **CSS variables and
breakpoint media-query constants**, not Tailwind theme keys. If the
team later adopts Tailwind, the same values port directly.

---

## 3. Breakpoints

| Token | Width | Mode | Notes |
|-------|-------|------|-------|
| `--bp-mobile-max` | `≤ 768px` | mobile | Phones in portrait + small landscape |
| `--bp-tablet-min` / `--bp-tablet-max` | `769–1023px` | tablet | Treated as desktop layout, slightly tightened |
| `--bp-desktop-min` | `≥ 1024px` | desktop | Current behavior |

**Decision: `768px` is the mobile/desktop boundary.** This matches the
existing logic already in production:

- `src/gaia/apps/webui/src/App.tsx` lines 309–317, 332, 372, 449, 452
  all use `window.innerWidth <= 768` / `> 768`.

Changing the boundary would invalidate that logic in five places for
no benefit; pinning 768 keeps M2 a CSS/refactor task, not a behavior
rewrite.

**Implementation:**

```css
/* src/gaia/apps/webui/src/styles/index.css — add to :root */
:root {
  --bp-mobile-max: 768px;
}

/* Use as: */
@media (max-width: 768px) { /* mobile-only */ }
@media (min-width: 769px) { /* desktop+ */ }
```

A matching TS constant lives at
`src/gaia/apps/webui/src/utils/breakpoints.ts`:

```ts
export const MOBILE_MAX_PX = 768;
export const isMobileViewport = () => window.innerWidth <= MOBILE_MAX_PX;
```

All inline `window.innerWidth <= 768` checks in `App.tsx` migrate to
`isMobileViewport()` as part of M2.

---

## 4. Navigation pattern

**Decision: slide-out drawer (left), not bottom-tab.**

The Agent UI today already implements a slide-out left sidebar with an
overlay (`App.tsx` lines 480–502, `Sidebar.css`). On desktop the
sidebar is persistent; on mobile it overlays the chat with a backdrop.
M2 hardens that pattern — it does not replace it.

**Why drawer over bottom-tab:**

| Criterion | Drawer (chosen) | Bottom-tab |
|-----------|-----------------|------------|
| Reuses existing `Sidebar` + `sidebar-overlay` | yes | no — full rewrite |
| Holds N sessions (sidebar's job) | yes — natural list | no — tabs are for ≤5 modes |
| Matches desktop mental model | yes | no — context switch on resize |
| Voice button real estate | unblocked | competes with tabs |
| Accessibility for screen-reader | drawer is a single landmark | tabs need `tablist` semantics |

**Documented alternative (bottom-tab) and why it loses:** A bottom-tab
bar (Chats / Documents / Settings) is conventional for consumer apps,
but GAIA's primary navigational unit is the *session list* — variable
length, frequently reordered. Tabs would flatten that into a
secondary screen and force a duplicate "session list" screen anyway.
We may revisit if/when GAIA grows >3 top-level modes; track in #895
follow-up.

**Drawer behavior on mobile (M2):**

- Closed by default on first paint when `isMobileViewport()` is true.
- Hamburger button (existing `.sidebar-toggle`) is the only entry
  point. Touch target 44×44 minimum (currently 18px icon — needs
  padding bump in M2).
- Backdrop tap closes (already wired).
- Swipe-from-left-edge to open is a P2 enhancement, not required for
  M2.
- `Esc` closes (focus moves back to hamburger).
- When drawer is open, body content gets `inert` attribute and
  `aria-hidden="true"`.

---

## 5. Modal & panel patterns

Today the app has three right-hand overlays: `DocumentLibrary`,
`FileBrowser`, `SettingsModal`, plus `MobileAccessModal`. They mount
through `<AnimatedPresence>` (`App.tsx` lines 22–52, 520–539).

**Pattern:** one reusable `<Panel>` component pair under
`components/mobile/`:

| Component | Desktop behavior | Mobile behavior |
|-----------|------------------|-----------------|
| `Panel` | Floating right-side card (current) | Full-screen modal, slides up from bottom |
| `PanelHeader` | Title + close (×) | Title + close (×) + drag-handle indicator |
| `PanelBody` | Scroll within panel | Scroll within panel, respects keyboard inset |
| `PanelFooter` | Right-aligned actions | Sticky-bottom actions, full-width primary |

**Behavioral contract:**

- On `isMobileViewport()`, `Panel` ignores any `width`/`maxWidth`
  prop and renders `position: fixed; inset: 0`.
- The exit animation differs by viewport: desktop slides right →
  off-screen; mobile slides down → off-screen. Both use
  `--ease`/`--duration` from `:root`.
- Closing returns focus to the trigger element (current code does
  this inconsistently — fix in M2).
- Only one full-screen `Panel` may be open on mobile at a time;
  opening a second closes the first.

**Migration plan for existing modals (M2 work):**

| Existing | Becomes |
|----------|---------|
| `SettingsModal` | `<Panel title="Settings">…` |
| `DocumentLibrary` | `<Panel title="Documents" wide>…` |
| `FileBrowser` | `<Panel title="Files" wide>…` |
| `MobileAccessModal` | Stays desktop-only (renders only when `!isMobile`, see `App.tsx:531`) |

---

## 6. Touch targets, buttons, hierarchy

### Minimums

- **44×44 CSS pixels** for any interactive element on mobile (Apple
  HIG; Android Material recommends 48dp — 44 is the floor, prefer
  48 where space permits).
- **8px minimum spacing** between adjacent touch targets to prevent
  mis-taps.
- Hover styles (`:hover`) must not be the *only* affordance — pair
  with `:active` and visible focus.

### Audit list (current violations to fix in M2)

| Element | Current | Fix |
|---------|---------|-----|
| `.sidebar-toggle` (hamburger) | 18px icon, padding TBD | 44×44 hit area, icon stays 18px |
| Session row delete (trash) icon | small, hover-only | always-visible on touch, 44×44 |
| Send button in `ChatView` input | size TBD | 44×44, primary color |
| Settings/theme/sun/moon row | tightly packed | space-out to 8px gaps |
| `MessageBubble` action icons | small, hover-only | swipe-to-reveal or tap-to-reveal action sheet |

### Hierarchy

- **Primary CTA** — solid `--amd-red` background, white icon/text.
  At most one per screen region.
- **Secondary** — outlined or `--bg-tertiary` fill.
- **Tertiary** — text-only, used for "Cancel" and link-like actions.
- **Destructive** — `--accent-danger` outline; confirmation required
  on mobile (no hover affordance to undo a slip).

Primary CTA placement on mobile: **bottom of the viewport, full
width when in a sticky footer; otherwise right-aligned in the input
row** (matches the Send button position).

---

## 7. Voice button (M3 dependency)

**Single canonical location:** the bottom-right of the chat input row,
adjacent to the Send button.

```
┌───────────────────────────────────────────┐
│  [+] [Type a message……………………]  [Mic] [▶]  │
└───────────────────────────────────────────┘
        attach     textarea           voice send
```

- **Touch target:** 44×44 minimum, icon 20px (lucide `Mic`).
- **States:**
  - idle — outline, `--text-secondary`
  - listening — filled `--accent-green`, pulsing ring
    (respect `prefers-reduced-motion`)
  - processing — spinner overlay, `aria-busy="true"`
  - error — `--accent-danger` outline + tooltip with reason
- **Default interaction on mobile: push-to-talk** (long-press to
  start, release to send). Tap-to-toggle is a settings opt-in for
  hands-free use. Rationale: PTT is unambiguous on a touchscreen and
  prevents accidental hot-mic when phone is in pocket.
- **Default on desktop:** click to toggle (PTT requires holding the
  mouse, which is poor ergonomics).
- **Live transcript** appears above the input row in a dismissible
  chip while listening, so users see what Whisper heard before send.
- **Codec/MIME negotiation, permission UX, ASR endpoint** — all in
  spec #896.

---

## 8. Safe areas & virtual keyboard

### Safe-area insets

iOS notch/home-bar and Android gesture bar require:

```css
:root {
  --safe-top: env(safe-area-inset-top, 0px);
  --safe-right: env(safe-area-inset-right, 0px);
  --safe-bottom: env(safe-area-inset-bottom, 0px);
  --safe-left: env(safe-area-inset-left, 0px);
}

/* Add to <meta name="viewport"> in index.html: */
<meta name="viewport"
      content="width=device-width, initial-scale=1.0, viewport-fit=cover">
```

The current `index.html` viewport tag is missing `viewport-fit=cover`
— **M2 must add it** (one-line change).

Apply insets to:

- Sidebar drawer top/bottom padding.
- Chat input row `padding-bottom`.
- Sticky panel footers.
- Bottom of `MessageBubble` list.

### Virtual keyboard

Use the **Visual Viewport API** to keep the input row visible above the
on-screen keyboard:

```ts
// src/gaia/apps/webui/src/hooks/useViewportHeight.ts (new in M2)
export function useViewportHeight() {
  const [h, setH] = useState(window.visualViewport?.height ?? window.innerHeight);
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;
    const onResize = () => setH(vv.height);
    vv.addEventListener('resize', onResize);
    vv.addEventListener('scroll', onResize);
    return () => {
      vv.removeEventListener('resize', onResize);
      vv.removeEventListener('scroll', onResize);
    };
  }, []);
  return h;
}
```

Then on mobile, the chat input is `position: sticky; bottom: 0` inside a
container whose height tracks `useViewportHeight()`. iOS Safari does
not auto-scroll input into view reliably — the hook fixes that.

---

## 9. Accessibility minimums

WCAG 2.1 AA is the floor; practical checklist:

- [ ] All interactive elements reachable with `Tab` in a logical order.
- [ ] Visible focus ring on every focusable element in **both** light
      and dark themes (current dark theme focus ring is faint —
      strengthen in M2).
- [ ] Color contrast ≥ 4.5:1 text, ≥ 3:1 large text (≥18.66px). The
      `--text-muted` token in dark mode (`#8585a0` on `#0e0e16`) is
      noted in `index.css:134` as "WCAG AA compliant (5.1:1)" — keep
      that level when adding mobile-specific text.
- [ ] `aria-label` on every icon-only button. The hamburger button
      (`App.tsx:484`) already does this — extend to all
      `lucide-react` icon buttons.
- [ ] `role="alert"` for the connection banner and create-error toast
      (toast already does this, `App.tsx:550`).
- [ ] Screen-reader live region for streaming assistant content
      (`aria-live="polite"`, `aria-atomic="false"`). M2 must add this
      to `MessageBubble` for the streaming message only.
- [ ] Honor `@media (prefers-reduced-motion: reduce)` — disable the
      grain texture (`index.css:187`), the typing pulse, and any
      slide animation duration > 100ms.
- [ ] Works at 200% zoom without horizontal scroll (test with
      Playwright #883).

**CI gate:** add `axe-core` accessibility check to the mobile
Playwright suite (#883). Fail the build on any AA violation.

---

## 10. Component inventory — `components/mobile/`

New shared components live at
`src/gaia/apps/webui/src/components/mobile/`. Each has its own
`.css` peer file, matching existing convention.

```
components/mobile/
├── Panel.tsx              // see §5
├── Panel.css
├── BottomSheet.tsx        // for action sheets, confirmations
├── BottomSheet.css
├── MobileHeader.tsx       // hamburger + title + right action slot
├── MobileHeader.css
├── TouchTarget.tsx        // wrapper enforcing 44x44
├── TouchTarget.css
├── VoiceButton.tsx        // see §7 + #896
├── VoiceButton.css
└── index.ts               // barrel
```

### Prop interfaces

```ts
interface PanelProps {
  open: boolean;
  onClose: () => void;
  title: string;
  /** desktop-only: makes the floating panel wider (used by DocumentLibrary, FileBrowser) */
  wide?: boolean;
  /** if true, render with no padding so embedded content owns the layout */
  flush?: boolean;
  children: React.ReactNode;
}

interface BottomSheetProps {
  open: boolean;
  onClose: () => void;
  /** swipe-down-to-dismiss, default true on mobile, ignored on desktop */
  dismissible?: boolean;
  children: React.ReactNode;
}

interface MobileHeaderProps {
  title: string;
  onMenuClick: () => void;
  rightSlot?: React.ReactNode;
}

interface TouchTargetProps {
  as?: 'button' | 'a' | 'div';
  size?: 44 | 48;       // CSS pixels, default 44
  ariaLabel: string;     // required — no icon-only without label
  onClick?: () => void;
  children: React.ReactNode;
}

interface VoiceButtonProps {
  /** uncontrolled: component manages PTT state. Controlled override for tests. */
  mode?: 'ptt' | 'toggle';
  onTranscript: (text: string, isFinal: boolean) => void;
  onError?: (err: VoiceError) => void;
  disabled?: boolean;
}
```

`TouchTarget` is the single "make this tappable" primitive — enforces
the 44px floor and the `aria-label` requirement at the type level.
Refactoring existing icon buttons through it is in scope for M2.

---

## 11. Storybook (recommendation)

**Recommended but not required for M2.** Adding Storybook 8 to the
webui (`npm i -D @storybook/react-vite`) gives:

- A render target each `mobile/*` component can be reviewed in
  without spinning up the backend.
- A way for ui-ux-designer to validate against the spec without a
  Lemonade Server running.
- Visual-regression hooks (Chromatic / Loki) for the mobile audit.

If the cost is too high for v0.20.x, defer Storybook to v0.21 and
have agents build a `MobilePreview` page at
`/preview/mobile` (Vite route) that renders each component in
isolation. Either path satisfies "agents need a render target".

---

## 12. How to consume this spec

| Issue | What to read | What to deliver |
|-------|--------------|-----------------|
| #893 (M1, tunnel) | §6 (touch targets), §9 (a11y) | Tunnel UX warnings sized for thumbs; QR modal accessible |
| #895 (M2, responsive) | All sections — this is the implementation issue | `components/mobile/` directory, breakpoint refactor, modal-to-panel migration, touch-target audit fixes |
| #896 (M3, voice) | §7 voice button, §6 button hierarchy, §9 a11y | `VoiceButton` component, codec/permission/PTT logic |
| #898 (M5, PWA) | §8 safe areas, §3 breakpoints | `manifest.webmanifest`, iOS install hints, viewport-fit |

**Rule of thumb for any agent touching mobile UI:** if you find
yourself adding `@media (max-width: …)` with a number other than
`768px`, or a touch target smaller than 44×44, **stop and update
this spec first** with rationale. Otherwise drift wins and the next
agent has a worse starting point.

---

## 13. Open questions

- **Tablet layout (769–1023px):** treat as desktop for v0.20.x; revisit
  if telemetry shows >5% tablet usage.
- **Landscape phone:** input row collapses to single line, voice
  button stays bottom-right. Confirmed by #895 acceptance.
- **PWA install prompt (M5):** lives in #898, not here.
- **Right-to-left languages:** out of scope for v0.20.x; add to
  v0.21 backlog.

---

## 14. References

- Existing app entry: `src/gaia/apps/webui/src/App.tsx`
- Existing styles: `src/gaia/apps/webui/src/styles/index.css`
- Existing sidebar: `src/gaia/apps/webui/src/components/Sidebar.tsx`
- Existing chat view: `src/gaia/apps/webui/src/components/ChatView.tsx`
- Apple HIG, Touch Targets:
  https://developer.apple.com/design/human-interface-guidelines/buttons
- Material 3 Touch Target Sizing:
  https://m3.material.io/foundations/accessible-design/accessibility-basics
- WCAG 2.1 AA: https://www.w3.org/WAI/WCAG21/quickref/?levels=aa
- Visual Viewport API:
  https://developer.mozilla.org/en-US/docs/Web/API/Visual_Viewport_API
