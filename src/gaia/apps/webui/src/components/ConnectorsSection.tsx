// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Settings → Connectors section (T-8b).
 *
 * Renders a tile grid of all connectors in the catalog. Clicking a tile
 * expands a detail view in-place (plan amendment A16). The detail view
 * shows an OAuth or MCP-key configure form plus per-agent grants.
 */

import ReactMarkdown from 'react-markdown';
import { useEffect, useState, useCallback, useRef } from 'react';
import {
    CheckCircle2,
    AlertCircle,
    Loader2,
    ExternalLink,
    ChevronDown,
    ChevronUp,
} from 'lucide-react';

import * as api from '../services/api';
import { SAFE_DISALLOWED_ELEMENTS, safeUrlTransform } from '../utils/markdown';
import { useChatStore } from '../stores/chatStore';
import { useConnectorsSSE } from '../hooks/useConnectorsSSE';
import { ref, type MessageKey } from '../i18n/registry';
import { translator, useT, type Translate } from '../i18n/useT';
import type { AgentMcpServer, ConnectorListEntry, ConnectorRow, UnavailableConnectorRow } from '../types';
import { connectorDescription, connectorDescriptionBare, fieldHelp, fieldLabel, missingFieldsMessage } from '../utils/connectorText';
import { personalizeSourceKind, requestPersonalize } from '../utils/connectorPersonalize';
import { ConnectorTileMenu } from './ConnectorTileMenu';
import { SettingsDisclosure } from './settings/SettingsDisclosure';
import { ltrIn } from './setup/format';
import './ConnectorsSection.css';

// Human-readable labels for well-known OAuth scope URIs.
// Unrecognised scopes fall back to the last path segment of the URI.
const SCOPE_LABELS: Record<string, MessageKey> = {
    'https://www.googleapis.com/auth/gmail.readonly': 'settings.connectors.scope.gmailRead',
    'https://www.googleapis.com/auth/gmail.modify': 'settings.connectors.scope.gmailModify',
    'https://www.googleapis.com/auth/gmail.send': 'settings.connectors.scope.gmailSend',
    'https://www.googleapis.com/auth/gmail.compose': 'settings.connectors.scope.gmailCompose',
    'https://www.googleapis.com/auth/calendar.readonly': 'settings.connectors.scope.calendarRead',
    'https://www.googleapis.com/auth/calendar.events': 'settings.connectors.scope.calendarEvents',
    'https://www.googleapis.com/auth/drive.readonly': 'settings.connectors.scope.driveRead',
    'https://www.googleapis.com/auth/drive.file': 'settings.connectors.scope.driveFile',
    'https://www.googleapis.com/auth/spreadsheets.readonly': 'settings.connectors.scope.sheetsRead',
    'https://www.googleapis.com/auth/spreadsheets': 'settings.connectors.scope.sheets',
    'openid': 'settings.connectors.scope.openid',
    'https://www.googleapis.com/auth/userinfo.email': 'settings.connectors.scope.email',
    'email': 'settings.connectors.scope.email',
    'profile': 'settings.connectors.scope.profile',
    'https://graph.microsoft.com/Mail.ReadWrite': 'settings.connectors.scope.outlookMail',
    'https://graph.microsoft.com/Mail.Send': 'settings.connectors.scope.outlookSend',
    'https://graph.microsoft.com/Calendars.ReadWrite': 'settings.connectors.scope.outlookCalendar',
    'https://graph.microsoft.com/Calendars.Read': 'settings.connectors.scope.outlookCalendarRead',
    'https://graph.microsoft.com/User.Read': 'settings.connectors.scope.microsoftProfile',
};

function scopeLabel(scope: string, t: Translate): string {
    const key = SCOPE_LABELS[scope];
    return key ? t(key) : scope.split(/[/.:]/).pop() ?? scope;
}

const SEARCH_ABOVE = 6;

// Words people search by that a connector's name and description never say.
const MAIL_ACCOUNT_KEYWORDS = ['email', 'e-mail', 'inbox', 'calendar'];
const SEARCH_KEYWORDS: Record<string, string[]> = {
    google: MAIL_ACCOUNT_KEYWORDS,
    microsoft: MAIL_ACCOUNT_KEYWORDS,
    microsoft_work: MAIL_ACCOUNT_KEYWORDS,
};

// Canonical scope for MCP-server grants. Agents that consume MCP servers
// dynamically declare no scopes of their own, so one-click activation
// auto-grants this when no prior grant exists.
const MCP_DEFAULT_GRANT_SCOPES = ['use'];

// The agent a personalization chat runs on.
const FLAGSHIP_AGENT_ID = 'gaia';

type Agents = ReturnType<typeof useChatStore.getState>['agents'];

/** What the personalization pass reads, as a message param ("your recent Slack channels..."). */
function personalizeSourceRef(connector: ConnectorRow) {
    const kind = personalizeSourceKind(connector.id);
    return kind
        ? ref(`settings.connectors.personalize.source.${kind}`)
        : ref('settings.connectors.personalize.source.other', { name: connector.display_name });
}

/**
 * Start the personalization pass. An MCP server's tools stay hidden from an
 * agent until it is activated for it, so activate the flagship first;
 * otherwise the pass runs with nothing to read.
 */
async function startPersonalize(t: Translate, connector: ConnectorRow, agents: Agents): Promise<void> {
    if (connector.type === 'mcp_server') {
        const flagship = agents.find((a) => a.id === FLAGSHIP_AGENT_ID)?.namespaced_agent_id;
        if (!flagship) {
            throw new Error(t('settings.connectors.personalize.noAgent', { name: connector.display_name }));
        }
        if (!connector.activations?.[flagship]) {
            await api.activateConnectorAgent(connector.id, flagship, MCP_DEFAULT_GRANT_SCOPES);
        }
    }
    requestPersonalize({ connectorId: connector.id, displayName: connector.display_name });
}

function PersonalizeButton({ connector, primary, onStarted }: {
    connector: ConnectorRow;
    primary?: boolean;
    onStarted?: () => void;
}) {
    const t = useT();
    const { agents } = useChatStore();
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    const run = async () => {
        setBusy(true);
        setErr(null);
        try {
            await startPersonalize(t, connector, agents);
            onStarted?.();
        } catch (e) {
            setErr(t('settings.connectors.personalize.failed', { error: e instanceof Error ? e.message : String(e) }));
        } finally {
            setBusy(false);
        }
    };
    return (
        <>
            <button
                type="button"
                className={primary ? 'btn-primary' : 'btn-secondary'}
                onClick={() => void run()}
                disabled={busy}
            >
                {busy && <Loader2 size={12} className="spin" />}
                {t('settings.connectors.personalize.button')}
            </button>
            {err && <span className="connector-personalize-error" role="alert">{err}</span>}
        </>
    );
}

// ── ConnectorsSection ────────────────────────────────────────────────────────

/**
 * Turn a ``connector.disconnected`` SSE payload into a user-facing notice,
 * or ``null`` when the disconnect was a full, confirmed revoke and there is
 * nothing more to say (#2591 review — the Disconnect button previously
 * always implied a full revoke, even when only the local credential was
 * cleared). Mirrors the CLI wording in ``connectors/cli.py::_handle_disconnect``.
 */
function describeDisconnectOutcome(payload: Record<string, unknown>, t: Translate): string | null {
    if (!('revoke_supported' in payload)) {
        // Handler type with no remote-revoke concept (e.g. MCP servers).
        return null;
    }
    if (payload.revoke_supported && payload.revoked_remotely) {
        return null;
    }
    if (payload.revoke_supported && !payload.revoked_remotely) {
        return t('settings.connectors.notRevoked', { reason: String(payload.revoke_error ?? t.bare('settings.advanced.unknown')) });
    }
    if (payload.revoke_error) {
        // Not attempted for a reason other than "no endpoint" — e.g. a
        // forwarded connection, or the provider couldn't be resolved.
        return t('settings.connectors.localOnly', { reason: String(payload.revoke_error) });
    }
    return t('settings.connectors.noRevokeApi');
}

export function ConnectorsSection() {
    const t = useT();
    const [connectors, setConnectors] = useState<ConnectorListEntry[]>([]);
    const [agentMcps, setAgentMcps] = useState<AgentMcpServer[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [disconnectNotice, setDisconnectNotice] = useState<string | null>(null);
    const focus = useChatStore((s) => s.settingsConnector);
    const [expanded, setExpanded] = useState<string | null>(focus);
    const [query, setQuery] = useState('');
    // Opened at one connector (from the chat's Connect card): open its tile.
    useEffect(() => {
        if (focus) setExpanded(focus);
    }, [focus]);
    // Groups are settled when the list loads, so a tile that connects stays under the pointer.
    const [connectedAtLoad, setConnectedAtLoad] = useState<ReadonlySet<string>>(new Set());
    // The connector a refresh just showed going from not connected to connected.
    const [justConnected, setJustConnected] = useState<string | null>(null);
    // A connector just disconnected and moved back to its group, which opens to show it.
    const [movedBack, setMovedBack] = useState<string | null>(null);
    const connectorsRef = useRef<ConnectorListEntry[]>([]);
    connectorsRef.current = connectors;
    // Kept here, not in the tile: a first save moves the tile into "Connected" and remounts it.
    const [checks, setChecks] = useState<Record<string, McpCheck | null>>({});
    // Bumped per connector so a check that a newer check or a toggle replaced can't repaint.
    const checkRuns = useRef<Record<string, number>>({});

    const setCheck = (id: string, next: McpCheck | null) =>
        setChecks((prev) => ({ ...prev, [id]: next }));

    const clearCheck = useCallback((id: string) => {
        checkRuns.current[id] = (checkRuns.current[id] ?? 0) + 1;
        setCheck(id, null);
    }, []);

    const runCheck = useCallback(async (id: string) => {
        const run = (checkRuns.current[id] ?? 0) + 1;
        checkRuns.current[id] = run;
        setCheck(id, { state: 'checking' });
        let next: McpCheck;
        try {
            const result = await api.testConnector(id);
            next = result.ok
                ? { state: 'ok' }
                : { state: 'failed', detail: result.detail, slow: result.reason === 'timeout' };
        } catch (e) {
            next = { state: 'failed', detail: e instanceof Error ? e.message : String(e) };
        }
        if (checkRuns.current[id] === run) setCheck(id, next);
    }, []);

    const load = useCallback(async () => {
        try {
            const [{ connectors: rows }, { agent_mcps: mcps }] = await Promise.all([
                api.listConnectors(),
                api.listAgentMcps(),
            ]);
            setConnectors(rows);
            setConnectedAtLoad(new Set(rows.filter((c) => !('error' in c) && c.configured).map((c) => c.id)));
            setAgentMcps(mcps);
            setError(null);
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { void load(); }, [load]);

    // The tile left the group it was in; keep it in view, and keep focus on it if it went with the tile.
    useEffect(() => {
        if (!movedBack) return;
        const tile = [...document.querySelectorAll<HTMLElement>('.connector-tile')].find((el) => el.dataset.connectorId === movedBack);
        const header = tile?.querySelector<HTMLElement>('.connector-tile-header');
        if (!header) return;
        header.scrollIntoView?.({ block: 'nearest' });
        if (!document.activeElement || document.activeElement === document.body) header.focus({ preventScroll: true });
    }, [movedBack]);

    const toggle = (id: string) =>
        setExpanded((prev) => (prev === id ? null : id));

    const onChanged = useCallback(async (id: string) => {
        // Refresh only the changed connector to avoid full reload.
        try {
            const row = await api.getConnector(id);
            const before = connectorsRef.current.find((c) => c.id === id);
            if (before && 'configured' in before && !before.configured && row.configured) {
                setJustConnected(id);
                // Its card carries the one Personalize offer; open it unless the user is reading another.
                setExpanded((prev) => prev ?? id);
            } else if (!row.configured || row.enabled === false) {
                // A disconnect or disable takes back the offer made for that connector.
                setJustConnected((prev) => (prev === id ? null : prev));
            }
            if (before && 'configured' in before && before.configured && !row.configured) {
                // Disconnected: back to its own group, counted there, not left under "Connected".
                setConnectedAtLoad((prev) => new Set([...prev].filter((c) => c !== id)));
                setMovedBack(id);
            }
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
                if (event.reason === 'disconnected') {
                    // #2591 review: the disconnect button used to always
                    // read as a full revoke. Surface the honest outcome the
                    // backend now reports on this event.
                    setDisconnectNotice(describeDisconnectOutcome(event.payload, t));
                }
                if (event.connectorId) {
                    void onChanged(event.connectorId);
                } else {
                    // No connector_id in payload — fall back to a full reload.
                    void load();
                }
            },
            [onChanged, load, t],
        ),
    );

    const q = query.trim().toLowerCase();
    const searchText = (c: ConnectorListEntry) =>
        'error' in c
            ? [c.display_name]
            : [c.display_name, c.description, connectorDescriptionBare(t, c), ...(SEARCH_KEYWORDS[c.id] ?? [])];
    const shown = connectors.filter((c) => !q || searchText(c).some((s) => s.toLowerCase().includes(q)));
    const unavailable = shown.filter((c): c is UnavailableConnectorRow => 'error' in c);
    const available = shown.filter((c): c is ConnectorRow => !('error' in c));
    const connected = available.filter((c) => connectedAtLoad.has(c.id));
    const accounts = available.filter((c) => !connectedAtLoad.has(c.id) && c.type === 'oauth_pkce');
    const servers = available.filter((c) => !connectedAtLoad.has(c.id) && c.type !== 'oauth_pkce');
    const nothingConnected = !connectors.some((c) => !('error' in c) && c.configured);
    // Re-keyed so the group the tile moved into opens on it.
    const groupKey = (name: string, rows: ConnectorRow[]) =>
        (movedBack && rows.some((c) => c.id === movedBack) ? `${name}:${movedBack}` : name);
    const holdsMoved = (rows: ConnectorRow[]) => !!movedBack && rows.some((c) => c.id === movedBack);

    const tiles = (rows: ConnectorRow[]) => (
        <div className="connectors-list">
            {rows.map((c) => (
                <ConnectorTile
                    key={c.id}
                    connector={c}
                    wasConnected={connectedAtLoad.has(c.id) || movedBack === c.id}
                    justConnected={justConnected === c.id}
                    onPersonalizeStarted={() => setJustConnected(null)}
                    expanded={expanded === c.id}
                    onToggle={() => toggle(c.id)}
                    onChanged={() => void onChanged(c.id)}
                    check={checks[c.id] ?? null}
                    onRunCheck={() => void runCheck(c.id)}
                    onClearCheck={() => clearCheck(c.id)}
                />
            ))}
        </div>
    );

    return (
        <section className="connectors-section" aria-label={t('settings.section.connectors')}>
            <p className="settings-pane-lede">{t('settings.connectors.lede')}</p>

            {error && (
                <div className="error-banner">
                    <AlertCircle size={14} />
                    <span>{error}</span>
                </div>
            )}

            {disconnectNotice && (
                <div
                    className="error-banner"
                    role="status"
                    onClick={() => setDisconnectNotice(null)}
                >
                    <AlertCircle size={14} />
                    <span>{disconnectNotice}</span>
                </div>
            )}

            {loading ? (
                <div className="connectors-loading">
                    <Loader2 size={16} className="spin" />
                </div>
            ) : (
                <>
                    {connectors.length > SEARCH_ABOVE && (
                        <input
                            className="settings-input"
                            type="search"
                            value={query}
                            onChange={(e) => setQuery(e.target.value)}
                            placeholder={t('settings.connectors.search')}
                            aria-label={t('settings.connectors.search')}
                        />
                    )}
                    {connectors.length === 0 && !error && <p className="settings-hint">{t('settings.connectors.none')}</p>}
                    {q && shown.length === 0 && <p className="settings-hint">{t('settings.connectors.noMatch')}</p>}
                    {unavailable.length > 0 && (
                        <SettingsDisclosure title={t('settings.connectors.unavailable')} hint={unavailableHint(unavailable, t)} count={unavailable.length} defaultOpen forceOpen={!!q}>
                            <div className="connectors-list">
                                {unavailable.map((c) => <UnavailableTile key={c.id} connector={c} />)}
                            </div>
                        </SettingsDisclosure>
                    )}
                    {connected.length > 0 && (
                        <SettingsDisclosure title={t('settings.connectors.connected')} count={connected.filter((c) => c.configured).length} defaultOpen forceOpen={!!q}>
                            {tiles(connected)}
                        </SettingsDisclosure>
                    )}
                    {accounts.length > 0 && (
                        <SettingsDisclosure key={groupKey('accounts', accounts)} title={t('settings.connectors.accounts')} hint={t('settings.connectors.accountsHint')} count={accounts.length} defaultOpen={nothingConnected || holdsMoved(accounts)} forceOpen={!!q}>
                            {tiles(accounts)}
                        </SettingsDisclosure>
                    )}
                    {servers.length > 0 && (
                        <SettingsDisclosure key={groupKey('servers', servers)} title={t('settings.connectors.servers')} hint={t('settings.connectors.serversHint')} count={servers.length} defaultOpen={holdsMoved(servers)} forceOpen={!!q}>
                            {tiles(servers)}
                        </SettingsDisclosure>
                    )}
                    {agentMcps.length > 0 && !q && (
                        <SettingsDisclosure title={t('settings.connectors.agentServers')} count={agentMcps.length}>
                            <AgentMcpsSection
                                servers={agentMcps}
                                expanded={expanded}
                                onToggle={toggle}
                            />
                        </SettingsDisclosure>
                    )}
                </>
            )}
        </section>
    );
}

function unavailableHint(rows: UnavailableConnectorRow[], t: Translate): string {
    const ledger = rows.find((c) => c.reason === 'activations_unreadable' && c.path);
    if (!ledger?.path) return t('settings.connectors.unavailableHint');
    return t('settings.connectors.activationsUnreadableHint', { path: ltrIn(t.locale, ledger.path) });
}

// ── UnavailableTile ──────────────────────────────────────────────────────────

function UnavailableTile({ connector }: { connector: UnavailableConnectorRow }) {
    const t = useT();
    return (
        <div className="connector-tile">
            <div className="connector-tile-header connector-tile-header--static">
                <bdi className="connector-tile-name">{connector.display_name}</bdi>
                <span className="connector-status disabled">{t('settings.connectors.unavailableStatus')}</span>
            </div>
        </div>
    );
}

// ── ConnectorTile ────────────────────────────────────────────────────────────

type McpCheck =
    | { state: 'checking' }
    | { state: 'ok' }
    | { state: 'failed'; detail: string; slow?: boolean };

function ConnectorTile({
    connector,
    wasConnected,
    justConnected,
    onPersonalizeStarted,
    expanded,
    onToggle,
    onChanged,
    check,
    onRunCheck,
    onClearCheck,
}: {
    connector: ConnectorRow;
    /** Listed under "Connected" since Settings opened, whatever it is now. */
    wasConnected: boolean;
    /** Connected a moment ago: the card's Personalize offer is the one that asks. */
    justConnected: boolean;
    onPersonalizeStarted: () => void;
    expanded: boolean;
    onToggle: () => void;
    onChanged: () => void;
    check: McpCheck | null;
    onRunCheck: () => void;
    onClearCheck: () => void;
    /** The just-connected banner already offers personalization for this connector. */
}) {
    const t = useT();
    const { agents } = useChatStore();
    const soleAgent = connector.type === 'mcp_server' ? soleMcpAgent(agents, connector.id) : null;
    // Status for the tile header (#1004): an unconfigured connector's group already says so,
    // so it only flags one that needs setup first.
    const renderStatus = () => {
        if (!connector.configured) {
            // Still in the "Connected" group it was listed in, so the row has to say it left.
            if (wasConnected) return <span className="connector-status idle" role="status">{t('settings.connectors.disconnected')}</span>;
            return connector.configurable === false
                ? <span className="connector-status idle">{t('settings.connectors.setupRequired')}</span>
                : null;
        }
        if (connector.type === 'mcp_server' && connector.enabled === false) {
            return <span className="connector-status disabled">{t('settings.connectors.paused')}</span>;
        }
        return (
            <span className="connector-status ok">
                <CheckCircle2 size={12} />
                {connector.account_id ? <bdi dir="ltr">{connector.account_id}</bdi> : t('settings.connectors.connected')}
            </span>
        );
    };

    return (
        <div className={`connector-tile${expanded ? ' connector-tile--open' : ''}`} data-connector-id={connector.id}>
            <div className="connector-tile-row">
                <button
                    type="button"
                    className="connector-tile-header"
                    onClick={onToggle}
                    aria-expanded={expanded}
                >
                    <bdi className="connector-tile-name">{connector.display_name}</bdi>
                    {connector.configured && (
                        <span className="connector-tile-type">{connector.type === 'oauth_pkce' ? 'OAuth' : 'MCP'}</span>
                    )}
                    {renderStatus()}
                    <span className="connector-tile-chevron">
                        {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                    </span>
                </button>
                {/* An open card already shows these actions. */}
                {!expanded && <ConnectorTileMenu connector={connector} onChanged={onChanged} />}
            </div>

            {expanded && (
                <div className="connector-detail">
                    {connector.type === 'oauth_pkce' ? (
                        <OAuthConfigureBody connector={connector} onChanged={onChanged} />
                    ) : (
                        <MCPServerConfigureBody
                            connector={connector}
                            onChanged={onChanged}
                            showEnabledToggle={!soleAgent}
                            check={check}
                            onRunCheck={onRunCheck}
                            onClearCheck={onClearCheck}
                        />
                    )}
                    {connector.configured && connector.enabled !== false && (
                        <div className={`connector-personalize${justConnected ? ' connector-personalize--offer' : ''}`} role={justConnected ? 'status' : undefined}>
                            <p>{t(justConnected ? 'settings.connectors.personalize.offer' : 'settings.connectors.personalize.tile', { source: personalizeSourceRef(connector), name: connector.display_name })}</p>
                            <PersonalizeButton connector={connector} primary={justConnected} onStarted={onPersonalizeStarted} />
                        </div>
                    )}
                    {connector.configured && (
                        <ConnectorAgentGrants
                            connectorId={connector.id}
                            connectorName={connector.display_name}
                            connectorType={connector.type}
                            connectorEnabled={connector.enabled !== false}
                            soleAgent={soleAgent}
                            onConnectorChanged={onChanged}
                            onClearCheck={onClearCheck}
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
    const t = useT();
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    const [setupValues, setSetupValues] = useState<Record<string, string>>({});
    // Device-code sign-in (#1275): once started, the server polls in the
    // background and we display the code + URL here until the SSE
    // oauth_completed/oauth_error event resolves it.
    const [deviceInfo, setDeviceInfo] = useState<api.DeviceAuthInfo | null>(null);

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

    // Device-code connect: no browser redirect / no Azure app registration.
    // The server polls in the background; we just show the code + URL and let
    // the SSE oauth_completed / oauth_error events (below) resolve the state.
    const handleConnectDevice = async () => {
        setBusy(true);
        setErr(null);
        setDeviceInfo(null);
        try {
            const info = await api.authorizeConnectorDevice(
                connector.id,
                connector.available_scopes?.length
                    ? connector.available_scopes
                    : connector.default_scopes,
                selectedGrantAgents(),
            );
            setDeviceInfo(info);
        } catch (e) {
            setErr(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(false);
        }
    };

    // Success path: once the backend reports the connection, drop the code UI.
    useEffect(() => {
        if (connector.configured) setDeviceInfo(null);
    }, [connector.configured]);

    // While a device sign-in is pending, react to THIS connector's SSE events
    // so an expiry / denial surfaces as an error instead of a spinner that
    // never resolves. Mounted only while the tile is expanded.
    useConnectorsSSE(
        useCallback(
            (event) => {
                if (event.connectorId !== connector.id) return;
                if (event.reason === 'oauth_error') {
                    // A browser sign-in says why it ended; its raw error stays in the log.
                    const why = event.payload.reason;
                    setErr(
                        why === 'cancelled'
                            ? t('connectCard.cancelled')
                            : why
                              ? t('connectCard.failed')
                              : String(event.payload.error ?? t('settings.connectors.deviceFailed')),
                    );
                    setDeviceInfo(null);
                } else if (event.reason === 'oauth_completed') {
                    setDeviceInfo(null);
                }
            },
            [connector.id, t],
        ),
    );

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
        const missing = (connector.oauth_setup_fields ?? []).filter(
            (f) => f.required !== false && !setupValues[f.key]?.trim(),
        );
        if (missing.length) {
            setErr(missingFieldsMessage(t, connector.id, missing));
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
                <p className="connector-desc"><bdi>{connectorDescription(t, connector)}</bdi></p>
            )}
            {showSetupForm && (
                <div className="oauth-setup-form">
                    <p className="connector-desc">{t('settings.connectors.firstSetup')}</p>
                    {setupFields.map((field) => (
                        <label key={field.key} className="oauth-setup-field">
                            <span className="oauth-setup-label">{fieldLabel(t, connector.id, field)}</span>
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
                                <span className="oauth-setup-help"><bdi>{fieldHelp(t, connector.id, field)}</bdi></span>
                            )}
                        </label>
                    ))}
                </div>
            )}
            {!connector.configured && grantableAgents.length > 0 && (
                <div className="oauth-grant-onconnect">
                    <p className="oauth-grant-onconnect-label">{t('settings.connectors.grantOnConnect')}</p>
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
                    <p className="oauth-grant-onconnect-help">{t('settings.connectors.grantLater')}</p>
                </div>
            )}
            {!showSetupForm && (
                <OAuthClientEditor connector={connector} onChanged={onChanged} />
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
                        {busy ? <Loader2 size={12} className="spin" /> : t('settings.connectors.disconnect')}
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
                            <><ExternalLink size={12} /> {t('settings.connectors.saveConnect')}</>
                        )}
                    </button>
                ) : connector.configurable === false ? (
                    <span
                        className="connector-setup-required"
                        title={connector.config_error ?? undefined}
                    >
                        {t('settings.connectors.setupRequired')}
                    </span>
                ) : (
                    <>
                        <button
                            className="btn-primary"
                            disabled={busy || deviceInfo !== null}
                            onClick={() => void handleConnect()}
                        >
                            {busy ? (
                                <Loader2 size={12} className="spin" />
                            ) : (
                                <><ExternalLink size={12} /> {t('settings.key.connect')}</>
                            )}
                        </button>
                        {connector.supports_device_code && (
                            <button
                                className="btn-secondary"
                                disabled={busy || deviceInfo !== null}
                                onClick={() => void handleConnectDevice()}
                                title={t('settings.connectors.codeTitle')}
                            >
                                {t('settings.connectors.code')}
                            </button>
                        )}
                    </>
                )}
                {(connector.docs_url || connector.product_url) && (
                    <a
                        href={connector.docs_url || connector.product_url || '#'}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="connector-product-link"
                    >
                        {t('settings.connectors.learnMore')} <ExternalLink size={11} />
                    </a>
                )}
            </div>
            {deviceInfo && !connector.configured && (
                <div className="device-code-block">
                    <p className="device-code-instructions">
                        {t('settings.connectors.goTo')}{' '}
                        <a
                            href={deviceInfo.verification_uri}
                            target="_blank"
                            rel="noopener noreferrer"
                            onClick={(e) => {
                                e.preventDefault();
                                openAuthUrl(deviceInfo.verification_uri);
                            }}
                        >
                            {deviceInfo.verification_uri}
                        </a>
                    </p>
                    <div className="device-code-value">
                        <code>{deviceInfo.user_code}</code>
                        <button
                            type="button"
                            className="btn-secondary"
                            onClick={() =>
                                void navigator.clipboard?.writeText(deviceInfo.user_code)
                            }
                            title={t('settings.connectors.copyCode')}
                        >
                            {t('gpu.copy')}
                        </button>
                    </div>
                    <p className="device-code-waiting">
                        <Loader2 size={12} className="spin" /> {t('settings.connectors.waiting')}
                    </p>
                    <button
                        type="button"
                        className="btn-secondary"
                        onClick={() => setDeviceInfo(null)}
                    >
                        {t('settings.key.cancel')}
                    </button>
                </div>
            )}
        </div>
    );
}

// ── OAuthClientEditor ────────────────────────────────────────────────────────

/**
 * Collapsible "OAuth client" editor (#2104 interim).
 *
 * The first-time setup form only renders while the provider is
 * unconfigurable; every other state (client from env, stale keyring
 * credentials, rotating a client, already connected) previously had no
 * UI at all — a user holding a client ID dead-ended in Settings. This
 * section is always available for OAuth connectors: it shows which
 * client the provider resolves (keyring / env / none) and saves a new
 * client ID + optional secret through the same keyring slot the CLI
 * `gaia connectors configure` path writes, so precedence (stored → env)
 * is unchanged. Saving never starts an OAuth flow (``save_only``).
 */
function OAuthClientEditor({
    connector,
    onChanged,
}: {
    connector: ConnectorRow;
    onChanged: () => void;
}) {
    const t = useT();
    const [open, setOpen] = useState(false);
    const [values, setValues] = useState<Record<string, string>>({});
    const [busy, setBusy] = useState(false);
    const [saved, setSaved] = useState(false);
    const [err, setErr] = useState<string | null>(null);

    const fields = connector.oauth_setup_fields ?? [];
    if (fields.length === 0) return null;

    const client = connector.oauth_client;
    const statusLine =
        client?.source === 'keyring'
            ? t('settings.connectors.savedClient', { id: client.client_id ?? '' })
            : client?.source === 'env'
              ? t('settings.connectors.envClient', { id: client.client_id ?? '' })
              : t('settings.connectors.noClient');

    const handleSave = async () => {
        const clientId = values['client_id']?.trim();
        if (!clientId) {
            setErr(t('settings.connectors.clientRequired'));
            return;
        }
        setBusy(true);
        setErr(null);
        setSaved(false);
        try {
            await api.configureConnector(connector.id, {
                ...Object.fromEntries(
                    Object.entries(values).map(([k, v]) => [k, v.trim()]),
                ),
                save_only: true,
            });
            setSaved(true);
            setValues({});
            onChanged();
            setTimeout(() => setSaved(false), 2200);
        } catch (e) {
            setErr(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="oauth-client-editor">
            <button
                type="button"
                className="oauth-client-editor__toggle"
                onClick={() => setOpen((v) => !v)}
                aria-expanded={open}
            >
                {open ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                <span>{t('settings.connectors.client')}</span>
                <span className="oauth-client-editor__status">{statusLine}</span>
            </button>
            {open && (
                <div className="oauth-setup-form">
                    <p className="connector-desc">{t('settings.connectors.clientHelp')}</p>
                    {fields.map((field) => (
                        <label key={field.key} className="oauth-setup-field">
                            <span className="oauth-setup-label">{fieldLabel(t, connector.id, field)}</span>
                            <input
                                type={field.kind === 'secret' ? 'password' : 'text'}
                                className="oauth-setup-input"
                                placeholder={
                                    field.kind === 'secret' &&
                                    client?.source === 'keyring' &&
                                    client.has_secret
                                        ? '••••••••'
                                        : field.placeholder
                                }
                                value={values[field.key] ?? ''}
                                onChange={(e) =>
                                    setValues((prev) => ({
                                        ...prev,
                                        [field.key]: e.target.value,
                                    }))
                                }
                                autoComplete="off"
                                spellCheck={false}
                            />
                            {field.help_md && (
                                <span className="oauth-setup-help"><bdi>{fieldHelp(t, connector.id, field)}</bdi></span>
                            )}
                        </label>
                    ))}
                    {err && (
                        <div className="configure-error">
                            <AlertCircle size={12} /> {err}
                        </div>
                    )}
                    <div className="configure-actions">
                        <button
                            className="btn-primary"
                            disabled={busy || !values['client_id']?.trim()}
                            onClick={() => void handleSave()}
                        >
                            {busy ? (
                                <Loader2 size={12} className="spin" />
                            ) : saved ? (
                                <><CheckCircle2 size={12} /> {t('settings.advanced.saved')}</>
                            ) : (
                                t('settings.connectors.saveClient')
                            )}
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
}

// ── MCPServerConfigureBody ───────────────────────────────────────────────────

function MCPServerConfigureBody({
    connector,
    onChanged,
    check,
    onRunCheck,
    onClearCheck,
    showEnabledToggle = true,
}: {
    connector: ConnectorRow;
    onChanged: () => void;
    showEnabledToggle?: boolean;
    check: McpCheck | null;
    onRunCheck: () => void;
    onClearCheck: () => void;
}) {
    const t = useT();
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

    // Servers like Git and Memory take no keys: connecting just registers them.
    const keyless = connector.mcp_env_keys.length === 0;
    const nothingTyped = Object.values(values).every((v) => v.trim() === '');

    const handleSave = async () => {
        const filled = Object.fromEntries(
            Object.entries(values)
                .map(([k, v]) => [k, v.trim()])
                .filter(([, v]) => v !== ''),
        );
        if (!keyless && Object.keys(filled).length === 0) return;
        const wrong = connector.config_fields?.find(
            (f) => f.prefix && f.key in filled && !filled[f.key].startsWith(f.prefix),
        );
        if (wrong) {
            setErr(
                t.compose((say, code) =>
                    say('settings.connectors.wrongPrefix', {
                        label: fieldLabel(translator(code), connector.id, wrong, true),
                        prefix: ltrIn(code, wrong.prefix ?? ''),
                    }),
                ),
            );
            return;
        }
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
            return;
        } finally {
            setBusy(false);
        }
        onRunCheck();
    };

    const handleDisconnect = async () => {
        setBusy(true);
        setErr(null);
        try {
            await api.disconnectConnector(connector.id);
            onClearCheck();
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
        onClearCheck();
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
                <p className="connector-desc"><bdi>{connectorDescription(t, connector)}</bdi></p>
            )}
            {err && (
                <div className="configure-error">
                    <AlertCircle size={12} /> {err}
                </div>
            )}
            {connector.configured && showEnabledToggle && (
                <div className="connector-toggle-row">
                    <span className="connector-toggle-label">{t('settings.connectors.active')}</span>
                    <span className="toggle-switch">
                        <input
                            type="checkbox"
                            checked={connector.enabled !== false}
                            onChange={() => void handleToggleEnabled()}
                            disabled={busy}
                            aria-label={
                                connector.enabled
                                    ? t('settings.connectors.disableServer')
                                    : t('settings.connectors.enableServer')
                            }
                        />
                        <span className="toggle-track" />
                    </span>
                </div>
            )}
            {connector.configured && showEnabledToggle && connector.enabled === false && (
                <p className="connector-toggle-help">{t('settings.connectors.disabledHelp')}</p>
            )}
            {connector.setup_url && !connector.configured && (
                <a
                    href={safeUrlTransform(connector.setup_url) || undefined}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="btn-secondary connector-setup-link"
                >
                    {t('settings.connectors.createApp', { name: connector.display_name })}
                    <ExternalLink size={12} />
                </a>
            )}
            {connector.mcp_env_keys.map((key) => {
                const field = connector.config_fields?.find((f) => f.key === key);
                return (
                    <div key={key} className="mcp-key-row">
                        <label className="mcp-key-label" htmlFor={`mcp-key-${connector.id}-${key}`}>
                            <bdi>{field ? fieldLabel(t, connector.id, field) : key}</bdi>
                        </label>
                        <input
                            id={`mcp-key-${connector.id}-${key}`}
                            type="password"
                            dir="ltr"
                            className="mcp-key-input"
                            placeholder={
                                connector.configured
                                    ? '••••••••'
                                    : field?.placeholder || t.bare('settings.connectors.enterValue')
                            }
                            value={values[key] ?? ''}
                            onChange={(e) =>
                                setValues((prev) => ({ ...prev, [key]: e.target.value }))
                            }
                            spellCheck={false}
                            autoComplete="off"
                        />
                        {field?.help_md && !connector.configured && (
                            <div className="mcp-key-help" dir="auto">
                                <ReactMarkdown
                                    disallowedElements={[...SAFE_DISALLOWED_ELEMENTS]}
                                    urlTransform={safeUrlTransform}
                                    components={{
                                        a: ({ href, children }) => (
                                            <a href={href} target="_blank" rel="noopener noreferrer">
                                                {children}
                                            </a>
                                        ),
                                    }}
                                >
                                    {fieldHelp(t, connector.id, field)}
                                </ReactMarkdown>
                            </div>
                        )}
                    </div>
                );
            })}
            <div className="configure-actions">
                {!(keyless && connector.configured) && (
                    <button
                        className="btn-primary"
                        disabled={busy || (!keyless && nothingTyped)}
                        onClick={() => void handleSave()}
                    >
                        {busy ? (
                            <Loader2 size={12} className="spin" />
                        ) : saved ? (
                            <><CheckCircle2 size={12} /> {t('settings.advanced.saved')}</>
                        ) : keyless ? (
                            t('settings.key.connect')
                        ) : (
                            t('settings.advanced.save')
                        )}
                    </button>
                )}
                {connector.configured && connector.enabled !== false && (
                    <button
                        className="btn-secondary"
                        disabled={busy || check?.state === 'checking'}
                        onClick={onRunCheck}
                    >
                        {t('settings.connectors.check')}
                    </button>
                )}
                {connector.configured && (
                    <button
                        className="btn-secondary"
                        disabled={busy}
                        onClick={() => void handleDisconnect()}
                    >
                        {t('settings.connectors.disconnect')}
                    </button>
                )}
                {connector.product_url && (
                    <a
                        href={connector.product_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="connector-product-link"
                    >
                        {t('settings.connectors.docs')} <ExternalLink size={11} />
                    </a>
                )}
            </div>
            {check?.state === 'checking' && (
                <p className="connector-check" role="status">
                    <Loader2 size={12} className="spin" />
                    {t('settings.connectors.checking', { name: connector.display_name })}
                </p>
            )}
            {check?.state === 'ok' && (
                <p className="connector-check connector-check--ok" role="status">
                    <CheckCircle2 size={12} />
                    {t('settings.connectors.checkOk', { name: connector.display_name })}
                </p>
            )}
            {check?.state === 'failed' && (
                <div className="connector-check connector-check--failed" role="alert">
                    <p>
                        {check.slow
                            ? t('settings.connectors.checkSlow', { name: connector.display_name })
                            : keyless
                              ? t('settings.connectors.checkFailed', { name: connector.display_name })
                              : t('settings.connectors.checkFailedKey', { name: connector.display_name })}
                    </p>
                    <details>
                        <summary>{t('settings.connectors.checkDetails')}</summary>
                        <pre dir="ltr">{check.detail}</pre>
                    </details>
                </div>
            )}
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
    const t = useT();
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
            <bdi className="grant-agent-card-name">{agent.name}</bdi>
            <div className="grant-scope-list">
                {req.scopes.map((scope) => (
                    <label key={scope} className="grant-scope-item">
                        <span className="grant-scope-label">{scopeLabel(scope, t)}</span>
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
    const t = useT();
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
                <span className="agent-mcp-agent-label">{t('settings.connectors.via', { name: server.agent_name })}</span>
                {server.disabled ? (
                    <span className="agent-mcp-status disabled">{t('settings.connectors.paused')}</span>
                ) : (
                    <span className="agent-mcp-status enabled">
                        <CheckCircle2 size={11} /> {t('settings.connectors.active')}
                    </span>
                )}
                <span className="agent-mcp-readonly-badge">{t('settings.connectors.readOnly')}</span>
                <span className="agent-mcp-chevron">
                    {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                </span>
            </button>

            {expanded && (
                <div className="agent-mcp-detail">
                    {cmdDisplay && (
                        <bdi dir="ltr" className="agent-mcp-command-row">{cmdDisplay}</bdi>
                    )}
                    <bdi dir="ltr" className="agent-mcp-config-path">{server.config_path}</bdi>
                    <div className="agent-mcp-config-hint">{t('settings.connectors.editConfig')}</div>
                </div>
            )}
        </div>
    );
}

// ── ConnectorAgentGrants ─────────────────────────────────────────────────────

function ConnectorAgentGrants({
    connectorId,
    connectorName,
    connectorType,
    connectorEnabled,
    soleAgent,
    onConnectorChanged,
    onClearCheck,
}: {
    connectorId: string;
    connectorName: string;
    connectorType: string;
    connectorEnabled: boolean;
    soleAgent: SoleAgent | null;
    onConnectorChanged: () => void;
    onClearCheck: () => void;
}) {
    const t = useT();
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

    if (soleAgent) {
        return (
            <div className="connection-grants">
                <SoleAgentSwitch
                    agent={soleAgent}
                    connectorId={connectorId}
                    connectorName={connectorName}
                    connectorEnabled={connectorEnabled}
                    active={Boolean(activations[soleAgent.namespaced_agent_id])}
                    hasGrant={(grants[soleAgent.namespaced_agent_id] ?? []).length > 0}
                    onToggle={onClearCheck}
                    onChanged={() => {
                        onConnectorChanged();
                        void load();
                    }}
                />
            </div>
        );
    }

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

    // A grants block with no agent in it is only noise in a one-agent app.
    if (relevantAgents.length === 0 && activatableAgents.length === 0) return null;

    return (
        <div className="connection-grants">
            {relevantAgents.length > 0 && (
                <>
                    <div className="grants-header">{t('settings.connectors.grants')}</div>
                    {relevantAgents.map((agent) => (
                        <AgentGrantCard
                            key={agent.namespaced_agent_id}
                            agent={agent}
                            connectorId={connectorId}
                            grantedScopes={grants[agent.namespaced_agent_id!] ?? []}
                            onChanged={() => void load()}
                        />
                    ))}
                </>
            )}

            {/* Activations gate MCP tool visibility. OAuth connectors have no
                MCP tool surface — their per-agent access is governed by the
                per-scope grant toggles above — so showing this block for them
                would be a switch that does nothing. (issue #1005) */}
            {activatableAgents.length > 0 && (
                <>
                    <div className="grants-header grants-header--activations">
                        {t('settings.connectors.activeFor')}
                    </div>
                    {activatableAgents.map((agent) => (
                        <AgentActivationCard
                            key={`activation-${agent.namespaced_agent_id}`}
                            agent={agent}
                            connectorId={connectorId}
                            connectorName={connectorName}
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

// ── SoleAgentSwitch ──────────────────────────────────────────────────────────

type SoleAgent = { name: string; namespaced_agent_id: string };

/**
 * The one assistant that can use this MCP server, when GAIA is the only one.
 * Most people have no other assistant, and two switches (server on, GAIA may
 * use it) that must both be on read as the same thing twice.
 */
function soleMcpAgent(
    agents: ReturnType<typeof useChatStore.getState>['agents'],
    connectorId: string,
): SoleAgent | null {
    const usable = agents.filter(
        (a) =>
            a.namespaced_agent_id &&
            (a.consumes_mcp_servers || a.required_connections?.some((rc) => rc.connector_id === connectorId)),
    );
    const needsGrant = agents.some((a) => a.required_connections?.some((rc) => rc.connector_id === connectorId));
    if (usable.length !== 1 || usable[0].id !== 'gaia' || needsGrant) return null;
    return { name: usable[0].name, namespaced_agent_id: usable[0].namespaced_agent_id! };
}

/** On means the server runs and GAIA sees its tools; off hides them from GAIA. */
function SoleAgentSwitch({
    agent,
    connectorId,
    connectorName,
    connectorEnabled,
    active,
    hasGrant,
    onToggle,
    onChanged,
}: {
    agent: SoleAgent;
    connectorId: string;
    connectorName: string;
    connectorEnabled: boolean;
    active: boolean;
    hasGrant: boolean;
    onToggle: () => void;
    onChanged: () => void;
}) {
    const t = useT();
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    const on = connectorEnabled && active;

    const toggle = async () => {
        if (busy) return;
        setBusy(true);
        setErr(null);
        onToggle();
        try {
            if (on) {
                await api.deactivateConnectorAgent(connectorId, agent.namespaced_agent_id);
            } else {
                if (!connectorEnabled) await api.enableConnector(connectorId);
                if (!active) {
                    await api.activateConnectorAgent(
                        connectorId,
                        agent.namespaced_agent_id,
                        hasGrant ? undefined : [...MCP_DEFAULT_GRANT_SCOPES],
                    );
                }
            }
            onChanged();
        } catch (e) {
            setErr(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(false);
        }
    };

    return (
        <>
            <label className="connector-toggle-row">
                <span className="connector-toggle-label">
                    {t('settings.connectors.agentCanUse', { agent: agent.name, name: connectorName })}
                </span>
                <span className="toggle-switch">
                    <input type="checkbox" checked={on} onChange={() => void toggle()} disabled={busy} />
                    <span className="toggle-track" />
                </span>
            </label>
            {err && (
                <div className="grant-scope-warning grant-scope-warning--error">
                    <AlertCircle size={11} /> {err}
                </div>
            )}
        </>
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
    connectorName,
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
    connectorName: string;
    active: boolean;
    grantedScopes: string[];
    onChanged: () => void;
}) {
    const t = useT();
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
            <bdi className="grant-agent-card-name">{agent.name}</bdi>
            <div className="grant-scope-list">
                <label className="grant-scope-item">
                    <span className="grant-scope-label">
                        {t(localActive ? 'settings.connectors.toolsVisible' : 'settings.connectors.toolsHidden', { name: connectorName })}
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
