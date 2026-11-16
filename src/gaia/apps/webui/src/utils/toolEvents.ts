// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useNotificationStore } from '../stores/notificationStore';
import type { GaiaNotification } from '../types/agent';
import type { StreamEvent } from '../types';
import { getT } from '../i18n/useT';
import { knownPermissionPrompt, permissionRequestNotification, policyAlertNotification as buildPolicyAlert } from './toolEvents.core';

export { toolResultUpdates } from './toolEvents.core';

/** Raise the prompt for a `permission_request`, or bring back the one a re-attach replays. */
export function showPermissionRequest(event: StreamEvent, sessionId: string): void {
    const { addNotification, notifications } = useNotificationStore.getState();
    // A re-attach replays the run: keep an answered prompt as it is, and
    // bring back one still waiting that earlier replayed steps hid.
    const known = knownPermissionPrompt(event, notifications);
    if (known) {
        if (!known.response && known.dismissed) {
            useNotificationStore.setState((st) => ({
                notifications: st.notifications.map((n) => (n.id === known.id ? { ...n, dismissed: false } : n)),
            }));
        }
        return;
    }
    addNotification(permissionRequestNotification(event, sessionId, notifications, getT()));
}

/** The notification for a tool call that governance policy blocked. */
export function policyAlertNotification(
    event: StreamEvent,
    sessionId: string,
    fallbackId: string,
): GaiaNotification & { tool: string } {
    return buildPolicyAlert(event, sessionId, fallbackId, getT());
}
