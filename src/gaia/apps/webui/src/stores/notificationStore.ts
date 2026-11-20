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
import { useChatStore } from './chatStore';
import { requiresFreshConsent } from '../utils/permissionPrompts.core';
import {
  ALERT_SETTINGS_KEY,
  DEFAULT_ALERT_SETTINGS,
  parseAlertSettings,
  type AlertEvent,
  type AlertMode,
  type AlertSettings,
} from '../utils/alertSettings.core';

export {
  PATH_ACCESS_TOOL,
  PROJECT_TRUST_TOOL,
  alwaysScopeText,
  isPathAccessFollowUp,
  pathAccessQuestion,
  pathAccessTitle,
  permissionPromptText,
  repeatedAllowCount,
  requiresFreshConsent,
} from '../utils/permissionPrompts.core';

// ── Constants ────────────────────────────────────────────────────────────

/** Maximum notifications kept in the center to prevent unbounded growth. */
const MAX_NOTIFICATIONS = 500;

/**
 * Legacy localStorage key for a client-side "always allow" list. Grants now
 * live on the backend, scoped to one call in one chat
 * (`GET /api/chat/permissions`); this drops any list an older build persisted.
 */
export const LEGACY_ALWAYS_ALLOW_TOOLS_KEY = 'gaia_always_allow_tools';

export function purgeLegacyAlwaysAllow(): void {
    try {
        if (localStorage.getItem(LEGACY_ALWAYS_ALLOW_TOOLS_KEY) !== null) {
            localStorage.removeItem(LEGACY_ALWAYS_ALLOW_TOOLS_KEY);
            console.warn(
                '[notificationStore] Discarded a persisted "always allow" tool list from an ' +
                'earlier version. Grants now cover one call in one chat and are listed in ' +
                'Settings → Permissions.'
            );
        }
    } catch (err) {
        console.error('[notificationStore] Could not clear the legacy always-allow list:', err);
    }
}

/** How the user answered a permission prompt. */
export type PermissionDecision = 'allow' | 'always' | 'deny';


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

  /**
   * Answer a permission request. Rejects when the answer did not reach the
   * agent, so the prompt stays answerable and the caller can say why.
   */
  respondToPermission: (id: string, decision: PermissionDecision) => Promise<void>;

  /** Drop chat `sessionId`'s unanswered prompts once its run has ended. */
  dismissSessionPrompts: (sessionId: string) => void;
}

// ── Store Implementation ─────────────────────────────────────────────────

export const useNotificationStore = create<NotificationState>((set, get) => ({
  notifications: [],
  showPanel: false,
  typeFilter: null,

  addNotification: (notification) =>
    set((state) => {
      const existing = state.notifications.find((n) => n.id === notification.id);
      if (!existing) {
        return { notifications: [notification, ...state.notifications].slice(0, MAX_NOTIFICATIONS) };
      }
      // A re-attached run replays its prompt with the same id: keep one entry, and any answer.
      if (existing.response) return state;
      return {
        notifications: state.notifications.map((n) =>
          n.id === notification.id ? { ...notification, read: existing.read } : n
        ),
      };
    }),

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

  dismissSessionPrompts: (sessionId) =>
    set((state) => ({
      notifications: state.notifications.map((n) =>
        n.type === 'permission_request' && n.sessionId === sessionId && !n.response
          ? { ...n, dismissed: true }
          : n
      ),
    })),

  respondToPermission: async (id, decision) => {
    const notification = get().notifications.find((n) => n.id === id);
    if (!notification) throw new Error('That permission request is no longer pending.');
    const allow = decision !== 'deny';
    const always = decision === 'always' && !requiresFreshConsent(notification.tool);
    const electronApi = window.gaiaAPI;
    if (notification.sessionId) {
      await confirmTool(notification.sessionId, allow, { always, confirmId: notification.confirmId });
    } else if (electronApi?.notification?.respondPermission) {
      await electronApi.notification.respondPermission(id, allow ? 'allow' : 'deny', always);
    } else {
      throw new Error('No agent is waiting for this answer any more.');
    }
    set((state) => ({
      notifications: state.notifications.map((n) =>
        n.id === id
          ? { ...n, response: allow ? 'allow' : 'deny', respondedAt: Date.now(), read: true }
          : n
      ),
    }));
    // The run goes on, often silently (a long tool), so the answer itself ends the wait.
    const chat = useChatStore.getState();
    if (notification.sessionId && notification.sessionId === chat.currentSessionId && chat.awaitingUser) {
      chat.setAwaitingUser(false);
    }
  },
}));

// ── Selectors ────────────────────────────────────────────────────────────

/** Get unread count (excluding dismissed). */
export const selectUnreadCount = (state: NotificationState): number =>
  state.notifications.filter((n) => !n.read && !n.dismissed).length;

/** Get visible (non-dismissed) notifications, optionally filtered by type. */
export const selectVisibleNotifications = (state: NotificationState): GaiaNotification[] => {
  const visible = state.notifications.filter((n) => !n.dismissed);
  if (state.typeFilter) {
    return visible.filter((n) => n.type === state.typeFilter);
  }
  return visible;
};

/** The kinds of notification in the list, for the panel's filter tabs. */
export const selectPresentTypes = (state: NotificationState): NotificationType[] => [
  ...new Set(state.notifications.filter((n) => !n.dismissed).map((n) => n.type)),
];

/** The newest unanswered permission request of chat `sessionId` — the one the agent waits on. */
export const selectSessionPermissionPrompt = (sessionId: string) =>
  (state: NotificationState): GaiaNotification | null =>
    state.notifications.find(
      (n) => n.type === 'permission_request' && n.sessionId === sessionId && !n.response && !n.dismissed
    ) ?? null;

/** Chats with an unanswered permission prompt, sorted; feeds the chat list's Needs you group. */
export const selectPromptingSessionIds = (state: NotificationState): string[] =>
  [...new Set(
    state.notifications
      .filter((n) => n.type === 'permission_request' && n.sessionId && !n.response && !n.dismissed)
      .map((n) => n.sessionId as string)
  )].sort();

// ── Chat alert settings (#5226) ──────────────────────────────────────────

export {
  ALERT_SETTINGS_KEY,
  DEFAULT_ALERT_SETTINGS,
  type AlertEvent,
  type AlertMode,
  type AlertSettings,
} from '../utils/alertSettings.core';

/** Saved settings over the defaults; an unreadable or invalid value is reported and replaced. */
export function loadAlertSettings(): AlertSettings {
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(ALERT_SETTINGS_KEY);
  } catch (err) {
    console.warn('[alerts] Notification settings unreadable (site data blocked); using defaults.', err);
    return DEFAULT_ALERT_SETTINGS;
  }
  return parseAlertSettings(raw);
}

interface AlertSettingsState extends AlertSettings {
  setMode: (event: AlertEvent, mode: AlertMode) => void;
  update: (patch: Partial<Omit<AlertSettings, 'modes'>>) => void;
}

function saveAlertSettings(s: AlertSettings): void {
  try {
    localStorage.setItem(ALERT_SETTINGS_KEY, JSON.stringify(s));
  } catch (err) {
    console.warn('[alerts] Notification settings not saved; they will reset on reload.', err);
  }
}

const pickSettings = (s: AlertSettings): AlertSettings => ({
  modes: s.modes, sound: s.sound, flash: s.flash, keepAwake: s.keepAwake, batteryFloor: s.batteryFloor,
});

export const useAlertSettings = create<AlertSettingsState>((set, get) => ({
  ...loadAlertSettings(),
  setMode: (event, mode) => {
    set({ modes: { ...get().modes, [event]: mode } });
    saveAlertSettings(pickSettings(get()));
  },
  update: (patch) => {
    set(patch);
    saveAlertSettings(pickSettings(get()));
  },
}));
