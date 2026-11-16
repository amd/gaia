// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Chat alerts for the whole app (#5226, #4813). Mounted once in App, so
 * notifications arrive whether or not the notification panel is open.
 */

import { useEffect, useRef, useState } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { useChatStore } from '../stores/chatStore';
import {
    selectPromptingSessionIds,
    useAlertSettings,
    useNotificationStore,
} from '../stores/notificationStore';
import {
    alertText,
    attentionCount,
    batteryAllowsKeepAwake,
    chatAlertEvents,
    chatIsInView,
    chatPhases,
    playChime,
    type BatteryState,
    type ChatPhases,
} from '../utils/chatAlerts';
import { log } from '../utils/logger';
import * as api from '../services/api';
import type { Session } from '../types';

interface BatteryManagerLike extends EventTarget {
    charging: boolean;
    level: number;
}

/** Electron IPC: subscribe once, unsubscribe on unmount. */
function useElectronNotifications(): void {
    useEffect(() => {
        const electron = window.gaiaAPI;
        if (!electron) return;
        const add = useNotificationStore.getState().addNotification;
        const offs = [
            electron.notification.onNotification(add),
            electron.notification.onPermissionRequest(add),
            electron.tray.onNavigate((target) => {
                const id = typeof target === 'string' && target.startsWith('session:')
                    ? target.slice('session:'.length) : null;
                if (!id) return;
                const store = useChatStore.getState();
                if (store.currentSessionId !== id) store.setMessages([]);
                store.setCurrentSession(id);
            }),
        ];
        return () => offs.forEach((off) => off());
    }, []);
}

function showBanner(sessionId: string, title: string, body: string): void {
    const electron = window.gaiaAPI;
    if (electron) {
        electron.notification.show({ title, body, sessionId })
            .then((shown) => { if (!shown) log.system.warn('This OS has no desktop notifications; the badge still counts it.'); })
            .catch((err) => log.system.error('Could not show the desktop notification', err));
        return;
    }
    if (typeof Notification === 'undefined') {
        log.system.warn('This browser has no notifications; the alert was not shown.');
        return;
    }
    if (Notification.permission !== 'granted') {
        log.system.warn(`Browser notifications are ${Notification.permission}; allow them in Settings → Notifications.`);
        return;
    }
    const n = new Notification(title, { body, tag: `gaia-${sessionId}` });
    n.onclick = () => {
        window.focus();
        useChatStore.getState().setCurrentSession(sessionId);
    };
}

function useBattery(): BatteryState | null {
    const [battery, setBattery] = useState<BatteryState | null>(null);
    useEffect(() => {
        const nav = navigator as Navigator & { getBattery?: () => Promise<BatteryManagerLike> };
        if (!nav.getBattery) return;
        let mgr: BatteryManagerLike | null = null;
        const read = () => { if (mgr) setBattery({ charging: mgr.charging, level: mgr.level }); };
        nav.getBattery().then((m) => {
            mgr = m;
            read();
            m.addEventListener('levelchange', read);
            m.addEventListener('chargingchange', read);
        }).catch((err) => log.system.warn('Battery level unavailable; keep-awake ignores the battery policy.', err));
        return () => {
            mgr?.removeEventListener('levelchange', read);
            mgr?.removeEventListener('chargingchange', read);
        };
    }, []);
    return battery;
}

export function useChatAlerts(): void {
    useElectronNotifications();

    const running = useChatStore((s) => s.runningSessionIds);
    const awaiting = useChatStore((s) => s.awaitingBySession);
    const sessions = useChatStore((s) => s.sessions);
    const currentSessionId = useChatStore((s) => s.currentSessionId);
    const prompting = useNotificationStore(useShallow(selectPromptingSessionIds));
    const settings = useAlertSettings();
    const battery = useBattery();

    const prev = useRef<ChatPhases | null>(null);
    const phases = chatPhases(running, awaiting, prompting);
    const phasesKey = JSON.stringify(phases);

    useEffect(() => {
        const next = JSON.parse(phasesKey) as ChatPhases;
        const before = prev.current;
        prev.current = next;
        // The first snapshot is the state at launch, not news.
        if (before === null) return;
        const { sessions: list, currentSessionId: current } = useChatStore.getState();
        const focused = document.hasFocus();
        let fired = false;
        for (const { sessionId, event } of chatAlertEvents(before, next)) {
            const session = list.find((s) => s.id === sessionId);
            if (session?.kind === 'eval' || session?.kind === 'scheduled') continue;
            const mode = settings.modes[event];
            if (mode === 'off' || chatIsInView(sessionId, current, focused)) continue;
            fired = true;
            if (mode !== 'banner') continue;
            if (event !== 'finished') {
                const { title, body } = alertText(event, session);
                showBanner(sessionId, title, body);
                continue;
            }
            // The list polls every 5 s; the banner needs this turn's outcome now.
            api.getSession(sessionId)
                .then((fresh: Session) => {
                    useChatStore.getState().updateSessionInList(sessionId, fresh);
                    const { title, body } = alertText(event, fresh);
                    showBanner(sessionId, title, body);
                })
                .catch((err) => {
                    log.system.error(`Could not read how chat ${sessionId} ended`, err);
                    const { title, body } = alertText(event, { ...session, last_run_status: null } as Session);
                    showBanner(sessionId, title, body);
                });
        }
        if (!fired) return;
        if (settings.sound) {
            try {
                playChime();
            } catch (err) {
                log.system.warn('Alert sound not played', err);
            }
        }
        if (settings.flash && !focused && window.gaiaAPI) {
            window.gaiaAPI.notification.flash()
                .catch((err) => log.system.error('Could not flash the taskbar button', err));
        }
    }, [phasesKey, settings.modes, settings.sound, settings.flash]);

    const badge = attentionCount(sessions, phases, settings.modes, currentSessionId);
    useEffect(() => {
        window.gaiaAPI?.notification.setAttentionCount(badge)
            .catch((err) => log.system.error('Could not update the taskbar badge', err));
    }, [badge]);

    const keepAwake = settings.keepAwake
        && running.length > 0
        && batteryAllowsKeepAwake(battery, settings.batteryFloor);
    useEffect(() => {
        window.gaiaAPI?.power.setKeepAwake(keepAwake)
            .catch((err) => log.system.error('Could not change keep-awake', err));
    }, [keepAwake]);
}
