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
    onPermissionRequest: (cb: (data: GaiaNotification) => void) => void;
    respondPermission: (id: string, action: 'allow' | 'deny', remember: boolean) => Promise<void>;
    onNotification: (cb: (data: GaiaNotification) => void) => void;
  };
}

// Augment Window to include gaiaAPI when running in Electron
declare global {
  interface Window {
    gaiaAPI?: GaiaElectronAPI;
  }
}
