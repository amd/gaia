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
import { confirmToolExecution } from '../services/api';
import { useChatStore } from './chatStore';

// ── Constants ────────────────────────────────────────────────────────────

/** Maximum notifications kept in the center to prevent unbounded growth. */
const MAX_NOTIFICATIONS = 500;

// ── State Interface ──────────────────────────────────────────────────────

interface NotificationState {
  /** All notifications (newest first). */
  notifications: GaiaNotification[];
  /** Whether the notification panel is open. */
  showPanel: boolean;
  /** Active type filter for the notification center (null = all). */
  typeFilter: NotificationType | null;

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
}

// ── Store Implementation ─────────────────────────────────────────────────

export const useNotificationStore = create<NotificationState>((set, get) => ({
  notifications: [],
  showPanel: false,
  typeFilter: null,

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
    const electronApi = window.gaiaAPI;
    if (electronApi) {
      // Electron path: route via IPC
      try {
        await electronApi.notification.respondPermission(id, action, remember);
      } catch (err) {
        console.error('[notificationStore] Failed to send permission response via IPC:', err);
        // Don't update local state — the agent didn't receive the response.
        // The permission prompt remains actionable so the user can retry.
        return;
      }
    } else {
      // Web path: route via HTTP to /api/chat/confirm
      const sessionId = useChatStore.getState().currentSessionId;
      if (sessionId) {
        try {
          await confirmToolExecution(sessionId, id, action, remember);
        } catch (err) {
          console.error('[notificationStore] Failed to send permission response via HTTP:', err);
          return;
        }
      }
    }
    // Persist "always allow" preference in localStorage
    if (action === 'allow' && remember) {
      const ALWAYS_ALLOW_KEY = 'gaia_always_allow_tools';
      const notification = get().notifications.find((n) => n.id === id);
      if (notification?.tool) {
        const existing: string[] = JSON.parse(localStorage.getItem(ALWAYS_ALLOW_KEY) || '[]');
        if (!existing.includes(notification.tool)) {
          existing.push(notification.tool);
          localStorage.setItem(ALWAYS_ALLOW_KEY, JSON.stringify(existing));
        }
      }
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

/** Get the first pending permission request (reactive selector for PermissionPrompt). */
export const selectActivePermissionPrompt = (state: NotificationState): GaiaNotification | null =>
  state.notifications.find(
    (n) => n.type === 'permission_request' && !n.response && !n.dismissed
  ) ?? null;
