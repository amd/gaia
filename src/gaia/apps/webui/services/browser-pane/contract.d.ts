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

/** A box the page opened with alert(), confirm() or prompt(), or the pane's "leave this page?" for beforeunload. */
export type PageDialogType = "alert" | "confirm" | "prompt" | "beforeunload";

/** The box a tab's page is waiting on. The page is frozen until someone answers it. */
export interface PageDialog {
  type: PageDialogType;
  message: string;
  /** The prompt's starting text; "" for other boxes. */
  defaultText: string;
  /** The page or frame that opened it. */
  url: string;
  /** "agent" when it opened during the agent's op on this tab: dismissed when that agent's turn ends unanswered. */
  openedBy: "agent" | "page";
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
  /** The box the page waits on, or null. One at a time per tab. */
  dialog: PageDialog | null;
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
  /** Dev servers GAIA started this run, for the toolbar's Dev servers menu. */
  devServers: DevServerState[];
}

// ─── Dev servers ────────────────────────────────────────────────────────

/** One launch.json configuration as Python resolved it (gaia/browser/dev_servers.py). Never a shell string. */
export interface DevServerSpec {
  name: string;
  argv: string[];
  /** Absolute; Python checked it is inside the chat's allowed folders. */
  cwd: string;
  port: number;
  /** Only names in bridge/dev_server_env.json; the app's own environment passes through an allowlist too. */
  env: Record<string, string>;
  /** http://localhost:<port><path> */
  url: string;
  readyTimeoutMs: number;
}

/** starting until the port answers; failed when it exits first; exited when it ends later on its own. */
export type DevServerStatus = "starting" | "ready" | "failed" | "exited" | "stopped";

export interface DevServerState {
  /** Python's handle: sha256 of cwd and name, 16 hex digits. */
  key: string;
  name: string;
  argv: string[];
  cwd: string;
  port: number;
  env: Record<string, string>;
  url: string;
  status: DevServerStatus;
  /** null once it is no longer running. */
  pid: number | null;
  startedAt: number;
  exitCode: number | null;
  logFile: string;
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
  /** Stop in the Dev servers menu. */
  stopDevServer(key: string): Promise<void>;
  /** The user's answer to the box in the pane. text is the prompt's answer; null keeps its starting text. */
  answerDialog(tabId: TabId, accept: boolean, text: string | null): Promise<void>;
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

/** Built by Python from its snapshot ref table, never from model text. frameId null is the main frame; any frame, cross-site too. */
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
    res: { tabId: TabId; url: string; title: string; adoptedTabId?: TabId; dialog?: PageDialog };
  };
  /**
   * The page's box. get reads it for the site gate; answer is refused with
   * off_scope when accepting a non-alert box on a site not in allowedOrigins;
   * release dismisses the boxes this chat's agent opened and left (turn end).
   */
  dialog: {
    req:
      | { sessionId: string; op: "get"; tabId: TabRef }
      | { sessionId: string; op: "answer"; tabId: TabRef; accept: boolean; text: string | null; allowedOrigins: AllowedOrigins }
      | { sessionId: string; op: "release" };
    res:
      | { tabId: TabId; dialog: PageDialog | null }
      | { tabId: TabId; answered: PageDialog; url: string; title: string; dialog: PageDialog | null }
      | { dismissed: number };
  };
  /** The model's expression in the page's main world (browser_eval). Refused with unsupported off loopback; page errors come back as ok false. */
  script: {
    req: { sessionId: string; tabId: TabRef; expression: string };
    res:
      | { tabId: TabId; url: string; ok: true; json: string; truncated: boolean }
      | { tabId: TabId; url: string; ok: false; error: string; dialog?: PageDialog };
  };
  /** applied is false when the user picked the viewport; theirs stays. */
  viewport: {
    req: { sessionId: string; tabId: TabRef } & ViewportChoice;
    res: { tabId: TabId; applied: boolean; viewport: ViewportState | null };
  };
  /** ensure is idempotent: the same spec returns the running server, a changed one restarts it. */
  devServers: {
    req:
      | { sessionId: string; op: "list" }
      | { sessionId: string; op: "ensure"; key: string; spec: DevServerSpec; searchPath: string | null }
      | { sessionId: string; op: "stop"; key: string }
      | { sessionId: string; op: "logs"; key: string; errorsOnly: boolean; pattern: string | null; limit: number };
    res:
      | { servers: DevServerState[] }
      | { server: DevServerState; reused: boolean; ms: number }
      | { server: DevServerState }
      | { server: DevServerState; lines: string[]; matched: number; total: number };
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
  | "not_actionable"
  | "dialog_open"
  | "dev_server_failed"
  | "port_in_use"
  | "no_such_server";

export interface BridgeErrorReply {
  error: { code: BridgeErrorCode; message: string; data: Record<string, unknown> };
}
