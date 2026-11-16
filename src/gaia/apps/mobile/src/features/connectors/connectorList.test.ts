// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { translator } from '@webui/i18n/translator';

import type { ConnectorListEntry, ConnectorRow, UnavailableConnectorRow } from '@/api/client.types';

import { groupConnectors, tileStatus, unavailableHint } from './connectorList';

const t = translator('en');

function row(id: string, over: Partial<ConnectorRow> = {}): ConnectorRow {
    return {
        id,
        display_name: id,
        icon: null,
        category: 'x',
        tier: 'x',
        type: 'oauth_pkce',
        description: `${id} description`,
        product_url: null,
        docs_url: null,
        configured: false,
        configurable: true,
        config_error: null,
        enabled: true,
        account_id: null,
        scopes: [],
        activations: {},
        last_tested_at: null,
        mcp_env_keys: [],
        default_scopes: [],
        available_scopes: [],
        oauth_setup_fields: [],
        ...over,
    };
}

const broken = (id: string, over: Partial<UnavailableConnectorRow> = {}): UnavailableConnectorRow => ({
    id,
    display_name: id,
    error: 'unavailable',
    ...over,
});

describe('groupConnectors', () => {
    const all: ConnectorListEntry[] = [
        row('google', { configured: true, account_id: 'me@example.com' }),
        row('microsoft'),
        row('github', { type: 'mcp_server' }),
        broken('slack'),
    ];

    it('splits by state, kind and readability', () => {
        const g = groupConnectors(all, '', t);
        expect(g.connected.map((c) => c.id)).toEqual(['google']);
        expect(g.accounts.map((c) => c.id)).toEqual(['microsoft']);
        expect(g.servers.map((c) => c.id)).toEqual(['github']);
        expect(g.unavailable.map((c) => c.id)).toEqual(['slack']);
        expect(g.nothingConnected).toBe(false);
    });

    it('finds a mail account by a word its name never says', () => {
        expect(groupConnectors(all, 'inbox', t).accounts.map((c) => c.id)).toEqual(['microsoft']);
    });

    it('searches unavailable connectors by name only', () => {
        const g = groupConnectors(all, 'description', t);
        expect(g.unavailable).toEqual([]);
        expect(g.connected.length + g.accounts.length + g.servers.length).toBe(3);
    });
});

describe('tileStatus', () => {
    it('says what the tile header shows', () => {
        expect(tileStatus(row('a', { configured: true, account_id: 'x' }))).toEqual({ kind: 'connected', accountId: 'x' });
        expect(tileStatus(row('a', { configured: true, type: 'mcp_server', enabled: false }))).toEqual({ kind: 'paused' });
        expect(tileStatus(row('a', { configurable: false }))).toEqual({ kind: 'setupRequired' });
        expect(tileStatus(row('a'))).toEqual({ kind: 'none' });
    });
});

describe('unavailableHint', () => {
    it('names the settings file when that is what GAIA could not read', () => {
        const hint = unavailableHint([broken('a', { reason: 'activations_unreadable', path: '/home/x/activations.json' })], t);
        expect(hint).toContain('/home/x/activations.json');
        expect(unavailableHint([broken('a')], t)).toBe(t('settings.connectors.unavailableHint'));
    });
});
