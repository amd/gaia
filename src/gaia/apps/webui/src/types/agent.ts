// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Notification types, and the slice of the Electron preload API the UI uses. */

// ── Notification Types ───────────────────────────────────────────────────

export type NotificationType =
  | 'permission_request'
  | 'policy_alert'
  | 'security_alert'
  | 'status_change'
  | 'info'
  | 'error';
export type NotificationPriority = 'low' | 'medium' | 'high' | 'critical';

export interface GaiaNotification {
  id: string;
  type: NotificationType;
  agentId: string;
  agentName: string;
  title: string;
  message: string;
  timestamp: number;
  read: boolean;
  dismissed: boolean;
  priority: NotificationPriority;
  /** For permission_request type. */
  tool?: string;
  toolArgs?: Record<string, unknown>;
  /** Chat session that raised the request; "always allow" grants are scoped to it. */
  sessionId?: string;
  /** Echoed back on the answer so a late click cannot answer a newer prompt. */
  confirmId?: string;
  /** What "always allow" would grant, e.g. `gh issue list`; absent means it is not offered. */
  alwaysScope?: string;
  /** What the call does (read / write / execute / destructive), from the backend's call_risk. */
  risk?: string;
  /** The agent's own one-line description of the call, shown when no args came with it. */
  summary?: string;
  /** For policy_alert type. */
  decision?: string;
  reason?: string;
  ruleIds?: string[];
  policyVersion?: string;
  receiptId?: string;
  actions?: string[];
  timeoutSeconds?: number;
  /** Response (after user action). */
  response?: 'allow' | 'deny';
  respondedAt?: number;
}

// ── Electron Preload API ─────────────────────────────────────────────────

/** Desktop-app settings saved by the tray (services/tray-manager.cjs). */
export interface TraySettings {
  /** Closing the window hides it; GAIA keeps running in the tray. */
  minimizeToTray: boolean;
  /** Every launch starts with the window hidden. */
  startMinimized: boolean;
  /** GAIA starts when the user signs in. */
  startOnLogin: boolean;
}

export interface TrayConfig {
  tray: TraySettings;
}

export interface TrayChatRef {
  id: string;
  title: string;
}

/** What GAIA is doing, sent to the tray (validated by services/tray-state.cjs). */
export interface TrayActivity {
  backendConnected: boolean;
  /** null until the first status read answers. */
  modelServerRunning: boolean | null;
  /** The loaded chat model; `where` is GPU/NPU/CPU, or "cloud" for a hosted model. */
  model: { name: string; where: string | null } | null;
  /** What the model is doing while the answer waits on it, e.g. "Loading X into memory". */
  waitingOnModel: string | null;
  running: TrayChatRef[];
  needsYou: Array<TrayChatRef & { reason: 'approval' | 'answer' }>;
  recent: TrayChatRef[];
}

/** A tray menu action for the window to carry out. */
export type TrayCommand =
  | { type: 'open-chat'; sessionId: string }
  | { type: 'new-chat' }
  | { type: 'open-settings'; section: 'general' }
  | { type: 'refresh-status' };

/** The part of window.gaiaAPI (preload.cjs) the UI calls. Present only in Electron. */
export interface GaiaElectronAPI {
  notification: {
    onPermissionRequest: (cb: (data: GaiaNotification) => void) => void;
    respondPermission: (id: string, action: 'allow' | 'deny', remember: boolean) => Promise<void>;
    onNotification: (cb: (data: GaiaNotification) => void) => void;
  };
  tray: {
    getConfig: () => Promise<TrayConfig>;
    /** Resolves with the saved config; rejects with why it couldn't be saved. */
    setConfig: (cfg: { tray: Partial<TraySettings> }) => Promise<TrayConfig>;
    setActivity: (activity: TrayActivity) => Promise<void>;
    /** Returns an unsubscribe function. */
    onCommand: (cb: (command: TrayCommand) => void) => () => void;
  };
}

// Augment Window to include gaiaAPI when running in Electron
declare global {
  interface Window {
    gaiaAPI?: GaiaElectronAPI;
  }
}
