// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Zustand store for notification management.
 *
 * Handles permission requests, security alerts, status changes, and general
 * notifications from OS agents. Notifications are persisted in memory and
 * cleared on dismiss or on session end.
 */

import { create } from 'zustand';
import type { GaiaNotification, NotificationType } from '../types/agent';
import { confirmTool } from '../services/api';

// ── Constants ────────────────────────────────────────────────────────────

/** Maximum notifications kept in the center to prevent unbounded growth. */
const MAX_NOTIFICATIONS = 500;

/**
 * Legacy localStorage key for the "always allow" tool list.
 *
 * Always-allow grants now live in this store only, scoped to one chat
 * session: a tick on `run_shell_command` must not silently approve every
 * shell command in every chat for the life of the browser profile. The key
 * is still named here so `purgeLegacyAlwaysAllow()` can delete any list a
 * previous build persisted.
 */
export const LEGACY_ALWAYS_ALLOW_TOOLS_KEY = 'gaia_always_allow_tools';

/**
 * Drop any always-allow list persisted by an older build. Called once at app
 * start; grants from a previous run are not carried into this one.
 */
export function purgeLegacyAlwaysAllow(): void {
    try {
        if (localStorage.getItem(LEGACY_ALWAYS_ALLOW_TOOLS_KEY) !== null) {
            localStorage.removeItem(LEGACY_ALWAYS_ALLOW_TOOLS_KEY);
            console.warn(
                '[notificationStore] Discarded a persisted "always allow" tool list from an ' +
                'earlier version — grants now cover one chat until you reload or restart GAIA and are ' +
                'revocable in Settings → Tools & Permissions.'
            );
        }
    } catch (err) {
        console.error('[notificationStore] Could not clear the legacy always-allow list:', err);
    }
}

/** A tool the user allowed for the rest of one chat session. In memory only. */
export interface SessionToolGrant {
  sessionId: string;
  tool: string;
  grantedAt: number;
}

// ── State Interface ──────────────────────────────────────────────────────

interface NotificationState {
  /** All notifications (newest first). */
  notifications: GaiaNotification[];
  /** Whether the notification panel is open. */
  showPanel: boolean;
  /** Active type filter for the notification center (null = all). */
  typeFilter: NotificationType | null;

  /**
   * "Allow for the rest of this chat" grants, keyed by chat session + tool.
   * Never persisted — a reload or restart starts empty.
   */
  alwaysAllowGrants: SessionToolGrant[];

  // ── Actions ─────────────────────────────────────────────────────────
  addNotification: (notification: GaiaNotification) => void;
  dismiss: (id: string) => void;
  markRead: (id: string) => void;
  markAllRead: () => void;
  clearAll: () => void;
  setShowPanel: (show: boolean) => void;
  setTypeFilter: (type: NotificationType | null) => void;

  /** Respond to a permission request notification. */
  respondToPermission: (id: string, action: 'allow' | 'deny', remember: boolean) => Promise<void>;

  /** Whether `tool` carries an always-allow grant in chat session `sessionId`. */
  isAlwaysAllowed: (sessionId: string, tool: string) => boolean;

  /** Revoke one grant. */
  revokeAlwaysAllow: (sessionId: string, tool: string) => void;

  /** Revoke every grant in every chat. */
  revokeAllAlwaysAllow: () => void;
}

// ── Store Implementation ─────────────────────────────────────────────────

export const useNotificationStore = create<NotificationState>((set, get) => ({
  notifications: [],
  showPanel: false,
  typeFilter: null,
  alwaysAllowGrants: [],

  addNotification: (notification) =>
    set((state) => ({
      notifications: [notification, ...state.notifications].slice(0, MAX_NOTIFICATIONS),
    })),

  dismiss: (id) =>
    set((state) => ({
      notifications: state.notifications.map((n) =>
        n.id === id ? { ...n, dismissed: true } : n
      ),
    })),

  markRead: (id) =>
    set((state) => ({
      notifications: state.notifications.map((n) =>
        n.id === id ? { ...n, read: true } : n
      ),
    })),

  markAllRead: () =>
    set((state) => ({
      notifications: state.notifications.map((n) => ({ ...n, read: true })),
    })),

  clearAll: () => set({ notifications: [] }),

  setShowPanel: (show) => set({ showPanel: show }),

  setTypeFilter: (type) => set({ typeFilter: type }),

  respondToPermission: async (id, action, remember) => {
    const notification = get().notifications.find((n) => n.id === id);
    const chatSessionId = notification?.sessionId;

    // Chat prompts belong to the backend even when Electron IPC is available.
    const electronApi = window.gaiaAPI;
    if (chatSessionId) {
      try {
        await confirmTool(chatSessionId, action === 'allow');
      } catch (err) {
        console.error('[notificationStore] Failed to send permission response via REST:', err);
        return;
      }
    } else if (notification && electronApi?.notification?.respondPermission) {
      try {
        await electronApi.notification.respondPermission(id, action, remember);
      } catch (err) {
        console.error('[notificationStore] Failed to send permission response via IPC:', err);
        // Don't update local state — the agent didn't receive the response.
        // The permission prompt remains actionable so the user can retry.
        return;
      }
    } else {
      console.error('[notificationStore] No permission response destination for notification:', id);
      return;
    }
    // Grant only within the chat that asked. A request with no chat session
    // (an OS agent) has nothing to auto-approve here; its remember flag
    // already went to the agent above.
    const tool = notification?.tool;
    if (action === 'allow' && remember && chatSessionId && tool) {
      set((state) =>
        state.alwaysAllowGrants.some((g) => g.sessionId === chatSessionId && g.tool === tool)
          ? state
          : {
              alwaysAllowGrants: [
                ...state.alwaysAllowGrants,
                { sessionId: chatSessionId, tool, grantedAt: Date.now() },
              ],
            }
      );
    }
    // Update local state after response is delivered
    set((state) => ({
      notifications: state.notifications.map((n) =>
        n.id === id
          ? { ...n, response: action, respondedAt: Date.now(), read: true }
          : n
      ),
    }));
  },

  isAlwaysAllowed: (sessionId, tool) =>
    get().alwaysAllowGrants.some((g) => g.sessionId === sessionId && g.tool === tool),

  revokeAlwaysAllow: (sessionId, tool) =>
    set((state) => ({
      alwaysAllowGrants: state.alwaysAllowGrants.filter(
        (g) => !(g.sessionId === sessionId && g.tool === tool)
      ),
    })),

  revokeAllAlwaysAllow: () => set({ alwaysAllowGrants: [] }),

}));

// ── Selectors ────────────────────────────────────────────────────────────

/** Get unread count (excluding dismissed). */
export const selectUnreadCount = (state: NotificationState): number =>
  state.notifications.filter((n) => !n.read && !n.dismissed).length;

/** Get pending permission requests. */
export const selectPendingPermissions = (state: NotificationState): GaiaNotification[] =>
  state.notifications.filter(
    (n) => n.type === 'permission_request' && !n.response && !n.dismissed
  );

/** Get visible (non-dismissed) notifications, optionally filtered by type. */
export const selectVisibleNotifications = (state: NotificationState): GaiaNotification[] => {
  const visible = state.notifications.filter((n) => !n.dismissed);
  if (state.typeFilter) {
    return visible.filter((n) => n.type === state.typeFilter);
  }
  return visible;
};

/** Every active "allow for the rest of this chat" grant. */
export const selectAlwaysAllowGrants = (state: NotificationState): SessionToolGrant[] =>
  state.alwaysAllowGrants;

/** Get the first pending permission request (reactive selector for PermissionPrompt). */
export const selectActivePermissionPrompt = (state: NotificationState): GaiaNotification | null =>
  state.notifications.find(
    (n) => n.type === 'permission_request' && !n.response && !n.dismissed
  ) ?? null;
