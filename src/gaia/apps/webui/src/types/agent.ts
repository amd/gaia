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
  /** `alwaysScope` in plain words, e.g. "editing notes.txt". */
  alwaysLabel?: string;
  /** The tool in plain words, e.g. "writing file". */
  toolLabel?: string;
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

/** The part of window.gaiaAPI (preload.cjs) the UI calls. Present only in Electron. */
export interface GaiaElectronAPI {
  notification: {
    /** Each `on*` returns its unsubscribe. */
    onPermissionRequest: (cb: (data: GaiaNotification) => void) => () => void;
    respondPermission: (id: string, action: 'allow' | 'deny', remember: boolean) => Promise<void>;
    onNotification: (cb: (data: GaiaNotification) => void) => () => void;
    /** OS banner for a chat event; resolves false when the OS has no notifications. */
    show: (payload: { title: string; body: string; sessionId: string }) => Promise<boolean>;
    /** Flash the taskbar button until the window is focused. */
    flash: () => Promise<boolean>;
    /** Chats needing the user, shown on the tray / dock badge. */
    setAttentionCount: (count: number) => Promise<void>;
  };
  tray: {
    /** `session:<id>` when an OS banner for that chat is clicked. */
    onNavigate: (cb: (target: string) => void) => () => void;
  };
  power: {
    setKeepAwake: (on: boolean) => Promise<boolean>;
  };
}

// Augment Window to include gaiaAPI when running in Electron
/** window.gaiaWindow (preload.cjs): how the desktop app's window frame is drawn. */
export interface GaiaWindowFrame {
  titleBar: 'mac' | 'overlay';
  setColors: (colors: { color: string; symbolColor: string }) => Promise<boolean>;
}

declare global {
  interface Window {
    gaiaAPI?: GaiaElectronAPI;
    gaiaWindow?: GaiaWindowFrame;
  }
}
