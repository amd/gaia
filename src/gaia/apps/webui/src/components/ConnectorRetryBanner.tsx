// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * ConnectorRetryBanner (#2119, stale pre-connect replies)
 *
 * When a session's LAST assistant reply was a connector auth-required error
 * ("connect your Google account…") and the user then connects that account in
 * another tab, the stale reply used to sit there with no cue — users concluded
 * the feature was broken. This banner watches the connector state and, once the
 * referenced provider is connected, surfaces an "Ask again" affordance that
 * re-runs the last question.
 *
 * The connector state is re-checked on mount, when the window regains focus
 * (the user returning from the OAuth tab), and on a light poll until connected.
 */

import { useCallback, useEffect, useState } from 'react';
import { CheckCircle2, RefreshCw } from 'lucide-react';
import * as api from '../services/api';
import { log } from '../utils/logger';
import type { ConnectorRow } from '../types';
import { detectProvider } from './email/EmailConnectCta';
import './ConnectorRetryBanner.css';

/**
 * Given the error message and the current connector rows, decide whether the
 * provider the message referenced is now connected. Pure + exported for tests.
 *
 * ``detectProvider`` returns 'google' | 'microsoft' | 'both' (ambiguous). For
 * an ambiguous message, either provider being connected is enough to retry.
 */
export function referencedProviderConnected(
    content: string,
    connectors: ConnectorRow[],
): boolean {
    const isConfigured = (id: string) =>
        connectors.some((c) => c.id === id && c.configured);
    const provider = detectProvider(content);
    if (provider === 'google') return isConfigured('google');
    if (provider === 'microsoft') return isConfigured('microsoft');
    return isConfigured('google') || isConfigured('microsoft');
}

/** Human label for the connected provider, for the banner copy. */
function connectedProviderLabel(content: string, connectors: ConnectorRow[]): string {
    const isConfigured = (id: string) =>
        connectors.some((c) => c.id === id && c.configured);
    const provider = detectProvider(content);
    if (provider === 'microsoft' || (provider === 'both' && isConfigured('microsoft') && !isConfigured('google'))) {
        return 'Microsoft';
    }
    return 'Google';
}

const POLL_MS = 4000;

export function ConnectorRetryBanner({
    content,
    onRetry,
}: {
    /** The last assistant message content (the connector-error reply). */
    content: string;
    /** Re-run the previous question. */
    onRetry: () => void;
}) {
    const [connectors, setConnectors] = useState<ConnectorRow[]>([]);
    const [dismissed, setDismissed] = useState(false);

    const refresh = useCallback(() => {
        api.listConnectors()
            .then((data) => setConnectors(data.connectors || []))
            .catch((err) => log.ui.warn('ConnectorRetryBanner: connector fetch failed', err));
    }, []);

    const connected = referencedProviderConnected(content, connectors);

    // Re-check on mount, on window focus (returning from OAuth), and poll until
    // connected. Stop the poll once connected to avoid needless traffic.
    useEffect(() => {
        refresh();
        const onFocus = () => refresh();
        window.addEventListener('focus', onFocus);
        return () => window.removeEventListener('focus', onFocus);
    }, [refresh]);

    useEffect(() => {
        if (connected) return; // no need to keep polling once it's connected
        const timer = setInterval(refresh, POLL_MS);
        return () => clearInterval(timer);
    }, [connected, refresh]);

    if (!connected || dismissed) return null;

    const label = connectedProviderLabel(content, connectors);

    return (
        <div className="connector-retry-banner" role="status">
            <CheckCircle2 size={14} className="connector-retry-banner__icon" />
            <span className="connector-retry-banner__text">
                {label} connected since this reply — ask again to run it now.
            </span>
            <button
                type="button"
                className="connector-retry-banner__btn"
                onClick={() => { setDismissed(true); onRetry(); }}
            >
                <RefreshCw size={12} />
                <span>Ask again</span>
            </button>
            <button
                type="button"
                className="connector-retry-banner__dismiss"
                onClick={() => setDismissed(true)}
                aria-label="Dismiss"
            >
                ×
            </button>
        </div>
    );
}
