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
    Plus,
} from 'lucide-react';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import { useConnectorsSSE } from '../hooks/useConnectorsSSE';
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

    // Live updates: refresh when the backend notifies us a connector's
    // state changed. Without this the OAuth tile only refreshes via the
    // window-focus listener inside OAuthConfigureBody — which means the
    // user has to alt-tab back to the app to see the "Connected" state.
    useConnectorsSSE(
        useCallback(
            (event) => {
                if (event.connectorId) {
                    void onChanged(event.connectorId);
                } else {
                    // No connector_id in payload — fall back to a full reload.
                    void load();
                }
            },
            [onChanged, load],
        ),
    );

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
    const [setupValues, setSetupValues] = useState<Record<string, string>>({});

    // Refresh the tile when the user returns to the window after completing OAuth.
    useEffect(() => {
        const handleFocus = () => { onChanged(); };
        window.addEventListener('focus', handleFocus);
        return () => window.removeEventListener('focus', handleFocus);
    }, [onChanged]);

    // Open the OAuth URL in a real browser (Electron prefers the system
    // browser via the IPC bridge; fall back to window.open for the
    // dev-server case).
    const openAuthUrl = (url: string) => {
        const anyWindow = window as unknown as {
            gaia?: { openExternal?: (url: string) => void };
        };
        if (anyWindow.gaia?.openExternal) {
            anyWindow.gaia.openExternal(url);
        } else {
            window.open(url, '_blank', 'noopener');
        }
    };

    const handleConnect = async () => {
        setBusy(true);
        setErr(null);
        try {
            const r = await api.authorizeConnector(
                connector.id,
                connector.default_scopes,
            );
            openAuthUrl(r.authorization_url);
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

    // First-time setup: persist the OAuth client credentials, then
    // start the browser flow in one shot. The configure endpoint
    // returns {flow_id, authorization_url} once the credentials land
    // and start_authorization succeeds.
    const handleSaveAndConnect = async () => {
        const missing = (connector.oauth_setup_fields ?? [])
            .filter((f) => f.required !== false && !setupValues[f.key]?.trim())
            .map((f) => f.label);
        if (missing.length) {
            setErr(`Required: ${missing.join(', ')}`);
            return;
        }
        setBusy(true);
        setErr(null);
        try {
            const result = await api.configureConnector(connector.id, setupValues);
            const url =
                typeof result.authorization_url === 'string'
                    ? result.authorization_url
                    : null;
            if (url) {
                openAuthUrl(url);
            }
            // Catalog row will refresh via SSE / window-focus.
            onChanged();
        } catch (e) {
            setErr(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(false);
        }
    };

    const setupFields = connector.oauth_setup_fields ?? [];
    // Show the setup form when the backend says the provider can't be
    // instantiated AND the user hasn't already completed an OAuth flow
    // (a stale-but-still-configured connection should keep its
    // Disconnect button — credential rotation is a separate path).
    const showSetupForm =
        connector.configurable === false &&
        !connector.configured &&
        setupFields.length > 0;

    return (
        <div className="configure-body">
            {connector.description && (
                <p className="connector-desc">{connector.description}</p>
            )}
            {showSetupForm && (
                <div className="oauth-setup-form">
                    <p className="connector-desc">
                        First-time setup — provide your OAuth client credentials
                        below. They&rsquo;re stored encrypted in your OS keyring
                        and reused for future connections.
                    </p>
                    {setupFields.map((field) => (
                        <label key={field.key} className="oauth-setup-field">
                            <span className="oauth-setup-label">{field.label}</span>
                            <input
                                type={field.kind === 'secret' ? 'password' : 'text'}
                                className="oauth-setup-input"
                                placeholder={field.placeholder}
                                value={setupValues[field.key] ?? ''}
                                onChange={(e) =>
                                    setSetupValues((prev) => ({
                                        ...prev,
                                        [field.key]: e.target.value,
                                    }))
                                }
                                autoComplete="off"
                                spellCheck={false}
                            />
                            {field.help_md && (
                                <span className="oauth-setup-help">{field.help_md}</span>
                            )}
                        </label>
                    ))}
                </div>
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
                ) : showSetupForm ? (
                    <button
                        className="btn-primary"
                        disabled={busy}
                        onClick={() => void handleSaveAndConnect()}
                    >
                        {busy ? (
                            <Loader2 size={12} className="spin" />
                        ) : (
                            <><ExternalLink size={12} /> Save &amp; Connect</>
                        )}
                    </button>
                ) : connector.configurable === false ? (
                    <span
                        className="connector-setup-required"
                        title={connector.config_error ?? undefined}
                    >
                        Setup required
                    </span>
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
                {(connector.docs_url || connector.product_url) && (
                    <a
                        href={connector.docs_url || connector.product_url || '#'}
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
    const [granting, setGranting] = useState<string | null>(null);
    const [actionErr, setActionErr] = useState<string | null>(null);

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
        setActionErr(null);
        try {
            await api.revokeConnectorAgentGrant(connectorId, agentId);
            void load();
        } catch (e) {
            setActionErr(e instanceof Error ? e.message : String(e));
        } finally {
            setRevoking(null);
        }
    };

    const grant = async (agentId: string, scopes: string[]) => {
        setGranting(agentId);
        setActionErr(null);
        try {
            await api.grantConnectorAgent(connectorId, agentId, scopes);
            void load();
        } catch (e) {
            setActionErr(e instanceof Error ? e.message : String(e));
        } finally {
            setGranting(null);
        }
    };

    // Agents that declare a requirement for this connector but have no grant yet.
    const pendingAgents = agents.filter((a) => {
        if (!a.namespaced_agent_id) return false;
        if (a.namespaced_agent_id in grants) return false;
        return a.required_connections?.some((rc) => rc.connector_id === connectorId) ?? false;
    });

    if (loading) return null;

    const grantedEntries = Object.entries(grants);
    const hasAnything = grantedEntries.length > 0 || pendingAgents.length > 0;

    return (
        <div className="connection-grants">
            <div className="grants-header">Per-agent grants</div>
            {actionErr && (
                <div className="configure-error" style={{ marginBottom: 6 }}>
                    <AlertCircle size={12} /> {actionErr}
                </div>
            )}
            {!hasAnything && (
                <div className="grants-empty">No agents have been granted access yet.</div>
            )}
            {/* Agents that already have a grant */}
            {grantedEntries.map(([agentId, scopes]) => {
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
            })}
            {/* Agents that need a grant but don't have one yet */}
            {pendingAgents.map((agent) => {
                const agentId = agent.namespaced_agent_id!;
                const req = agent.required_connections!.find((rc) => rc.connector_id === connectorId)!;
                const busy = granting === agentId;
                return (
                    <div key={agentId} className="grant-row grant-row--pending">
                        <span className="grant-agent">{agent.name}</span>
                        <span className="grant-scopes grant-scopes--pending">
                            Needs access
                        </span>
                        <button
                            className="btn-grant-add"
                            disabled={busy}
                            onClick={() => void grant(agentId, req.scopes)}
                            aria-label={`Grant ${agent.name}`}
                            title={req.reason}
                        >
                            {busy ? <Loader2 size={11} className="spin" /> : <Plus size={11} />}
                        </button>
                    </div>
                );
            })}
        </div>
    );
}
