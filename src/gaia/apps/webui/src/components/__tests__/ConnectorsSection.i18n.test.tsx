// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// A built-in connector's description, field labels and setup help follow the
// interface language (#6860); text the catalog doesn't ship shows as sent.

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../services/api';
import { stripIsolates } from '../../i18n/registry';
import { ar } from '../../i18n/locales/ar';
import { enMessages } from '../../i18n/locales/en';
import { useChatStore } from '../../stores/chatStore';
import type { ConnectorRow } from '../../types';
import { ConnectorsSection } from '../ConnectorsSection';

vi.mock('../../services/api');
vi.mock('../../hooks/useConnectorsSSE', () => ({ useConnectorsSSE: () => {} }));

const mocked = vi.mocked(api);
const arMessages = ar.messages;

const field = (key: string, required: boolean) => ({
    key,
    label: enMessages[`connectors.microsoft_work.${key}.label` as keyof typeof enMessages],
    kind: 'text' as const,
    required,
    placeholder: '',
    help_md: enMessages[`connectors.microsoft_work.${key}.help` as keyof typeof enMessages],
});

function workRow(over: Partial<ConnectorRow> = {}): ConnectorRow {
    return {
        id: 'microsoft_work', display_name: 'Microsoft Work or School', type: 'oauth_pkce',
        description: enMessages['connectors.microsoft_work.description'], icon: null, category: 'productivity',
        tier: 1, product_url: null, configured: false, configurable: false, enabled: true, mcp_env_keys: [],
        default_scopes: [], available_scopes: [],
        oauth_setup_fields: [field('client_id', true), field('client_secret', false), field('tenant_id', false)],
        ...over,
    } as ConnectorRow;
}

const shown = () => stripIsolates(document.body.textContent ?? '');

async function openTile(row: ConnectorRow) {
    mocked.listConnectors.mockResolvedValue({ connectors: [row] });
    render(<ConnectorsSection />);
    fireEvent.click(await screen.findByText(row.display_name));
}

beforeEach(() => {
    vi.clearAllMocks();
    mocked.listAgentMcps.mockResolvedValue({ agent_mcps: [] });
});

afterEach(() => {
    act(() => useChatStore.getState().setLocale('en'));
});

describe('connector setup help in English', () => {
    it('explains the tenant ID without issue numbers or developer terms', async () => {
        await openTile(workRow());
        const help = enMessages['connectors.microsoft_work.tenant_id.help'];
        expect(shown()).toContain(help);
        expect(help).not.toMatch(/#\d+|daemon|environment variable|loudly/i);
    });
});

describe('connector catalog text in Arabic', () => {
    beforeEach(() => {
        act(() => useChatStore.getState().setLocale('ar'));
    });

    it('shows the description, field labels and setup help in Arabic', async () => {
        await openTile(workRow());
        const text = shown();
        for (const key of [
            'connectors.microsoft_work.description',
            'connectors.microsoft_work.client_id.label',
            'connectors.microsoft_work.client_id.help',
            'connectors.microsoft_work.tenant_id.label',
            'connectors.microsoft_work.tenant_id.help',
        ] as const) {
            expect(text, key).toContain(arMessages[key]);
        }
    });

    it('names a missing field in each language of the error', async () => {
        await openTile(workRow());
        fireEvent.click(screen.getByRole('button', { name: /Save and connect/ }));
        const text = shown();
        expect(text).toContain(`مطلوب: ${arMessages['connectors.microsoft_work.client_id.label']}`);
        expect(text).toContain('(Required: Application (client) ID)');
    });

    it('shows a custom or reworded description as sent', async () => {
        await openTile(workRow({ description: 'A server this build has never seen.' }));
        expect(shown()).toContain('A server this build has never seen.');
        expect(shown()).not.toContain(arMessages['connectors.microsoft_work.description']);
    });

    it('finds a connector by a word of its Arabic description', async () => {
        const others = ['Notion', 'Postgres', 'Fetch', 'Brave', 'Filesystem', 'Weather'].map((n) =>
            workRow({ id: n, display_name: n, type: 'mcp_server', description: `${n} tools`, oauth_setup_fields: [] }),
        );
        mocked.listConnectors.mockResolvedValue({ connectors: [workRow(), ...others] });
        render(<ConnectorsSection />);
        await screen.findByText('Microsoft Work or School');
        fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'المؤسسة التعليمية' } });
        expect(screen.getByText('Microsoft Work or School')).toBeInTheDocument();
        expect(screen.queryByText('Notion')).not.toBeInTheDocument();
    });
});

describe('connector catalog text in English', () => {
    it('is the catalog text, unchanged', async () => {
        await openTile(workRow());
        expect(shown()).toContain(enMessages['connectors.microsoft_work.client_id.help']);
        expect(shown()).not.toContain(arMessages['connectors.microsoft_work.client_id.help']);
    });
});
