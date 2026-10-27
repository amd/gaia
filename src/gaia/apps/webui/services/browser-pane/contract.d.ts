// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// The browser pane's shared shapes. The .cjs services reference them through
// JSDoc imports and the renderer imports them as types.

export type TabId = string & { readonly __brand: "TabId" };

/** Why the pane refused or could not show something; cleared by the tab's next navigation. */
export type PaneNotice =
  | { kind: "blocked"; url: string; message: string }
  | { kind: "load-failed"; url: string; message: string }
  | { kind: "permission-denied"; permission: string; origin: string };

/** Who may drive a tab: shared with the agent, held by the user (the agent is paused), or private (hidden from the agent). */
export type TabControl = "shared" | "user" | "private";

/** A screen to emulate. size null fills the pane; colorScheme null follows the system. */
export interface ViewportChoice {
  size: { width: number; height: number; mobile: boolean } | null;
  colorScheme: "light" | "dark" | null;
}

/** The emulated screen, and who chose it. A user's choice wins over the agent's. */
export interface ViewportState extends ViewportChoice {
  source: "user" | "agent";
}

export interface TabState {
  id: TabId;
  /** null while the tab shows the new tab page and has no page of its own. */
  url: string | null;
  title: string;
  loading: boolean;
  canGoBack: boolean;
  canGoForward: boolean;
  devToolsOpen: boolean;
  notice: PaneNotice | null;
  /** The chat session whose agent opened this tab, or null when the user did. */
  openedBy: string | null;
  /** True while an agent op runs on this tab, and briefly after; drives the "GAIA is reading" strip. */
  agentBusy: boolean;
  /** True while the agent clicks or types here, and between its steps; drives "GAIA is using this tab". */
  agentActing: boolean;
  control: TabControl;
  /** null when the page fills the pane at the system color scheme. */
  viewport: ViewportState | null;
}

export interface RecentPage {
  url: string;
  title: string;
}

/** Main owns this and pushes all of it on every change. The renderer only mirrors it. */
export interface PaneState {
  visible: boolean;
  occluded: boolean;
  /** Never empty: closing the last tab leaves a new tab page. */
  tabs: TabState[];
  activeTabId: TabId;
  recent: RecentPage[];
  /** True while the user picks elements on the active tab (Cmd+Shift+S). */
  picking: boolean;
}

/** The placeholder's rect in CSS pixels, from getBoundingClientRect. */
export interface CssRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export type HistoryAction = "back" | "forward" | "reload" | "stop";

// ─── Element picker ─────────────────────────────────────────────────────

/** The computed styles a pick carries. Mirrors STYLE_PROPS in picker.cjs and gaia/browser/picked.py. */
export type PickedStyleProp =
  | "display" | "position" | "width" | "height" | "margin" | "padding" | "gap"
  | "color" | "background-color" | "font-family" | "font-size" | "font-weight" | "line-height"
  | "border" | "border-radius" | "box-shadow" | "opacity" | "z-index";

/** A React component from the page's main world; source only where the dev build stamps it. */
export interface PickedComponent {
  name: string;
  source: { file: string; line: number } | null;
}

/** One element the user picked, as main builds it (picker.cjs). */
export interface PickedElement {
  /** "p1", "p2" ...: stamped on the element as data-gaia-pick, so browser_click("p1") clicks it. */
  ref: string;
  url: string;
  title: string;
  tag: string;
  /** tag.class, as the outline's label shows it. */
  label: string;
  /** Shortest unique: #id, [data-testid], then a tag:nth-of-type path. */
  selector: string;
  role: string;
  name: string;
  text: string;
  /** CSS px, relative to the viewport when picked. */
  rect: CssRect;
  component: PickedComponent | null;
  /** At most 1536 characters; descendants past depth 2 folded to a comment. */
  outerHTML: string;
  /** Only values that differ from the usual default. */
  styles: Partial<Record<PickedStyleProp, string>>;
  /** PNG crop, base64, at most 800 px wide; null when it had no size or could not be taken. */
  crop: string | null;
}

/**
 * One chip as the chat request carries it (`page_elements`), validated by
 * gaia/browser/picked.py. A toggle that is off sends null; the crop travels
 * as an uploaded file.
 */
export interface PageElementWire extends Omit<PickedElement, "crop" | "outerHTML" | "styles"> {
  outerHTML: string | null;
  styles: Partial<Record<PickedStyleProp, string>> | null;
  cropUrl: string | null;
}

/** The current selection of one picking run. A later event for the same run replaces it. */
export interface PickEvent {
  run: number;
  elements: PickedElement[];
}

/** Commands main sends the window: the toggle shortcut pressed inside a pane page, or the agent opening a page. */
export type PaneCommand = "toggle" | "show";

/** window.gaiaBrowserPane (preload.cjs). Present only in the desktop app. */
export interface GaiaBrowserPaneAPI {
  getState(): Promise<PaneState>;
  /** Returns its unsubscribe. */
  onState(cb: (state: PaneState) => void): () => void;
  onCommand(cb: (command: PaneCommand) => void): () => void;
  setBounds(rect: CssRect): void;
  setVisible(visible: boolean): Promise<void>;
  /** Hides the live page under an app overlay. Resolves with a still frame of it, or null when no page shows. */
  setOccluded(occluded: boolean): Promise<string | null>;
  openTab(url: string | null): Promise<TabId>;
  closeTab(tabId: TabId): Promise<void>;
  selectTab(tabId: TabId): Promise<void>;
  navigate(tabId: TabId, url: string): Promise<void>;
  history(tabId: TabId, action: HistoryAction): Promise<void>;
  toggleDevTools(tabId: TabId): Promise<void>;
  clearBrowsingData(): Promise<void>;
  openExternal(tabId: TabId): Promise<void>;
  /** The chat session this window shows; only that chat's agent may use the pane. */
  setActiveSession(sessionId: string | null): void;
  /** Pause and Resume on the strip, and marking a tab private. */
  setControl(tabId: TabId, control: TabControl): Promise<void>;
  /** The Responsive control. null fills the pane again and lets the agent choose. */
  setViewport(tabId: TabId, choice: ViewportChoice | null): Promise<void>;
  /** Select element (Cmd+Shift+S): starts picking on the active tab, or ends it. */
  togglePicking(): Promise<void>;
  onPicked(cb: (event: PickEvent) => void): () => void;
}

// ─── Bridge (the agent, in Python, to main) ─────────────────────────────
// Served by bridge-server.cjs, called by gaia/browser/_pane_bridge.py.
// POST /v1/<op>, `Authorization: Bearer <token>`, a JSON body, a JSON reply.

/** Written at mode 0600 to the discovery file on start; removed on quit. */
export interface BridgeDiscovery {
  port: number;
  /** 32 random bytes, hex. */
  token: string;
  pid: number;
  protocol: number;
}

/** null in a request means the tab the user is looking at. */
export type TabRef = TabId | null;

/**
 * Loopback needs no entry. Any other origin the op may read or land on, as
 * URL.origin spells it, approved for this one call by Python's site gate.
 */
export type AllowedOrigins = string[];

/** A tab as the agent sees it. Private tabs are never listed. */
export interface TabSummary {
  id: TabId;
  url: string | null;
  title: string;
  active: boolean;
  openedBy: string | null;
  control: "shared" | "user";
}

export interface TabsReply {
  tabs: TabSummary[];
  /** null while the user looks at a private tab. */
  activeTabId: TabId | null;
}

/** The only keys the agent may press; anything else is refused before it reaches the page. */
export type AllowedKey =
  | "Enter" | "Escape" | "Tab" | "Backspace" | "Space"
  | "PageDown" | "PageUp" | "Home" | "End"
  | "ArrowLeft" | "ArrowUp" | "ArrowRight" | "ArrowDown";

/** One trusted input action. fill picks a <select> option by its label. */
export type ActAction =
  | { kind: "click" }
  | { kind: "fill"; text: string; pressEnter: boolean }
  | { kind: "press"; key: AllowedKey };

/** Built by Python from its snapshot ref table, never from model text. frameId null is the main frame. */
export interface ActTarget {
  frameId: string | null;
  selector: string;
}

export interface FrameEval {
  /** null for the main frame, else "processId:routingId". Main comes first. */
  frameId: string | null;
  url: string;
  ok: boolean;
  value?: unknown;
  error?: string;
}

export interface LogEntry {
  /** Rises by one per entry in each tab; the reader's cursor. */
  seq: number;
  at: number;
  level: "log" | "info" | "warning" | "error";
  /** At most 500 characters. */
  text: string;
  /** url:line */
  source?: string;
}

export interface NetworkEntry {
  seq: number;
  at: number;
  method: string;
  /** At most 300 characters. */
  url: string;
  /** null when the request failed before a response. */
  status: number | null;
  resourceType: string;
  durationMs: number | null;
  failure?: string;
}

export interface BridgeOps {
  hello: { req: Record<string, never>; res: { protocol: number; appVersion: string; ops: BridgeOp[] } };
  /** not_in_desktop is an answer here, so Python uses its headless browser; every later op sends sessionId and fails with not_in_desktop once the user switches chats. */
  attach: {
    req: { sessionId: string };
    res: { status: "attached"; tabId: TabId; paneVisible: boolean } | { status: "not_in_desktop" };
  };
  tabs: {
    req:
      | { sessionId: string; op: "list" }
      | { sessionId: string; op: "open"; url: string; allowedOrigins: AllowedOrigins }
      | { sessionId: string; op: "select"; tabId: TabId };
    res: TabsReply;
  };
  navigate: {
    req: { sessionId: string; tabId: TabRef; to: string | "back" | "forward" | "reload"; allowedOrigins: AllowedOrigins };
    res: { tabId: TabId; url: string; title: string };
  };
  /** The script is Python's own (the snapshot or find script), never model text. */
  evaluate: {
    req: { sessionId: string; tabId: TabRef; script: string; args: unknown; frames: "main" | "all"; allowedOrigins: AllowedOrigins };
    res: { tabId: TabId; url: string; frames: FrameEval[] };
  };
  origin: {
    req: { sessionId: string; tabId: TabRef };
    res: { tabId: TabId; url: string | null; origin: string | null; hasCookies: boolean };
  };
  screenshot: {
    req: { sessionId: string; tabId: TabRef; clip: { frameId: string | null; selector: string } | null; maxWidth: number; allowedOrigins: AllowedOrigins };
    res: { png: string; width: number; height: number; url: string };
  };
  logs: {
    req: {
      sessionId: string;
      tabId: TabRef;
      kind: "console" | "network";
      afterSeq: number;
      errorsOnly: boolean;
      pattern: string | null;
      limit: number;
      allowedOrigins: AllowedOrigins;
    };
    res: { entries: (LogEntry | NetworkEntry)[]; lastSeq: number; dropped: number; url: string };
  };
  /** Refused with user_active in a tab the user holds or touched in the last 4 s. A press may have no target: the focused element gets it. */
  act: {
    req: { sessionId: string; tabId: TabRef; action: ActAction; target: ActTarget | null; allowedOrigins: AllowedOrigins };
    res: { tabId: TabId; url: string; title: string; adoptedTabId?: TabId };
  };
  /** applied is false when the user picked the viewport; theirs stays. */
  viewport: {
    req: { sessionId: string; tabId: TabRef } & ViewportChoice;
    res: { tabId: TabId; applied: boolean; viewport: ViewportState | null };
  };
}

export type BridgeOp = keyof BridgeOps;

/** Python maps each onto an exception in _pane_bridge.py; a test keeps the two lists equal. */
export type BridgeErrorCode =
  | "unauthorized"
  | "forbidden"
  | "bad_request"
  | "unknown_op"
  | "internal"
  | "no_such_tab"
  | "no_page"
  | "unsupported"
  | "blocked_navigation"
  | "off_scope"
  | "navigation_failed"
  | "blocked_url"
  | "element_not_found"
  | "not_in_desktop"
  | "timeout"
  | "user_active"
  | "private_tab"
  | "not_actionable";

export interface BridgeErrorReply {
  error: { code: BridgeErrorCode; message: string; data: Record<string, unknown> };
}
