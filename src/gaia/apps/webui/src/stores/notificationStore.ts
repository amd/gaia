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

/** These decisions apply to one displayed snapshot or code scope, never a tool name. */
/** The prompt raised when a tool reaches outside the chat's files (security.py). */
export const PATH_ACCESS_TOOL = 'allow_path_access';

function pathAccessArgs(args: unknown): { target: string | null; kind: unknown } {
  const { path, kind } = (args ?? {}) as { path?: unknown; kind?: unknown };
  return { target: typeof path === 'string' && path.trim() ? path : null, kind };
}

/**
 * The question a path-access prompt asks. `kind` is the agent's: it omits it only
 * when nothing exists at the path yet, so a prompt that names no path at all
 * says nothing about whether it exists. `followUp` means it backs a call just allowed.
 */
export function pathAccessQuestion(args: unknown, followUp = false): string {
  const { target, kind } = pathAccessArgs(args);
  const what = !target
    ? 'a file or folder outside this chat'
    : kind === 'folder'
      ? `the folder ${target} and everything in it`
      : kind === 'file' ? `the file ${target}` : `${target}, which doesn't exist yet`;
  return followUp
    ? `To do what you just allowed, GAIA also needs ${what}. This chat can't reach it yet. Allow it for this chat?`
    : `GAIA wants to use ${what}. This chat can't reach it yet. Allow it for this chat?`;
}

/** The path-access prompt's heading, in the user's terms rather than the tool's name. */
export function pathAccessTitle(args: unknown, followUp = false): string {
  const { kind } = pathAccessArgs(args);
  const noun = kind === 'folder' ? 'folder' : kind === 'file' ? 'file' : 'location';
  return followUp ? `Also let GAIA use this ${noun}?` : `Let GAIA use this ${noun}?`;
}

/** `C:\A\b.txt` and `c:/a/b.txt/` compare equal. Picks card wording only; too loose for an access check. */
function normalizePath(p: string): string {
  return p.trim().replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
}

/** The path a gated tool call acts on, if its arguments name one. */
function toolTargetPath(args: Record<string, unknown> | undefined): string | null {
  const value = ['file_path', 'path', 'directory', 'dir_path', 'destination']
    .map((k) => args?.[k])
    .find((v) => typeof v === 'string' && v.trim());
  return typeof value === 'string' ? value : null;
}

/**
 * Whether a path-access request backs the call this chat just allowed — the same
 * path, or a folder holding it. Only then may the prompt read as a follow-up.
 */
export function isPathAccessFollowUp(
  notifications: GaiaNotification[],
  sessionId: string,
  args: unknown,
): boolean {
  const previous = notifications.find((n) => n.type === 'permission_request' && n.sessionId === sessionId);
  if (!previous || previous.response !== 'allow' || previous.tool === PATH_ACCESS_TOOL) return false;
  const acted = toolTargetPath(previous.toolArgs);
  const { path } = (args ?? {}) as { path?: unknown };
  if (!acted || typeof path !== 'string' || !path.trim()) return false;
  const a = normalizePath(acted);
  const p = normalizePath(path);
  if (a === p || a.startsWith(`${p}/`)) return true;
  // A relative argument resolves against a directory the prompt doesn't show.
  const relative = !/^([a-z]:)?\//.test(a) && !a.startsWith('~');
  return relative && p.endsWith(`/${a.replace(/^\.\//, '')}`);
}

export function requiresFreshConsent(tool: string | undefined): boolean {
  return tool === 'share_engineering_context'
    || tool === 'append_engineering_context'
    || tool === 'approve_engineering_code'
    || tool === PATH_ACCESS_TOOL;
}

/**
 * How many earlier prompts in this chat asked the same thing and were allowed:
 * same tool, same "always" scope, same risk. Only counts when "always" is on
 * offer, so the prompt can point at it instead of asking a tenth time.
 */
export function repeatedAllowCount(notifications: GaiaNotification[], prompt: GaiaNotification): number {
  if (!prompt.alwaysScope || !prompt.sessionId || requiresFreshConsent(prompt.tool)) return 0;
  if (prompt.risk === 'destructive') return 0;
  return notifications.filter((n) =>
    n.id !== prompt.id
    && n.type === 'permission_request'
    && n.sessionId === prompt.sessionId
    && n.response === 'allow'
    && n.tool === prompt.tool
    && n.alwaysScope === prompt.alwaysScope
    && n.risk === prompt.risk
  ).length;
}

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

/** The chat events that can alert the user. */
export type AlertEvent = 'needs_approval' | 'question' | 'finished';
/** banner: OS notification plus badge; badge: counted on the tray/dock badge only. */
export type AlertMode = 'banner' | 'badge' | 'off';

export interface AlertSettings {
  modes: Record<AlertEvent, AlertMode>;
  /** A short chime with each alert. */
  sound: boolean;
  /** Flash the taskbar button when an alert fires and GAIA isn't focused. */
  flash: boolean;
  /** Hold off idle sleep while a chat turn runs (desktop app). */
  keepAwake: boolean;
  /** On battery, stop holding off sleep below this percent; 0 never stops. */
  batteryFloor: number;
}

export const ALERT_SETTINGS_KEY = 'gaia_alert_settings';

export const DEFAULT_ALERT_SETTINGS: AlertSettings = {
  modes: { needs_approval: 'banner', question: 'banner', finished: 'banner' },
  sound: false,
  flash: true,
  keepAwake: true,
  batteryFloor: 20,
};

const ALERT_MODES: readonly AlertMode[] = ['banner', 'badge', 'off'];

/** Saved settings over the defaults; an unreadable or invalid value is reported and replaced. */
export function loadAlertSettings(): AlertSettings {
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(ALERT_SETTINGS_KEY);
  } catch (err) {
    console.warn('[alerts] Notification settings unreadable (site data blocked); using defaults.', err);
    return DEFAULT_ALERT_SETTINGS;
  }
  if (!raw) return DEFAULT_ALERT_SETTINGS;
  let saved: Partial<AlertSettings>;
  try {
    saved = JSON.parse(raw);
  } catch (err) {
    console.warn('[alerts] Saved notification settings are corrupt; using defaults.', err);
    return DEFAULT_ALERT_SETTINGS;
  }
  const modes = { ...DEFAULT_ALERT_SETTINGS.modes };
  for (const event of Object.keys(modes) as AlertEvent[]) {
    const m = saved.modes?.[event];
    if (m !== undefined && ALERT_MODES.includes(m)) modes[event] = m;
    else if (m !== undefined) console.warn(`[alerts] Ignored unknown mode ${String(m)} for ${event}.`);
  }
  const bool = (v: unknown, d: boolean) => (typeof v === 'boolean' ? v : d);
  const floor = saved.batteryFloor;
  return {
    modes,
    sound: bool(saved.sound, DEFAULT_ALERT_SETTINGS.sound),
    flash: bool(saved.flash, DEFAULT_ALERT_SETTINGS.flash),
    keepAwake: bool(saved.keepAwake, DEFAULT_ALERT_SETTINGS.keepAwake),
    batteryFloor: typeof floor === 'number' && floor >= 0 && floor <= 100
      ? floor : DEFAULT_ALERT_SETTINGS.batteryFloor,
  };
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
