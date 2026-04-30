// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Settings → Connectors section (T-8b).
 *
 * Renders a tile grid of all connectors in the catalog. Clicking a tile
 * expands a detail view in-place (plan amendment A16). The detail view
 * shows an OAuth or MCP-key configure form plus per-agent grants.
 */

import { useEffect, useState, useCallback } from 'react';
import {
    CheckCircle2,
    AlertCircle,
    Loader2,
    ExternalLink,
    ChevronDown,
    ChevronUp,
    X,
} from 'lucide-react';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import type { ConnectorRow } from '../types';
import './ConnectorsSection.css';

// ── ConnectorsSection ────────────────────────────────────────────────────────

export function ConnectorsSection() {
    const [connectors, setConnectors] = useState<ConnectorRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [expanded, setExpanded] = useState<string | null>(null);

    const load = useCallback(async () => {
        try {
            const { connectors: rows } = await api.listConnectors();
            setConnectors(rows);
            setError(null);
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { void load(); }, [load]);

    const toggle = (id: string) =>
        setExpanded((prev) => (prev === id ? null : id));

    const onChanged = useCallback(async (id: string) => {
        // Refresh only the changed connector to avoid full reload.
        try {
            const row = await api.getConnector(id);
            setConnectors((prev) => prev.map((c) => (c.id === id ? row : c)));
        } catch {
            void load();
        }
    }, [load]);

    return (
        <section className="settings-section connectors-section">
            <h4>Connectors</h4>
            <p className="settings-help">
                Connect external accounts and MCP servers so agents can use them on
                your behalf. Each agent must be granted scopes individually.
            </p>

            {error && (
                <div className="error-banner">
                    <AlertCircle size={14} />
                    <span>{error}</span>
                </div>
            )}

            {loading ? (
                <div className="connectors-loading">
                    <Loader2 size={16} className="spin" />
                </div>
            ) : (
                <div className="connectors-list">
                    {connectors.map((c) => (
                        <ConnectorTile
                            key={c.id}
                            connector={c}
                            expanded={expanded === c.id}
                            onToggle={() => toggle(c.id)}
                            onChanged={() => void onChanged(c.id)}
                        />
                    ))}
                </div>
            )}
        </section>
    );
}

// ── ConnectorTile ────────────────────────────────────────────────────────────

function ConnectorTile({
    connector,
    expanded,
    onToggle,
    onChanged,
}: {
    connector: ConnectorRow;
    expanded: boolean;
    onToggle: () => void;
    onChanged: () => void;
}) {
    return (
        <div className={`connector-tile${expanded ? ' connector-tile--open' : ''}`}>
            <button
                className="connector-tile-header"
                onClick={onToggle}
                aria-expanded={expanded}
            >
                <span className="connector-tile-name">{connector.display_name}</span>
                <span className="connector-tile-type">{connector.type === 'oauth_pkce' ? 'OAuth' : 'MCP'}</span>
                {connector.configured ? (
                    <span className="connector-status ok">
                        <CheckCircle2 size={12} />
                        {connector.account_id ?? 'Configured'}
                    </span>
                ) : (
                    <span className="connector-status idle">Not configured</span>
                )}
                <span className="connector-tile-chevron">
                    {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                </span>
            </button>

            {expanded && (
                <div className="connector-detail">
                    {connector.type === 'oauth_pkce' ? (
                        <OAuthConfigureBody connector={connector} onChanged={onChanged} />
                    ) : (
                        <MCPServerConfigureBody connector={connector} onChanged={onChanged} />
                    )}
                    {connector.configured && (
                        <ConnectorAgentGrants connectorId={connector.id} />
                    )}
                </div>
            )}
        </div>
    );
}

// ── OAuthConfigureBody ───────────────────────────────────────────────────────

function OAuthConfigureBody({
    connector,
    onChanged,
}: {
    connector: ConnectorRow;
    onChanged: () => void;
}) {
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);

    // Refresh the tile when the user returns to the window after completing OAuth.
    useEffect(() => {
        const handleFocus = () => { onChanged(); };
        window.addEventListener('focus', handleFocus);
        return () => window.removeEventListener('focus', handleFocus);
    }, [onChanged]);

    const handleConnect = async () => {
        setBusy(true);
        setErr(null);
        try {
            const r = await api.authorizeConnector(
                connector.id,
                connector.default_scopes,
            );
            const anyWindow = window as unknown as {
                gaia?: { openExternal?: (url: string) => void };
            };
            if (anyWindow.gaia?.openExternal) {
                anyWindow.gaia.openExternal(r.authorization_url);
            } else {
                window.open(r.authorization_url, '_blank', 'noopener');
            }
            // onChanged is called via the 'focus' listener when the user returns.
        } catch (e) {
            setErr(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(false);
        }
    };

    const handleDisconnect = async () => {
        setBusy(true);
        setErr(null);
        try {
            await api.disconnectConnector(connector.id);
            onChanged();
        } catch (e) {
            setErr(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="configure-body">
            {connector.description && (
                <p className="connector-desc">{connector.description}</p>
            )}
            {err && (
                <div className="configure-error">
                    <AlertCircle size={12} /> {err}
                </div>
            )}
            <div className="configure-actions">
                {connector.configured ? (
                    <button
                        className="btn-secondary"
                        disabled={busy}
                        onClick={() => void handleDisconnect()}
                    >
                        {busy ? <Loader2 size={12} className="spin" /> : 'Disconnect'}
                    </button>
                ) : (
                    <button
                        className="btn-primary"
                        disabled={busy}
                        onClick={() => void handleConnect()}
                    >
                        {busy ? (
                            <Loader2 size={12} className="spin" />
                        ) : (
                            <><ExternalLink size={12} /> Connect</>
                        )}
                    </button>
                )}
                {connector.product_url && (
                    <a
                        href={connector.product_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="connector-product-link"
                    >
                        Learn more <ExternalLink size={11} />
                    </a>
                )}
            </div>
        </div>
    );
}

// ── MCPServerConfigureBody ───────────────────────────────────────────────────

function MCPServerConfigureBody({
    connector,
    onChanged,
}: {
    connector: ConnectorRow;
    onChanged: () => void;
}) {
    const [values, setValues] = useState<Record<string, string>>(() =>
        Object.fromEntries(connector.mcp_env_keys.map((k) => [k, ''])),
    );
    const [busy, setBusy] = useState(false);
    const [saved, setSaved] = useState(false);
    const [err, setErr] = useState<string | null>(null);

    // Reset inputs when the key set changes (e.g. after a server-side update).
    useEffect(() => {
        setValues(Object.fromEntries(connector.mcp_env_keys.map((k) => [k, ''])));
    }, [connector.mcp_env_keys.join(',')]); // eslint-disable-line react-hooks/exhaustive-deps

    const handleSave = async () => {
        const filled = Object.fromEntries(
            Object.entries(values).filter(([, v]) => v.trim() !== ''),
        );
        if (Object.keys(filled).length === 0) return;
        setBusy(true);
        setErr(null);
        setSaved(false);
        try {
            await api.configureConnector(connector.id, filled);
            setSaved(true);
            onChanged();
            setTimeout(() => setSaved(false), 2200);
        } catch (e) {
            setErr(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(false);
        }
    };

    const handleDisconnect = async () => {
        setBusy(true);
        setErr(null);
        try {
            await api.disconnectConnector(connector.id);
            setValues(Object.fromEntries(connector.mcp_env_keys.map((k) => [k, ''])));
            onChanged();
        } catch (e) {
            setErr(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="configure-body">
            {connector.description && (
                <p className="connector-desc">{connector.description}</p>
            )}
            {err && (
                <div className="configure-error">
                    <AlertCircle size={12} /> {err}
                </div>
            )}
            {connector.mcp_env_keys.map((key) => (
                <div key={key} className="mcp-key-row">
                    <label className="mcp-key-label">{key}</label>
                    <input
                        type="password"
                        className="mcp-key-input"
                        placeholder={connector.configured ? '••••••••' : 'Enter value'}
                        value={values[key] ?? ''}
                        onChange={(e) =>
                            setValues((prev) => ({ ...prev, [key]: e.target.value }))
                        }
                        spellCheck={false}
                        autoComplete="off"
                    />
                </div>
            ))}
            <div className="configure-actions">
                <button
                    className={`btn-model-save${saved ? ' saved' : ''}`}
                    disabled={busy || Object.values(values).every((v) => v.trim() === '')}
                    onClick={() => void handleSave()}
                >
                    {busy ? (
                        <Loader2 size={12} className="spin" />
                    ) : saved ? (
                        <><CheckCircle2 size={12} /> Saved</>
                    ) : (
                        'Save'
                    )}
                </button>
                {connector.configured && (
                    <button
                        className="btn-secondary"
                        disabled={busy}
                        onClick={() => void handleDisconnect()}
                    >
                        Disconnect
                    </button>
                )}
                {connector.product_url && (
                    <a
                        href={connector.product_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="connector-product-link"
                    >
                        Docs <ExternalLink size={11} />
                    </a>
                )}
            </div>
        </div>
    );
}

// ── ConnectorAgentGrants ─────────────────────────────────────────────────────

function ConnectorAgentGrants({ connectorId }: { connectorId: string }) {
    const { agents } = useChatStore();
    const [grants, setGrants] = useState<Record<string, string[]>>({});
    const [loading, setLoading] = useState(true);
    const [revoking, setRevoking] = useState<string | null>(null);
    const [revokeErr, setRevokeErr] = useState<string | null>(null);

    const load = useCallback(async () => {
        try {
            const { grants: g } = await api.listConnectorGrants(connectorId);
            setGrants(g);
        } catch {
            setGrants({});
        } finally {
            setLoading(false);
        }
    }, [connectorId]);

    useEffect(() => { void load(); }, [load]);

    const revoke = async (agentId: string) => {
        setRevoking(agentId);
        setRevokeErr(null);
        try {
            await api.revokeConnectorAgentGrant(connectorId, agentId);
            void load();
        } catch (e) {
            setRevokeErr(e instanceof Error ? e.message : String(e));
        } finally {
            setRevoking(null);
        }
    };

    if (loading) return null;

    return (
        <div className="connection-grants">
            <div className="grants-header">Per-agent grants</div>
            {revokeErr && (
                <div className="configure-error" style={{ marginBottom: 6 }}>
                    <AlertCircle size={12} /> {revokeErr}
                </div>
            )}
            {Object.entries(grants).length === 0 ? (
                <div className="grants-empty">No agents have been granted access yet.</div>
            ) : (
                Object.entries(grants).map(([agentId, scopes]) => {
                    const agent = agents.find((a) => a.namespaced_agent_id === agentId);
                    return (
                        <div key={agentId} className="grant-row">
                            <span className="grant-agent">{agent ? agent.name : agentId}</span>
                            <span className="grant-scopes">{scopes.join(', ')}</span>
                            <button
                                className="btn-grant-revoke"
                                disabled={revoking === agentId}
                                onClick={() => void revoke(agentId)}
                                aria-label={`Revoke ${agentId}`}
                            >
                                <X size={11} />
                            </button>
                        </div>
                    );
                })
            )}
        </div>
    );
}
