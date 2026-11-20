// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef } from 'react';

import type { GaiaNotification } from '@webui/types/agent';
import type { Schedule } from '@webui/types';

import { useActiveRuns, useSchedulesInApp } from '@/data';
import { useT } from '@/i18n/useT';
import { useAlertSettings } from '@/state/alertSettings';
import { useChatAlertBannerStore } from '@/state/chatAlertBanner';
import { addNotification } from '@/state/notifications';
import { createScheduleAnnouncer } from '@/state/scheduleAlerts';

type Translate = ReturnType<typeof useT>;

function raiseScheduleFailure(failed: Schedule, t: Translate): void {
    const title = t('schedules.failed', { name: failed.name });
    const message = failed.last_error ?? '';
    const notification: GaiaNotification = {
        id: `schedule-error-${failed.id}-${failed.error_count}`,
        type: 'error',
        agentId: 'scheduler',
        agentName: 'GAIA',
        title,
        message,
        timestamp: Date.now(),
        read: false,
        dismissed: false,
        priority: 'high',
        ...(failed.session_id ? { sessionId: failed.session_id } : {}),
    };
    addNotification(notification);
    if (failed.session_id && useAlertSettings.getState().modes.finished === 'banner') {
        useChatAlertBannerStore.getState().show({ sessionId: failed.session_id, event: 'finished', tone: 'danger', title, message });
    }
}

/**
 * The app-wide reads the web makes: the schedules and the running chats, every 30 s while the app is active.
 * A scheduled task that failed, including while the app was closed, raises one alert in the center and the banner.
 */
export function AppPolls() {
    const t = useT();
    useActiveRuns();
    const schedules = useSchedulesInApp().data?.schedules;
    const liveT = useRef(t);
    useEffect(() => {
        liveT.current = t;
    });
    const announcer = useRef<ReturnType<typeof createScheduleAnnouncer> | null>(null);
    useEffect(() => {
        if (!schedules) return;
        announcer.current ??= createScheduleAnnouncer((failed) => raiseScheduleFailure(failed, liveT.current));
        void announcer.current(schedules);
    }, [schedules]);
    return null;
}
