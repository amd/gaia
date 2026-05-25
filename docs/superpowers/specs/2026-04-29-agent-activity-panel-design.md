# Agent Activity Panel — Design

**Date:** 2026-04-29
**Owner:** Kalin Ovtcharov
**Branch:** `feature/agent-memory`
**Status:** Draft, pending implementation plan

## Goal

Add a **right-hand side, resizable, real-time Agent Activity panel** as the primary live-monitoring surface for impactful agent operations during a conversation. Memory operations are one category among several. The existing Memory Dashboard modal is **demoted to a deep-management view** (filtering, CRUD, profile setup, maintenance), launched from the activity panel — not replaced.

## Non-goals

- **No new backend SSE channel for v1.** The existing per-message agent step stream covers in-conversation activity. A dedicated `/api/activity/stream` for background (non-chat) agent activity is future work.
- **No replacement of the Memory Dashboard modal.** The modal keeps all current functionality and remains the place for filtering, CRUD, profile setup, and maintenance.
- **No raw debug trace.** Thinking text, internal status pings, and empty recalls do not appear in the feed. This is an audit log of impactful actions, not a step-by-step debug view.
- **No cross-session aggregation in v1.** Feed is scoped to the currently open session.
- **No goal events in the feed for v1.** Goals are created/advanced via REST endpoints, not the chat SSE stream — wiring them in would require a second event source. Goals stay accessible in the Memory Dashboard's Goals tab. Future work.
- **No real-time for non-chat activity.** Background agents (autonomy engine, scheduled MCP, manual maintenance) emit no chat SSE; their activity will not appear live. The feed reflects what happens inside the active conversation.

## What counts as an "action"

The feed is curated. The filter applies at insertion time so noise never reaches the store.

### Include (signal)

| Category | Source | Rendered as |
|---|---|---|
| **Memory write** | Tool calls: `remember`, `forget`, `supersede` | "Remembered: '<content preview>'" with category chip |
| **Memory retrieval** | `recall` calls that returned ≥ 1 result | "Recalled N memories" with expandable list of titles |
| **File operation** | `file_io` tool calls: `write_file`, `edit_file`, `delete_file`; `read_file` only if returned > 0 lines | "Edited /path/to/file.py" with line-count delta |
| **Shell command** | `shell` tool execution (success or failure) | "Ran `command`" with truncated output preview |
| **External integration** | MCP tool calls, web fetches, RAG queries on indexed docs | "MCP: github.create_issue" / "Web: fetched URL" |
| **Error** | Any tool step with `success === false` | Red chip, "<tool> failed: <error>" |

### Exclude (noise)

- `type: 'thinking'` steps
- `type: 'status'` steps
- `type: 'plan'` steps (already shown inline in the message)
- `recall` calls that returned 0 results (no signal to surface)
- `read_file` calls that returned empty or were probes (heuristic: returned 0 lines)
- Internal sub-agent routing decisions
- Unknown tools (default-out — see Filtering implementation)

### Memory retrieval inclusion rule

For v1, a `recall` is shown if it returned ≥ 1 memory entry. We do **not** attempt to detect whether the assistant's response actually cited those memories — there is no first-class memory-citation token in the response format today, so any "cited" detection would be a brittle heuristic. The "≥ 1 result" rule is good enough: it filters speculative dead-end recalls (the noisy case) while keeping all retrievals that actually surfaced context.

First-class memory citations + a "this recall was actually used" signal are future work, tracked in Open Questions.

## Architecture

### Data flow

```
┌─────────────────┐    SSE    ┌──────────────────┐
│  ChatAgent      │──────────▶│  sse_handler.py  │
│  (emits steps)  │           │  (forwards step) │
└─────────────────┘           └────────┬─────────┘
                                       │
                                       ▼
                          ┌──────────────────────────┐
                          │  ChatView SSE consumer   │
                          │  (existing)              │
                          └────────┬─────────────────┘
                                   │
                ┌──────────────────┴───────────────────┐
                │                                      │
                ▼                                      ▼
   ┌──────────────────────┐              ┌──────────────────────┐
   │  MessageBubble       │              │  agentActivityStore  │
   │  inline AgentActivity│              │  (Zustand, NEW)      │
   │  (existing)          │              │  - filter at insert  │
   └──────────────────────┘              │  - per-session keyed │
                                         └──────────┬───────────┘
                                                    │
                                                    ▼
                                         ┌──────────────────────┐
                                         │  AgentActivityPanel  │
                                         │  (NEW component)     │
                                         └──────────────────────┘
```

The same `AgentStep` events that already feed the per-message inline `<AgentActivity>` component get a second consumer: a new `agentActivityStore` keyed by session ID. The panel subscribes to that store and re-renders on insert.

### Session-load rebuild

When the user opens a session (page load, sidebar click, URL navigation), `ChatView` already loads `messages[].agent_steps` from the backend. The panel must rebuild its action list from those existing steps so a session that ran activity yesterday does not show an empty panel today.

On session-load:

1. `agentActivityStore.replaceForSession(sessionId, steps[])` is called with all steps from all loaded messages, oldest-first.
2. Each step runs through `stepToAction`; nulls are dropped.
3. The resulting list is stored newest-first.
4. Subsequent live SSE steps are prepended via `appendAction` as usual.

The store therefore reflects: *historical actions from loaded messages + live actions from the active turn* — both filtered identically.

### Inline `<AgentActivity>` is unchanged

The existing per-message `<AgentActivity>` component inside `MessageBubble` keeps its current behavior (auto-expanded native tools, MCP tool filter bar, thinking-text consolidation). The new panel is **additive**: same source events, different lens — turn-local inline view vs. session-wide audit feed. Some duplication is expected and acceptable.

### Key components

| File | Status | Purpose |
|---|---|---|
| `src/gaia/apps/webui/src/stores/agentActivityStore.ts` | **NEW** | Zustand store: `{ [sessionId]: ActionEntry[] }`, with `appendAction(sessionId, step)` that applies the include/exclude filter |
| `src/gaia/apps/webui/src/components/AgentActivityPanel.tsx` | **NEW** | The right-side resizable panel itself |
| `src/gaia/apps/webui/src/components/AgentActivityPanel.css` | **NEW** | Panel-specific styles |
| `src/gaia/apps/webui/src/utils/activityFilter.ts` | **NEW** | Pure function: `stepToAction(step) → ActionEntry \| null` (returns null = filter out) |
| `src/gaia/apps/webui/src/components/ChatView.tsx` | **EDIT** | In the existing SSE step handler, also call `agentActivityStore.appendAction()` |
| `src/gaia/apps/webui/src/stores/chatStore.ts` | **EDIT** | Add `showActivityPanel: boolean`, `activityPanelWidth: number`, `setShowActivityPanel`, `setActivityPanelWidth` |
| `src/gaia/apps/webui/src/App.tsx` | **EDIT** | Render `<AgentActivityPanel>` next to `<MessageContent>`; adjust grid so chat shrinks when panel is open |
| `src/gaia/apps/webui/src/components/Sidebar.tsx` | **EDIT** | Existing memory `<Brain>` button: at desktop width toggles the activity panel; at mobile width opens the Memory Dashboard modal directly (existing behavior) |
| `src/gaia/apps/webui/src/components/ChatView.tsx` (header) | **EDIT** | Same — `<Brain>` button toggles the panel on desktop, opens the modal on mobile |
| `src/gaia/apps/webui/src/components/MemoryDashboard.tsx` | **UNCHANGED** behavior, **NEW** entry point | Modal stays as-is; only its trigger moves to a button inside the new panel |

### Layout

- **Desktop (>768px):** app becomes a 3-column flex layout — `Sidebar | MainContent | AgentActivityPanel`. Panel is hidden by default; toggling expands it. Width persisted to localStorage key `gaia.activityPanel.width` (default 400px, min 320px, max 640px).
- **Mobile (≤768px):** the panel UI is impractical alongside chat. The chat header `<Brain>` button **opens the existing Memory Dashboard modal directly** — same fallback as today. The panel and resize handle are not rendered at this width.
- **Open / close transition:** 220ms ease slide-out (matches `AnimatedPresence` duration already used in `App.tsx`). Chat content area's `flex: 1` absorbs the freed width; `overflow-x: hidden` on the parent prevents reflow flicker.
- **Drag handle:** 4px-wide vertical strip on the panel's left edge, `cursor: col-resize`, full-height, hover state visible. Uses pointer events with rAF-throttled width updates.
- **Theme support:** all panel CSS supports both themes via existing `[data-theme="dark"]` overrides in `index.css`. Activity-row backgrounds, chip colors, and the live-dot accent come from CSS variables (`--bg-card`, `--text-secondary`, etc.) — no hardcoded colors.

### Real-time behavior

1. **Insert at top.** New action entries prepend; oldest at bottom.
2. **Flash highlight.** New entries get a 600ms accent flash (`@keyframes activityFlash`).
3. **Live indicator.** A pulsing dot in the panel header when the **agent is actively generating a response** for the current session (i.e., chat SSE stream is open). Goes solid (no pulse) when idle. SSE is not continuously open between turns — "live" means "agent is working right now."
4. **Auto-scroll to top** on new entry only when the user is already at scrollTop ≤ 32px. Otherwise preserve scroll position and show a "↑ N new" pill at the top to jump up.
5. **Visible totals strip** at the top of the panel — counts derived from the current in-store action list. After session-load rebuild this matches "all actions in this session" except in the rare case of a session that exceeds the 500-entry cap. Strip label is "Visible: N actions" (honest about what's being shown), not "Session total." Click the strip → opens the Memory Dashboard modal where exact session totals live.

## UX

### Panel structure (top to bottom)

```
┌─────────────────────────────────────────┐
│ Activity                    ● live  ⛶ ✕ │  ← header: title, live dot, "open
│                                         │     Memory Dashboard" button (⛶),
│                                         │     close (✕)
├─────────────────────────────────────────┤
│ [All] [Memory] [Tools] [Files] [Errors] │  ← filter chips
├─────────────────────────────────────────┤
│ Visible: 12 actions                     │  ← totals strip (clickable → modal)
├─────────────────────────────────────────┤
│ ▸ 14:32  Remembered: "user prefers..."  │  ← live feed (newest first, all collapsed)
│ ▸ 14:31  Edited /src/foo.py (+12 -3)    │
│ ▸ 14:30  MCP: github.list_issues        │
│ ▸ 14:29  Recalled 3 memories            │
│ ▸ 14:28  ⚠ shell failed                 │
│ ...                                     │
└─────────────────────────────────────────┘
```

All rows start collapsed — `▸` chevron flips to `▾` when the user clicks to expand. No row auto-expands.

### Filter chips

- "All" + 4 categories: **Memory**, **Tools**, **Files**, **Errors**.
- MCP, web fetch, RAG queries, and shell commands all roll up under **Tools** — they're tool calls, and a separate chip per source would balloon the chip row.
- Client-side filter applied to the in-store actions.
- Chip state is **panel-global** (persisted to `localStorage['gaia.activityPanel.filter']`), not per-session — switching sessions keeps the user's chosen filter.
- **Hidden-new badge:** when actions arrive that don't match the current filter, the matching chip shows a small `+N` badge so the user knows hidden activity occurred. Badge clears when the chip is selected.

### Empty state

"No actions yet. The feed updates live as the agent works." — single line, muted, centered. Replaces the entire feed area.

### Action entry interactions

- **Click row** → toggles inline expansion. Expanded view shows category-specific detail (file diff, shell output, MCP args + result, recalled memory list, full error + traceback).
- **Inside the expanded view** for memory actions, an `Open in Memory Dashboard →` link sends the user to the modal on the Dashboard tab. (Deep-linking to a specific entry is future work.)
- **Keyboard:** rows are focusable; `Enter` / `Space` toggles expansion. `↑` / `↓` move focus row-to-row when the feed is focused.

## Filtering implementation

`activityFilter.ts` exports:

```ts
type ActionCategory = 'memory' | 'tools' | 'files' | 'errors';

interface ActionEntry {
  id: number;              // step.id
  category: ActionCategory;
  subtype?: string;        // e.g. 'memory_write', 'memory_recall', 'mcp', 'web', 'shell'
  timestamp: number;       // step.timestamp
  title: string;           // human-readable summary
  detail?: string;         // expandable detail (file diff, output, recall list)
  tool?: string;           // raw tool name
  success: boolean;
  latencyMs?: number;
  meta?: Record<string, unknown>;  // category-specific extras (path, lines, recall ids)
}

export function stepToAction(step: AgentStep): ActionEntry | null {
  // Hard excludes
  if (step.type === 'thinking' || step.type === 'status' || step.type === 'plan') return null;
  if (step.type === 'error') return errorAction(step);  // → category: 'errors'
  if (step.type !== 'tool') return null;

  // A failed tool step is always an error in the feed, regardless of tool name.
  if (step.success === false) return errorAction(step);

  switch (step.tool) {
    case 'remember':
    case 'forget':
    case 'supersede':
      return memoryWriteAction(step);             // → category: 'memory'
    case 'recall':
      return recallReturnedResults(step)
        ? memoryRetrievalAction(step)             // → category: 'memory'
        : null;
    case 'write_file':
    case 'edit_file':
    case 'delete_file':
      return fileAction(step);                    // → category: 'files'
    case 'read_file':
      return readFileIsSubstantive(step) ? fileAction(step) : null;
    case 'shell':
      return shellAction(step);                   // → category: 'tools', subtype: 'shell'
  }

  if (step.mcpServer) return mcpAction(step);     // → category: 'tools', subtype: 'mcp'
  return null;  // unknown tools default-out: keep the feed clean
}
```

The 4 user-facing categories (`memory` / `tools` / `files` / `errors`) match the filter chips exactly. `subtype` is metadata for icon/label rendering inside the row, not for filtering.

**Default-out, not default-in.** Unknown tools are filtered out by default. Adding a new tool to the feed is an explicit decision in `activityFilter.ts`. This keeps the feed signal-rich as new tools land.

## Resize behavior

- Drag handle on left edge, 4px wide, hover state visible.
- Pointer events (`pointerdown`, `pointermove`, `pointerup`) — works on touch + mouse.
- Width updates throttled via `requestAnimationFrame`.
- Min 320px, max 640px, default 400px.
- Persisted to `localStorage['gaia.activityPanel.width']` on `pointerup`.
- Read from localStorage on mount; clamp to [320, 640] on read in case of stale or invalid values.
- The CSS uses `flex: 0 0 var(--activity-panel-width)` on the panel; chat content area is `flex: 1` so it absorbs the remaining width.

## Accessibility

- **Panel container:** `role="region"`, `aria-label="Agent activity"`.
- **Live feed:** `role="feed"` with `aria-busy={isAgentGenerating}`. Each row is `role="article"` with an `aria-posinset` / `aria-setsize` pair so screen readers announce position.
- **New entries:** announced via an off-screen `aria-live="polite"` region — single concise message per insert (e.g., `"Memory written: user prefers dark mode"`). Avoid announcing every field.
- **Filter chips:** `role="tab"` group with `aria-selected` and `tabIndex` management; arrow keys move focus between chips, `Enter` / `Space` activates.
- **Drag handle:** `role="separator"` with `aria-orientation="vertical"`, `aria-valuenow={width}`, `aria-valuemin={320}`, `aria-valuemax={640}`. When focused, `←` / `→` adjusts width by 16px steps; `Home` / `End` snap to min / max.
- **Live indicator dot:** wrapped in `<span aria-hidden="true">`; the live state is communicated via `aria-busy` on the feed, not a redundant text label.
- **Close button:** `aria-label="Close activity panel"`; `Esc` while focus is in the panel closes it.

## Error handling

- **Step parse failures:** if `stepToAction` throws, log to console with the offending step JSON and skip — never let one malformed step block the panel.
- **Store overflow:** cap at 500 actions per session in memory. On overflow, drop the oldest. (Sessions persist actions to the backend separately via existing `agent_steps` storage; the panel is a live view, not a permanent log.)
- **localStorage quota:** wrap the width persistence in try/catch; if it throws, just don't persist this time.
- **SSE disconnection:** the live dot turns off. Existing ChatView SSE-reconnect logic handles re-establishing the connection — the panel just observes.

## Testing

| Test | Type | What it verifies |
|---|---|---|
| `activityFilter.test.ts` | Unit | Each tool category maps to the right `ActionEntry`; excluded tools return `null`; malformed steps don't throw |
| `agentActivityStore.test.ts` | Unit | `appendAction` filters via `stepToAction`; per-session keying; 500-entry cap |
| `AgentActivityPanel.test.tsx` | Component | Renders empty state with no actions; renders feed with mocked store; filter chips narrow the feed; click-to-expand works |
| Manual / Playwright | E2E | Open chat, ask agent to remember something + edit a file → both actions appear in panel within 1s of the SSE event |
| Manual | Resize | Drag handle resizes panel; width persists across reload; clamps at 320 / 640 |

CLI test plan (since GAIA values testing actual user paths): exercise the SSE pipeline end-to-end via `gaia chat --ui` with a session that triggers `remember` and `edit_file`, confirm panel populates live.

## Open questions / future work

1. **Background activity (non-chat).** Background agents (autonomy engine, scheduled MCP, manual maintenance) emit no chat SSE. A separate `/api/activity/stream` SSE merging those into the same panel is future work.
2. **Cross-session feed.** "What did the agent do across all sessions in the last hour?" — useful when multiple sessions or background agents are running. Out of scope for v1.
3. **Cross-day action history.** v1 rebuilds from already-loaded `messages[].agent_steps` on session-load. Showing actions from sessions beyond what's currently loaded would need a backend `/activity?since=...` endpoint. Out of scope for v1.
4. **Memory-citation grounding.** Today there is no first-class "this recall was used" signal in the response format. v1 falls back to "recall returned ≥ 1 result." Adding a citation token (e.g. `[memory:<id>]`) to memory tool responses + a parser pass would let us mark recalls as definitively grounded vs. speculative.
5. **Goal events in the feed.** Goal create / advance / complete events come through REST, not SSE. v1 keeps goals in the modal Goals tab; merging them into the live feed would need either polling `/memory/goals/stats` (cheap, 5–10s cadence) or a dedicated SSE channel.
6. **Per-row "open in modal" deep-link.** Today the link opens the modal on the Dashboard tab. Future: scroll the modal to / highlight the specific entry the user clicked.

## Approval

Pending user review of this spec.
