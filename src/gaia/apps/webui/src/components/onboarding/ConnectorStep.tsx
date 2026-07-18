// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { CheckCircle2, Link2, Loader2, XCircle } from 'lucide-react';
import * as api from '../../services/api';
import { log } from '../../utils/logger';
import type { ConnectorRow } from '../../types';

interface ConnectorStepProps {
    /**
     * Connector id to offer on first run. Defaults to Google — the connector
     * the email agent declares as required (``REQUIRED_CONNECTORS``). Optional
     * step: the wizard never guards it.
     */
    connectorId?: string;
}

const POLL_MS = 2000;

/**
 * Step 4 — offer to connect an app before first run (#1727, "connector-on-
 * install"). Reuses the existing OAuth flow (``authorizeConnector`` +
 * ``getConnector`` polling); it does NOT reimplement grant logic — that lives
 * in the connectors framework (see #2117/#2118). If the richer connect→grant
 * flow lands on main, this step can call into it directly; the plain authorize
 * path here is the interface available today.
 */
export function ConnectorStep({ connectorId = 'google' }: ConnectorStepProps) {
    const [connector, setConnector] = useState<ConnectorRow | null>(null);
    const [loading, setLoading] = useState(true);
    const [connecting, setConnecting] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

    const stopPolling = useCallback(() => {
        if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    }, []);

    useEffect(() => {
        let cancelled = false;
        api.listConnectors()
            .then(({ connectors }) => {
                if (cancelled) return;
                setConnector(connectors.find((c) => c.id === connectorId) ?? null);
            })
            .catch((err) => {
                log.system.warn('Onboarding: failed to list connectors', err);
                if (!cancelled) setError('Could not load connectors.');
            })
            .finally(() => { if (!cancelled) setLoading(false); });
        return () => { cancelled = true; stopPolling(); };
    }, [connectorId, stopPolling]);

    const openExternal = (url: string) => {
        const anyWindow = window as unknown as { gaia?: { openExternal?: (url: string) => void } };
        if (anyWindow.gaia?.openExternal) anyWindow.gaia.openExternal(url);
        else window.open(url, '_blank', 'noopener');
    };

    const pollUntilConfigured = useCallback(() => {
        stopPolling();
        pollRef.current = setInterval(async () => {
            try {
                const row = await api.getConnector(connectorId);
                setConnector(row);
                if (row.configured) {
                    setConnecting(false);
                    stopPolling();
                }
            } catch (err) {
                log.system.warn('Onboarding: connector poll failed', err);
            }
        }, POLL_MS);
    }, [connectorId, stopPolling]);

    const connect = useCallback(async () => {
        if (!connector) return;
        setConnecting(true);
        setError(null);
        try {
            const scopes = connector.default_scopes?.length ? connector.default_scopes : connector.available_scopes;
            const { authorization_url } = await api.authorizeConnector(connectorId, scopes ?? []);
            openExternal(authorization_url);
            pollUntilConfigured();
        } catch (err) {
            const message = err instanceof Error ? err.message : 'Failed to start the connection.';
            log.system.error('Onboarding: connect failed', err);
            setError(message);
            setConnecting(false);
        }
    }, [connector, connectorId, pollUntilConfigured]);

    if (loading) {
        return (
            <div className="onboarding-body" data-testid="onboarding-connector-loading">
                <h2>Connect an app</h2>
                <p className="lede" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Loader2 size={16} className="onboarding-spin" /> Loading…
                </p>
            </div>
        );
    }

    // No such connector available (not configured on this build) — nothing to
    // offer, so the step is a no-op the user simply continues past.
    if (!connector) {
        return (
            <div className="onboarding-body" data-testid="onboarding-connector-none">
                <h2>Connect an app</h2>
                <p className="lede">
                    No apps to connect right now. You can add connectors any time from
                    Settings → Connectors.
                </p>
            </div>
        );
    }

    return (
        <div className="onboarding-body" data-testid="onboarding-connector">
            <h2>Connect an app <span style={{ color: 'var(--text-muted)', fontWeight: 400, fontSize: '0.9rem' }}>(optional)</span></h2>
            <p className="lede">
                Some agents (like Email triage) work with your accounts. Connect now, or skip
                and do it later from Settings.
            </p>

            <div className="connector-choice">
                <span className="cc-icon"><Link2 size={20} /></span>
                <div className="cc-body">
                    <div className="cc-title">{connector.display_name}</div>
                    <div className="cc-desc">{connector.description}</div>
                </div>
                {connector.configured ? (
                    <span className="cc-connected" data-testid="connector-connected">
                        <CheckCircle2 size={15} /> Connected
                    </span>
                ) : (
                    <button
                        className="onboarding-btn primary"
                        onClick={connect}
                        disabled={connecting || !connector.configurable}
                        data-testid="connector-connect"
                    >
                        {connecting ? <Loader2 size={15} className="onboarding-spin" /> : <Link2 size={15} />}
                        {connecting ? 'Waiting…' : 'Connect'}
                    </button>
                )}
            </div>

            {!connector.configurable && connector.config_error && (
                <div className="onboarding-banner warn">
                    <XCircle size={18} />
                    <div>{connector.config_error}</div>
                </div>
            )}

            {error && (
                <div className="onboarding-banner error" role="alert" data-testid="connector-error">
                    <XCircle size={18} />
                    <div>{error}</div>
                </div>
            )}
        </div>
    );
}
