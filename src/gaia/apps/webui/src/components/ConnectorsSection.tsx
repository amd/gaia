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
} from 'lucide-react';

// Human-readable labels for well-known OAuth scope URIs.
// Unrecognised scopes fall back to the last path segment of the URI.
const SCOPE_LABELS: Record<string, string> = {
    // Google / Gmail
    'https://www.googleapis.com/auth/gmail.readonly':        'Read emails',
    'https://www.googleapis.com/auth/gmail.modify':          'Organize emails (archive, label, trash)',
    'https://www.googleapis.com/auth/gmail.send':            'Send emails on your behalf',
    'https://www.googleapis.com/auth/gmail.compose':         'Compose emails',
    'https://www.googleapis.com/auth/calendar.readonly':     'View calendar events',
    'https://www.googleapis.com/auth/calendar.events':       'Create & respond to calendar events',
    'https://www.googleapis.com/auth/drive.readonly':        'Read Google Drive files',
    'https://www.googleapis.com/auth/drive.file':            'Manage Drive files created by this app',
    'https://www.googleapis.com/auth/spreadsheets.readonly': 'Read Google Sheets',
    'https://www.googleapis.com/auth/spreadsheets':          'Edit Google Sheets',
    'openid':   'Identify you',
    'email':    'See your email address',
    'profile':  'See your basic profile info',
    // Microsoft Graph — email agent scopes (#1770)
    'https://graph.microsoft.com/Mail.ReadWrite':      'Read & organize Outlook mail (archive, label, trash)',
    'https://graph.microsoft.com/Mail.Send':           'Send Outlook mail on your behalf',
    'https://graph.microsoft.com/Calendars.ReadWrite': 'Create & respond to Outlook calendar events',
    'https://graph.microsoft.com/Calendars.Read':      'View Outlook calendar events',
    'https://graph.microsoft.com/User.Read':           'Read your basic Microsoft profile',
};

function scopeLabel(scope: string): string {
    return SCOPE_LABELS[scope] ?? scope.split(/[/.:]/).pop() ?? scope;
}
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import { useConnectorsSSE } from '../hooks/useConnectorsSSE';
import type { AgentMcpServer, ConnectorRow } from '../types';
import { ConnectorTileMenu } from './ConnectorTileMenu';
import './ConnectorsSection.css';

// Canonical scope for MCP-server grants. Agents that consume MCP servers
// dynamically declare no scopes of their own, so one-click activation
// auto-grants this when no prior grant exists.
const MCP_DEFAULT_GRANT_SCOPES = ['use'];

// ── ConnectorsSection ────────────────────────────────────────────────────────

export function ConnectorsSection() {
    const [connectors, setConnectors] = useState<ConnectorRow[]>([]);
    const [agentMcps, setAgentMcps] = useState<AgentMcpServer[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [expanded, setExpanded] = useState<string | null>(null);

    const load = useCallback(async () => {
        try {
            const [{ connectors: rows }, { agent_mcps: mcps }] = await Promise.all([
                api.listConnectors(),
                api.listAgentMcps(),
            ]);
            setConnectors(rows);
            setAgentMcps(mcps);
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
                <>
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
                    {agentMcps.length > 0 && (
                        <AgentMcpsSection
                            servers={agentMcps}
                            expanded={expanded}
                            onToggle={toggle}
                        />
                    )}
                </>
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
    // Three exclusive status states for the tile header pill (#1004):
    //   - configured + enabled   → "Configured" / account_id (green ok)
    //   - configured + disabled  → "Disabled"               (warm muted)
    //   - not configured         → "Not configured"         (cool muted)
    const renderStatus = () => {
        if (!connector.configured) {
            return <span className="connector-status idle">Not configured</span>;
        }
        if (connector.type === 'mcp_server' && connector.enabled === false) {
            return <span className="connector-status disabled">Disabled</span>;
        }
        return (
            <span className="connector-status ok">
                <CheckCircle2 size={12} />
                {connector.account_id ?? 'Configured'}
            </span>
        );
    };

    return (
        <div className={`connector-tile${expanded ? ' connector-tile--open' : ''}`}>
            <button
                className="connector-tile-header"
                onClick={onToggle}
                aria-expanded={expanded}
            >
                <span className="connector-tile-name">{connector.display_name}</span>
                <span className="connector-tile-type">{connector.type === 'oauth_pkce' ? 'OAuth' : 'MCP'}</span>
                {renderStatus()}
                <ConnectorTileMenu connector={connector} onChanged={onChanged} />
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
                        <ConnectorAgentGrants
                            connectorId={connector.id}
                            connectorType={connector.type}
                        />
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

    // #2117 — agents that declare this connector as a requirement. Connecting
    // grants the selected agents in the same flow (default-on), so a mailbox
    // connected here is usable immediately instead of hitting a "no grant"
    // dead end that only a CLI command could fix.
    const { agents } = useChatStore();
    const grantableAgents = agents.filter(
        (a) =>
            a.namespaced_agent_id &&
            a.required_connections?.some((rc) => rc.connector_id === connector.id),
    );
    const grantableKey = grantableAgents
        .map((a) => a.namespaced_agent_id)
        .join(',');
    const [grantSel, setGrantSel] = useState<Set<string>>(() => new Set());
    // Default every declaring agent ON once the agent list resolves.
    useEffect(() => {
        setGrantSel(new Set(grantableAgents.map((a) => a.namespaced_agent_id!)));
    }, [grantableKey]); // eslint-disable-line react-hooks/exhaustive-deps
    const toggleGrant = (nsid: string) =>
        setGrantSel((prev) => {
            const next = new Set(prev);
            next.has(nsid) ? next.delete(nsid) : next.add(nsid);
            return next;
        });
    const selectedGrantAgents = () => [...grantSel];

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
                connector.available_scopes?.length
                    ? connector.available_scopes
                    : connector.default_scopes,
                selectedGrantAgents(),
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
            const scopes = connector.available_scopes?.length
                ? connector.available_scopes
                : connector.default_scopes;
            const result = await api.configureConnector(connector.id, {
                ...setupValues,
                scopes,
                grant_agents: selectedGrantAgents(),
            });
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
            {!connector.configured && grantableAgents.length > 0 && (
                <div className="oauth-grant-onconnect">
                    <p className="oauth-grant-onconnect-label">
                        Grant access to these agents when you connect:
                    </p>
                    {grantableAgents.map((a) => (
                        <label
                            key={a.namespaced_agent_id}
                            className="oauth-grant-onconnect-item"
                        >
                            <input
                                type="checkbox"
                                checked={grantSel.has(a.namespaced_agent_id!)}
                                onChange={() => toggleGrant(a.namespaced_agent_id!)}
                            />
                            <span>{a.name}</span>
                        </label>
                    ))}
                    <p className="oauth-grant-onconnect-help">
                        You can change these any time under Per-agent grants below.
                    </p>
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

    // Per-MCP enable/disable toggle (#1004). The toggle row appears only
    // for already-configured MCP connectors — toggling on a never-
    // configured connector has no meaning. Detail-view toggle and the
    // tile-header overflow menu both call the same enableConnector /
    // disableConnector helpers, so the SSE refresh keeps both surfaces
    // in sync.
    const handleToggleEnabled = async () => {
        setBusy(true);
        setErr(null);
        try {
            if (connector.enabled) {
                await api.disableConnector(connector.id);
            } else {
                await api.enableConnector(connector.id);
            }
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
            {connector.configured && (
                <div className="connector-toggle-row">
                    <span className="connector-toggle-label">Active</span>
                    <span className="toggle-switch">
                        <input
                            type="checkbox"
                            checked={connector.enabled !== false}
                            onChange={() => void handleToggleEnabled()}
                            disabled={busy}
                            aria-label={
                                connector.enabled
                                    ? 'Disable this MCP server'
                                    : 'Enable this MCP server'
                            }
                        />
                        <span className="toggle-track" />
                    </span>
                </div>
            )}
            {connector.configured && connector.enabled === false && (
                <p className="connector-toggle-help">
                    Credentials and per-agent grants are preserved.
                    Re-enable to make this server's tools available again.
                </p>
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

/**
 * Unified scope-toggle card for one agent (granted or not).
 * Toggling a scope auto-saves immediately — no explicit button needed.
 * Granted scopes start ON; not-yet-granted scopes start OFF.
 */
function AgentGrantCard({
    agent,
    connectorId,
    grantedScopes,
    onChanged,
}: {
    agent: { namespaced_agent_id?: string; name: string; required_connections?: Array<{ connector_id: string; scopes: string[]; reason: string }> };
    connectorId: string;
    grantedScopes: string[];
    onChanged: () => void;
}) {
    const req = agent.required_connections?.find((rc) => rc.connector_id === connectorId);
    const agentId = agent.namespaced_agent_id;
    if (!req || !agentId) return null;

    const [localScopes, setLocalScopes] = useState<Set<string>>(() => new Set(grantedScopes));
    const [busyScope, setBusyScope] = useState<string | null>(null);
    const [err, setErr] = useState<string | null>(null);

    // Sync when the parent reloads grants after a successful API call.
    useEffect(() => {
        setLocalScopes(new Set(grantedScopes));
    }, [grantedScopes.join(',')]); // eslint-disable-line react-hooks/exhaustive-deps

    const toggleScope = async (scope: string) => {
        if (busyScope !== null) return; // one request at a time
        const next = new Set(localScopes);
        next.has(scope) ? next.delete(scope) : next.add(scope);
        setLocalScopes(next); // optimistic
        setBusyScope(scope);
        setErr(null);
        try {
            if (next.size === 0) {
                await api.revokeConnectorAgentGrant(connectorId, agentId);
            } else {
                await api.grantConnectorAgent(connectorId, agentId, [...next]);
            }
            onChanged();
        } catch (e) {
            setLocalScopes(new Set(grantedScopes)); // revert
            setErr(e instanceof Error ? e.message : String(e));
        } finally {
            setBusyScope(null);
        }
    };

    return (
        <div className="grant-agent-card">
            <div className="grant-agent-card-name">{agent.name}</div>
            <div className="grant-scope-list">
                {req.scopes.map((scope) => (
                    <label key={scope} className="grant-scope-item">
                        <span className="grant-scope-label">{scopeLabel(scope)}</span>
                        <span className="toggle-switch">
                            <input
                                type="checkbox"
                                checked={localScopes.has(scope)}
                                onChange={() => void toggleScope(scope)}
                                disabled={busyScope !== null}
                            />
                            <span className="toggle-track" />
                        </span>
                    </label>
                ))}
            </div>
            {err && (
                <div className="grant-scope-warning grant-scope-warning--error">
                    <AlertCircle size={11} /> {err}
                </div>
            )}
        </div>
    );
}

// ── AgentMcpsSection ─────────────────────────────────────────────────────────

/**
 * Read-only section listing MCP servers declared by custom Python agents
 * (#1020). These are controlled by the agent's local mcp_servers.json;
 * the user edits that file directly — no toggle or disconnect action here.
 */
function AgentMcpsSection({
    servers,
    expanded,
    onToggle,
}: {
    servers: AgentMcpServer[];
    expanded: string | null;
    onToggle: (key: string) => void;
}) {
    return (
        <div className="agent-mcps-section">
            <div className="agent-mcps-section-header">Custom agent servers</div>
            <div className="connectors-list">
                {servers.map((s) => {
                    const key = `${s.agent_id}::${s.server_name}`;
                    return (
                        <AgentMcpTile
                            key={key}
                            server={s}
                            expanded={expanded === key}
                            onToggle={() => onToggle(key)}
                        />
                    );
                })}
            </div>
        </div>
    );
}

function AgentMcpTile({
    server,
    expanded,
    onToggle,
}: {
    server: AgentMcpServer;
    expanded: boolean;
    onToggle: () => void;
}) {
    const cmdDisplay = [server.command, ...server.args].join(' ');

    return (
        <div className={`agent-mcp-tile${expanded ? ' agent-mcp-tile--open' : ''}`}>
            <button
                type="button"
                className="agent-mcp-tile-header"
                onClick={onToggle}
                aria-expanded={expanded}
            >
                <span className="agent-mcp-server-name">{server.server_name}</span>
                <span className="agent-mcp-agent-label">via {server.agent_name}</span>
                {server.disabled ? (
                    <span className="agent-mcp-status disabled">Disabled</span>
                ) : (
                    <span className="agent-mcp-status enabled">
                        <CheckCircle2 size={11} /> Active
                    </span>
                )}
                <span className="agent-mcp-readonly-badge">read-only</span>
                <span className="agent-mcp-chevron">
                    {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                </span>
            </button>

            {expanded && (
                <div className="agent-mcp-detail">
                    {cmdDisplay && (
                        <div className="agent-mcp-command-row">{cmdDisplay}</div>
                    )}
                    <div className="agent-mcp-config-path">{server.config_path}</div>
                    <div className="agent-mcp-config-hint">
                        Edit the agent's <code>mcp_servers.json</code> to change this server.
                    </div>
                </div>
            )}
        </div>
    );
}

// ── ConnectorAgentGrants ─────────────────────────────────────────────────────

function ConnectorAgentGrants({
    connectorId,
    connectorType,
}: {
    connectorId: string;
    connectorType: string;
}) {
    const { agents } = useChatStore();
    const [grants, setGrants] = useState<Record<string, string[]>>({});
    const [activations, setActivations] = useState<Record<string, boolean>>({});
    const [loading, setLoading] = useState(true);

    const load = useCallback(async () => {
        try {
            const [{ grants: g }, { activations: acts }] = await Promise.all([
                api.listConnectorGrants(connectorId),
                api.listConnectorActivations(connectorId),
            ]);
            setGrants(g);
            setActivations(acts);
        } catch {
            setGrants({});
            setActivations({});
        } finally {
            setLoading(false);
        }
    }, [connectorId]);

    useEffect(() => { void load(); }, [load]);

    // Live updates: when grant or activation changes (either through this UI
    // or another caller — CLI, SDK), pick the fresh state up.
    useConnectorsSSE(
        useCallback(
            (event) => {
                if (
                    event.connectorId === connectorId &&
                    (event.reason === 'grant_changed' ||
                        event.reason === 'activation_changed' ||
                        event.reason === 'disconnected')
                ) {
                    void load();
                }
            },
            [connectorId, load],
        ),
    );

    if (loading) return null;

    // Every agent that declares a requirement for this connector — granted or not.
    // Drives the per-agent (credential) grants section below.
    const relevantAgents = agents.filter(
        (a) => a.namespaced_agent_id && a.required_connections?.some((rc) => rc.connector_id === connectorId),
    );

    // Activation eligibility is wider than grant eligibility: for MCP-server
    // connectors, agents that load MCP servers dynamically (consumes_mcp_servers)
    // can use this connector's tools once activated even without a static
    // requirement declaration. OAuth connectors have no MCP tool surface, so
    // there are no activatable agents for them.
    const activatableAgents =
        connectorType === 'mcp_server'
            ? agents.filter(
                  (a) =>
                      a.namespaced_agent_id &&
                      (a.consumes_mcp_servers ||
                          a.required_connections?.some((rc) => rc.connector_id === connectorId)),
              )
            : [];

    return (
        <div className="connection-grants">
            <div className="grants-header">Per-agent grants</div>
            {relevantAgents.length === 0 ? (
                <div className="grants-empty">No agents require access to this connector.</div>
            ) : (
                relevantAgents.map((agent) => (
                    <AgentGrantCard
                        key={agent.namespaced_agent_id}
                        agent={agent}
                        connectorId={connectorId}
                        grantedScopes={grants[agent.namespaced_agent_id!] ?? []}
                        onChanged={() => void load()}
                    />
                ))
            )}

            {/* Activations gate MCP tool visibility. OAuth connectors have no
                MCP tool surface — their per-agent access is governed by the
                per-scope grant toggles above — so showing this block for them
                would be a switch that does nothing. (issue #1005) */}
            {activatableAgents.length > 0 && (
                <>
                    <div className="grants-header grants-header--activations">
                        Active for
                    </div>
                    <div className="grants-help">
                        Activations gate which agents see this connector's tools.
                        Activating without a prior grant auto-creates one.
                    </div>
                    {activatableAgents.map((agent) => (
                        <AgentActivationCard
                            key={`activation-${agent.namespaced_agent_id}`}
                            agent={agent}
                            connectorId={connectorId}
                            active={Boolean(activations[agent.namespaced_agent_id!])}
                            grantedScopes={grants[agent.namespaced_agent_id!] ?? []}
                            onChanged={() => void load()}
                        />
                    ))}
                </>
            )}
        </div>
    );
}

// ── AgentActivationCard ──────────────────────────────────────────────────────

/**
 * One-row activation toggle for a single agent (issue #1005).
 *
 * Activation gates MCP tool visibility: when ON, the MCP server's tools
 * appear in the agent's prompt; when OFF (or absent), the tools are hidden
 * even if the agent holds a grant. Activating without a prior grant
 * auto-creates one using the agent's declared REQUIRED_CONNECTORS scopes, or
 * the canonical MCP scope for agents that consume MCP servers dynamically and
 * declare no static requirement.
 *
 * Rendered only for ``type === 'mcp_server'`` connectors — the parent
 * (``ConnectorAgentGrants``) gates the entire ``Active for`` block on
 * connector type because activations apply to MCP servers only.
 */
function AgentActivationCard({
    agent,
    connectorId,
    active,
    grantedScopes,
    onChanged,
}: {
    agent: {
        namespaced_agent_id?: string;
        name: string;
        required_connections?: Array<{ connector_id: string; scopes: string[]; reason: string }>;
    };
    connectorId: string;
    active: boolean;
    grantedScopes: string[];
    onChanged: () => void;
}) {
    const req = agent.required_connections?.find((rc) => rc.connector_id === connectorId);
    const agentId = agent.namespaced_agent_id;
    // A dynamic MCP consumer has no ``req`` for this connector but is still
    // activatable — only the namespaced id is required.
    if (!agentId) return null;

    const [localActive, setLocalActive] = useState(active);
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);

    useEffect(() => {
        setLocalActive(active);
    }, [active]);

    const toggle = async () => {
        if (busy) return;
        const next = !localActive;
        setLocalActive(next); // optimistic
        setBusy(true);
        setErr(null);
        try {
            if (next) {
                // Auto-grant only when no grant is in place yet (the server
                // ignores the body when a grant already exists). Use the
                // agent's declared REQUIRED_CONNECTORS scopes, falling back to
                // the canonical MCP scope for dynamic consumers that declare none.
                const scopesForGrant =
                    grantedScopes.length === 0
                        ? req
                            ? [...req.scopes]
                            : [...MCP_DEFAULT_GRANT_SCOPES]
                        : undefined;
                await api.activateConnectorAgent(connectorId, agentId, scopesForGrant);
            } else {
                await api.deactivateConnectorAgent(connectorId, agentId);
            }
            onChanged();
        } catch (e) {
            setLocalActive(active); // revert
            setErr(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="grant-agent-card">
            <div className="grant-agent-card-name">{agent.name}</div>
            <div className="grant-scope-list">
                <label className="grant-scope-item">
                    <span className="grant-scope-label">
                        {localActive ? 'Tools visible to this agent' : 'Tools hidden from this agent'}
                    </span>
                    <span className="toggle-switch">
                        <input
                            type="checkbox"
                            checked={localActive}
                            onChange={() => void toggle()}
                            disabled={busy}
                        />
                        <span className="toggle-track" />
                    </span>
                </label>
            </div>
            {err && (
                <div className="grant-scope-warning grant-scope-warning--error">
                    <AlertCircle size={11} /> {err}
                </div>
            )}
        </div>
    );
}
