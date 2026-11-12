// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * An open phone alert, at the top of the main area. Polls only while the app is
 * visible, and stays silent when phone is off, the background service can't be
 * reached, or nothing is open. Dismiss marks the alerts handled, which stops
 * their texts and calls; it works from the phone too.
 */

import { useCallback, useEffect, useState } from 'react';
import { useT } from '../i18n/useT';
import { getPhoneAlerts, markAlertHandled, phoneFailure, pollPhoneStatus, type PhoneAlert } from '../services/phoneApi';
import { useChatStore } from '../stores/chatStore';
import { log } from '../utils/logger';
import { setVisibleInterval } from '../utils/visibleInterval';
import { alertTitle, phoneErrorText } from './settings/phoneText';
import { focusPhoneAlertsNext } from './settings/PhoneSettings';

export const PHONE_BANNER_POLL_MS = 15_000;

let lastFailure: string | null = null;

export function PhoneAlertBanner() {
    const t = useT();
    const openSettings = useChatStore((s) => s.openSettings);
    const [open, setOpen] = useState<PhoneAlert[]>([]);
    const [dismissError, setDismissError] = useState<string | null>(null);

    const poll = useCallback(async () => {
        try {
            const status = await pollPhoneStatus();
            if (!status || !status.enabled || status.open_alerts === 0) {
                setOpen([]);
                return;
            }
            const { alerts } = await getPhoneAlerts(true);
            lastFailure = null;
            setOpen(alerts.filter((a) => a.state === 'open'));
        } catch (err) {
            // Shown nowhere else, so logged once per failure kind rather than on every poll.
            const f = phoneFailure(err);
            if (f.code !== lastFailure) log.ui.warn(`Phone alert poll failed (${f.code}); no banner until it recovers`, f.message);
            lastFailure = f.code;
            setOpen([]);
            return;
        }
    }, []);

    useEffect(() => {
        if (document.visibilityState !== 'hidden') void poll();
        return setVisibleInterval(() => void poll(), PHONE_BANNER_POLL_MS);
    }, [poll]);

    if (open.length === 0) return null;
    const [first, ...rest] = open;

    const dismiss = async () => {
        setDismissError(null);
        try {
            await Promise.all(open.map((a) => markAlertHandled(a.id)));
            setOpen([]);
        } catch (err) {
            setDismissError(phoneErrorText(phoneFailure(err), t));
        }
        void poll();
    };

    return (
        <div className="app-notice is-warning phone-banner" role="alert">
            <span>
                <span className="phone-banner-title">{alertTitle(first.kind, t)}</span>
                {rest.length > 0 && <> <span className="phone-banner-more">({t.bare('phone.banner.more', { n: rest.length })})</span></>}
                {dismissError && <> <span className="phone-banner-more">{dismissError}</span></>}
            </span>
            <button type="button" className="link-btn" onClick={() => { focusPhoneAlertsNext(); openSettings('phone'); }}>
                {t('phone.banner.open')}
            </button>
            <button type="button" className="link-btn" onClick={() => void dismiss()}>
                {t('phone.banner.dismiss')}
            </button>
        </div>
    );
}
