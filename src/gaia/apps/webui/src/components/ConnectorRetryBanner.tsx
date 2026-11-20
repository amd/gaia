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
import type { ConnectorListEntry } from '../types';
import { useT } from '../i18n/useT';
import { useConnectorPoll } from '../hooks/useConnectorPoll';
import { connectedProviderLabel, referencedProviderConnected } from '../utils/connectorRetry.core';
import './ConnectorRetryBanner.css';

export { connectedProviderLabel, referencedProviderConnected };

export function ConnectorRetryBanner({
    content,
    providers = [],
    accounts,
    onRetry,
}: {
    /** The last assistant message content (the connector-error reply). */
    content: string;
    /** The connectors that reply's Connect card offered, when it had one. */
    providers?: string[];
    /** Per provider, the account that card offered to sign in to again. */
    accounts?: Record<string, string>;
    /** Re-run the previous question. */
    onRetry: () => void;
}) {
    const t = useT();
    const [connectors, setConnectors] = useState<ConnectorListEntry[]>([]);
    // The connectors as this reply first saw them: only a change since counts as the fix.
    const [before, setBefore] = useState<ConnectorListEntry[] | null>(null);
    const [dismissed, setDismissed] = useState(false);

    const refresh = useCallback(() => {
        api.listConnectors()
            .then((data) => {
                const rows = data.connectors || [];
                setConnectors(rows);
                setBefore((prev) => prev ?? rows);
            })
            .catch((err) => log.ui.warn('ConnectorRetryBanner: connector fetch failed', err));
    }, []);

    const connected = before !== null && referencedProviderConnected(content, connectors, providers, before, accounts);

    // Re-check on mount, on window focus (returning from OAuth), and poll until connected.
    useEffect(() => {
        refresh();
        const onFocus = () => refresh();
        window.addEventListener('focus', onFocus);
        return () => window.removeEventListener('focus', onFocus);
    }, [refresh]);

    useConnectorPoll(!connected, refresh);

    if (!connected || dismissed) return null;

    const label = connectedProviderLabel(content, connectors, providers, before ?? [], accounts);

    return (
        <div className="connector-retry-banner" role="status">
            <CheckCircle2 size={14} className="connector-retry-banner__icon" />
            <span className="connector-retry-banner__text">
                {t('retryBanner.connected', { name: label })}
            </span>
            <button
                type="button"
                className="connector-retry-banner__btn"
                onClick={() => { setDismissed(true); onRetry(); }}
            >
                <RefreshCw size={12} />
                <span>{t('retryBanner.askAgain')}</span>
            </button>
            <button
                type="button"
                className="connector-retry-banner__dismiss"
                onClick={() => setDismissed(true)}
                aria-label={t('retryBanner.dismiss')}
            >
                ×
            </button>
        </div>
    );
}
