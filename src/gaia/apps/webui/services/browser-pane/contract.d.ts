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

/** Commands main sends the window, e.g. the toggle shortcut pressed inside a pane page. */
export type PaneCommand = "toggle";

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
}
