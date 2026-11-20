// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { create } from 'zustand';

import { translator } from '@webui/i18n/translator';
import type { GaiaNotification, NotificationType } from '@webui/types/agent';
import { permissionRequestNotification, policyAlertNotification } from '@webui/utils/toolEvents.core';

import { useChatStreamStore, type ChatTurn } from './chatStreams';

/** The web store's cap, so a long-running app cannot grow the list without end. */
const MAX_NOTIFICATIONS = 500;

interface NotificationsState {
    /** Newest first. */
    items: readonly GaiaNotification[];
    filter: NotificationType | null;
}

// App-lifetime memory only, like the web store.
export const useNotificationStore = create<NotificationsState>(() => ({ items: [], filter: null }));

function change(update: (items: readonly GaiaNotification[]) => readonly GaiaNotification[]): void {
    useNotificationStore.setState((state) => ({ items: update(state.items) }));
}

export function addNotification(notification: GaiaNotification): void {
    change((items) => (items.some((n) => n.id === notification.id) ? items : [notification, ...items].slice(0, MAX_NOTIFICATIONS)));
}

export const markNotificationRead = (id: string): void => change((items) => items.map((n) => (n.id === id ? { ...n, read: true } : n)));
export const markAllNotificationsRead = (): void => change((items) => items.map((n) => ({ ...n, read: true })));
export const dismissNotification = (id: string): void => change((items) => items.map((n) => (n.id === id ? { ...n, dismissed: true } : n)));
export const clearNotifications = (): void => change(() => []);
export const setNotificationFilter = (filter: NotificationType | null): void => useNotificationStore.setState({ filter });

/** The answer the center shows on an approval, from either place it was given. */
export function recordNotificationResponse(id: string, response: 'allow' | 'deny'): void {
    change((items) => items.map((n) => (n.id === id ? { ...n, response, read: true } : n)));
}

export const selectVisible = (state: NotificationsState): GaiaNotification[] => {
    const visible = state.items.filter((n) => !n.dismissed);
    return state.filter ? visible.filter((n) => n.type === state.filter) : visible;
};

export const selectUnreadCount = (state: NotificationsState): number => state.items.filter((n) => !n.read && !n.dismissed).length;

/** The filter row's kinds, always all of them, in the web's order. */
export const FILTER_ORDER: readonly NotificationType[] = ['permission_request', 'policy_alert', 'error', 'security_alert', 'status_change', 'info'];

/** Files what a chat's live turn raised: its approval request and its newest policy block. */
export function noteTurn(sessionId: string, turn: ChatTurn | undefined, previous: ChatTurn | undefined): void {
    if (!turn) return;
    const t = translator('en');
    const asked = turn.pendingPermission;
    if (asked && asked !== previous?.pendingPermission) {
        addNotification(permissionRequestNotification(asked, sessionId, [], t));
    }
    const blocked = turn.policyAlert;
    if (blocked && blocked.seq !== previous?.policyAlert?.seq) {
        const event = turn.events[blocked.seq];
        if (event) addNotification({ ...policyAlertNotification(event, sessionId, `policy-${sessionId}-${blocked.seq}`, t), sessionId });
    }
}

let feeding = false;

/** Starts filing notifications from every chat's live turn; safe to call twice. */
export function startNotificationFeed(): void {
    if (feeding) return;
    feeding = true;
    useChatStreamStore.subscribe((state, prev) => {
        for (const [sessionId, turn] of Object.entries(state.turns)) {
            if (turn !== prev.turns[sessionId]) noteTurn(sessionId, turn, prev.turns[sessionId]);
        }
    });
}
