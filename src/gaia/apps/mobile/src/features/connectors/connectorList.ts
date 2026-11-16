// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { isMessageKey, isolate } from '@webui/i18n/registry';
import { tEn, type Translate } from '@webui/i18n/translator';

import type { AgentMcpServer, ConnectorListEntry, DisconnectOutcome, ConnectorRow, UnavailableConnectorRow } from '@/api/client.types';

/** The search box appears once there are more connectors than this, as on the web. */
export const SEARCH_ABOVE = 6;

// Words people search by that a connector's name and description never say.
const MAIL_ACCOUNT_KEYWORDS = ['email', 'e-mail', 'inbox', 'calendar'];
const SEARCH_KEYWORDS: Record<string, string[]> = {
    google: MAIL_ACCOUNT_KEYWORDS,
    microsoft: MAIL_ACCOUNT_KEYWORDS,
    microsoft_work: MAIL_ACCOUNT_KEYWORDS,
};

export const GOOGLE_CONNECTOR_ID = 'google';

export type GroupId = 'unavailable' | 'connected' | 'accounts' | 'servers';

export interface ConnectorGroups {
    unavailable: UnavailableConnectorRow[];
    connected: ConnectorRow[];
    accounts: ConnectorRow[];
    servers: ConnectorRow[];
    /** Whether any account is set up; with none, the Accounts group starts open. */
    nothingConnected: boolean;
}

export function isUnavailable(c: ConnectorListEntry): c is UnavailableConnectorRow {
    return 'error' in c;
}

/** The catalog's text in the chosen language; a custom server's, or a reworded catalog's, shows as sent. */
export function connectorDescription(t: Translate, c: ConnectorRow, bare = false): string {
    const key = `connectors.${c.id}.description`;
    if (!isMessageKey(key) || tEn(key) !== c.description) return c.description;
    return bare ? t.bare(key) : t(key);
}

function searchText(c: ConnectorListEntry, t: Translate): string[] {
    if (isUnavailable(c)) return [c.display_name];
    return [c.display_name, c.description, connectorDescription(t, c, true), ...(SEARCH_KEYWORDS[c.id] ?? [])];
}

export function groupConnectors(connectors: readonly ConnectorListEntry[], query: string, t: Translate): ConnectorGroups {
    const q = query.trim().toLowerCase();
    const shown = connectors.filter((c) => !q || searchText(c, t).some((s) => s.toLowerCase().includes(q)));
    const available = shown.filter((c): c is ConnectorRow => !isUnavailable(c));
    const connected = available.filter((c) => c.configured);
    const rest = available.filter((c) => !c.configured);
    return {
        unavailable: shown.filter(isUnavailable),
        connected,
        accounts: rest.filter((c) => c.type === 'oauth_pkce'),
        servers: rest.filter((c) => c.type !== 'oauth_pkce'),
        nothingConnected: !connectors.some((c) => !isUnavailable(c) && c.configured),
    };
}

/** The hint under "Unavailable": the unreadable settings file's path when that is the cause. */
export function unavailableHint(rows: readonly UnavailableConnectorRow[], t: Translate): string {
    const ledger = rows.find((c) => c.reason === 'activations_unreadable' && c.path);
    if (!ledger?.path) return t('settings.connectors.unavailableHint');
    return t('settings.connectors.activationsUnreadableHint', { path: isolate(ledger.path, 'ltr') });
}

export function agentMcpKey(s: AgentMcpServer): string {
    return `${s.agent_id}::${s.server_name}`;
}

export type TileStatus =
    | { kind: 'connected'; accountId: string | null }
    | { kind: 'paused' }
    | { kind: 'setupRequired' }
    | { kind: 'none' };

export function tileStatus(c: ConnectorRow): TileStatus {
    if (!c.configured) return c.configurable === false ? { kind: 'setupRequired' } : { kind: 'none' };
    if (c.type === 'mcp_server' && c.enabled === false) return { kind: 'paused' };
    return { kind: 'connected', accountId: c.account_id };
}

/** What a disconnect tells the person about the provider-side revoke, or null when nothing needs saying. */
export function disconnectNotice(outcome: DisconnectOutcome | null, connector: ConnectorRow, t: Translate): string | null {
    if (outcome === null) return connector.type === 'oauth_pkce' ? t('mobileApp.connectors.revokeUnreported') : null;
    if (!('revoke_supported' in outcome)) return null;
    if (outcome.revoke_supported) {
        return outcome.revoked_remotely
            ? null
            : t('settings.connectors.notRevoked', { reason: String(outcome.revoke_error ?? t('settings.advanced.unknown')) });
    }
    if (outcome.revoke_error) return t('settings.connectors.localOnly', { reason: String(outcome.revoke_error) });
    return t('settings.connectors.noRevokeApi');
}

/** The saved OAuth client's status line, as the desktop's OAuth client editor words it. */
export function oauthClientStatus(connector: ConnectorRow, t: Translate): string | null {
    const client = connector.oauth_client;
    if (!client) return null;
    if (client.source === 'keyring') return t('settings.connectors.savedClient', { id: client.client_id ?? '' });
    if (client.source === 'env') return t('settings.connectors.envClient', { id: client.client_id ?? '' });
    return t('settings.connectors.noClient');
}
