// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { usePathname } from 'expo-router';
import { useEffect, useRef } from 'react';
import { AppState } from 'react-native';

import { alertText, chatIsInView } from '@webui/utils/chatAlerts.core';
import type { Session } from '@webui/types';

import { useSessions } from '@/data';
import { activeChatId } from '@/features/nav/navigation';
import { useT } from '@/i18n/useT';
import { playAlertSound } from '@/platform/alertSound';
import { useAlertSettings } from '@/state/alertSettings';
import { useChatAlertBannerStore } from '@/state/chatAlertBanner';
import { useChatStreamStore } from '@/state/chatStreams';

import { alertsBetween, FINISHED_ALERT_MS, turnPhases } from './chatAlerts';

/** Raises a banner in the app's one banner slot when a chat you aren't looking at finishes or needs you, as the Notifications settings say. */
export function ChatAlertFeed() {
    const t = useT();
    const pathname = usePathname();
    const sessions = useSessions().data?.sessions;
    const live = useRef({ t, pathname, sessions });
    useEffect(() => {
        live.current = { t, pathname, sessions };
    });
    const openId = activeChatId(pathname);

    // Looking at the chat is the answer to its banner.
    useEffect(() => {
        const { alert, dismiss } = useChatAlertBannerStore.getState();
        if (alert && alert.sessionId === openId) dismiss();
    }, [openId]);

    // A finished chat needs no answer, so its banner leaves on its own; approvals and questions stay until acted on.
    const shownAlert = useChatAlertBannerStore((s) => s.alert);
    useEffect(() => {
        if (!shownAlert || shownAlert.event !== 'finished') return;
        const timer = setTimeout(() => {
            if (useChatAlertBannerStore.getState().alert === shownAlert) useChatAlertBannerStore.getState().dismiss();
        }, FINISHED_ALERT_MS);
        return () => clearTimeout(timer);
    }, [shownAlert]);

    useEffect(
        () =>
            useChatStreamStore.subscribe((state, prev) => {
                if (state.turns === prev.turns) return;
                const { t, pathname, sessions } = live.current;
                const banner = useChatAlertBannerStore.getState();
                const phases = turnPhases(state.turns);
                const shown = banner.alert;
                // An approval or question answered elsewhere no longer needs the banner.
                if (shown && shown.event !== 'finished' && phases[shown.sessionId] !== (shown.event === 'needs_approval' ? 'permission' : 'question')) banner.dismiss();

                const settings = useAlertSettings.getState();
                const current = activeChatId(pathname);
                const focused = AppState.currentState === 'active';
                let fired = false;
                for (const { sessionId, event, outcome } of alertsBetween(prev.turns, state.turns)) {
                    const session = sessions?.find((s) => s.id === sessionId);
                    if (session?.kind === 'eval' || session?.kind === 'scheduled') continue;
                    const mode = settings.modes[event];
                    if (mode === 'off' || chatIsInView(sessionId, current, focused)) continue;
                    fired = true;
                    if (mode !== 'banner') continue;
                    const { title, body } = alertText(event, { title: session?.title, last_run_status: outcome } as Session, t);
                    const tone = event === 'finished' ? (outcome === 'done' ? 'success' : outcome === 'failed' ? 'danger' : 'info') : 'warning';
                    banner.show({ sessionId, event, tone, title, message: body });
                }
                if (fired && settings.sound) playAlertSound();
            }),
        [],
    );
    return null;
}
