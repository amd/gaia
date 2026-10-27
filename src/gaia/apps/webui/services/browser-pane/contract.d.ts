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
}

/** The placeholder's rect in CSS pixels, from getBoundingClientRect. */
export interface CssRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export type HistoryAction = "back" | "forward" | "reload" | "stop";

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

export interface TabSummary {
  id: TabId;
  url: string | null;
  title: string;
  active: boolean;
  openedBy: string | null;
}

export interface TabsReply {
  tabs: TabSummary[];
  activeTabId: TabId;
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
  | "timeout";

export interface BridgeErrorReply {
  error: { code: BridgeErrorCode; message: string; data: Record<string, unknown> };
}
