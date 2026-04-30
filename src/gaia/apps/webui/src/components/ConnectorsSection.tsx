// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Issue #915 — Settings → Connections section.
 *
 * Lists OAuth providers (currently just Google), shows connect/disconnect
 * buttons, and enumerates per-agent grants for each provider. The connect
 * flow opens the system browser; the SSE hook (useConnectorsSSE) updates
 * UI within ~2 seconds of the user finishing OAuth.
 */

import { useEffect, useState } from 'react';
import { CheckCircle2, AlertCircle, Loader2, ExternalLink } from 'lucide-react';
import * as api from '../services/api';
import { useConnectionsStore } from '../stores/connectorsStore';
import { useConnectorsSSE } from '../hooks/useConnectorsSSE';
import { useChatStore } from '../stores/chatStore';

const PROVIDERS = [
    {
        id: 'google',
        name: 'Google',
        // The default scopes for the connect button — agents can request
        // narrower or wider scopes via REQUIRED_CONNECTORS, and re-connect
        // adds those if missing.
        defaultScopes: [
            'openid',
            'https://www.googleapis.com/auth/userinfo.email',
        ],
    },
];

export function ConnectorsSection() {
    const { connections, grants, refresh, error } = useConnectionsStore();
    const { agents } = useChatStore();
    const [busy, setBusy] = useState<string | null>(null);

    useConnectorsSSE();

    useEffect(() => {
        void refresh();
    }, [refresh]);

    const handleConnect = async (provider: string, scopes: string[]) => {
        setBusy(provider);
        try {
            const r = await api.authorizeConnection(provider, scopes);
            // Open the system browser. The Electron preload exposes
            // window.gaia?.openExternal for native shell open; fall back
            // to window.open in dev/web.
            const anyWindow = window as unknown as {
                gaia?: { openExternal?: (url: string) => void };
            };
            if (anyWindow.gaia?.openExternal) {
                anyWindow.gaia.openExternal(r.authorization_url);
            } else {
                window.open(r.authorization_url, '_blank', 'noopener');
            }
            // SSE event connection.connected will refresh the store.
        } finally {
            setBusy(null);
        }
    };

    const handleDisconnect = async (provider: string) => {
        setBusy(provider);
        try {
            await api.revokeConnection(provider);
            await refresh();
        } finally {
            setBusy(null);
        }
    };

    return (
        <section className="settings-section connections-section">
            <h4>Connections</h4>
            <p className="settings-help">
                Connect external accounts so agents can access them on your
                behalf. Each agent must be granted scopes individually.
            </p>

            {error && (
                <div className="error-banner">
                    <AlertCircle size={14} />
                    <span>{error}</span>
                </div>
            )}

            {PROVIDERS.map((p) => {
                const conn = connections.find((c) => c.provider === p.id);
                const providerGrants = grants[p.id] ?? {};
                const isBusy = busy === p.id;
                return (
                    <div key={p.id} className="connection-row">
                        <div className="connection-row-header">
                            <span className="connection-name">{p.name}</span>
                            {conn ? (
                                <span className="connection-status ok">
                                    <CheckCircle2 size={12} />
                                    {conn.account_email || 'Connected'}
                                </span>
                            ) : (
                                <span className="connection-status idle">
                                    Not connected
                                </span>
                            )}
                            <div className="connection-actions">
                                {conn ? (
                                    <button
                                        className="btn-secondary"
                                        disabled={isBusy}
                                        onClick={() => void handleDisconnect(p.id)}
                                    >
                                        {isBusy ? <Loader2 size={12} className="spin" /> : 'Disconnect'}
                                    </button>
                                ) : (
                                    <button
                                        className="btn-primary"
                                        disabled={isBusy}
                                        onClick={() =>
                                            void handleConnect(p.id, p.defaultScopes)
                                        }
                                    >
                                        {isBusy ? (
                                            <Loader2 size={12} className="spin" />
                                        ) : (
                                            <>
                                                Connect <ExternalLink size={12} />
                                            </>
                                        )}
                                    </button>
                                )}
                            </div>
                        </div>

                        {conn && (
                            <div className="connection-grants">
                                <div className="grants-header">
                                    Per-agent grants
                                </div>
                                {Object.entries(providerGrants).length === 0 ? (
                                    <div className="grants-empty">
                                        No agents have been granted access yet.
                                    </div>
                                ) : (
                                    Object.entries(providerGrants).map(
                                        ([agentId, scopes]) => {
                                            const agent = agents.find(
                                                (a) =>
                                                    a.namespaced_agent_id === agentId,
                                            );
                                            return (
                                                <div
                                                    key={agentId}
                                                    className="grant-row"
                                                >
                                                    <span className="grant-agent">
                                                        {agent
                                                            ? agent.name
                                                            : agentId}
                                                    </span>
                                                    <span className="grant-scopes">
                                                        {scopes.join(', ')}
                                                    </span>
                                                    <button
                                                        className="btn-link"
                                                        onClick={() =>
                                                            void api
                                                                .revokeAgentGrant(
                                                                    p.id,
                                                                    agentId,
                                                                )
                                                                .then(refresh)
                                                        }
                                                    >
                                                        Revoke
                                                    </button>
                                                </div>
                                            );
                                        },
                                    )
                                )}
                            </div>
                        )}
                    </div>
                );
            })}
        </section>
    );
}
