// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { disconnectConnector } from '../api';
import { accountChoices } from '../../utils/connectorAccounts';

const fetchMock = () => fetch as ReturnType<typeof vi.fn>;
const SEVERAL = {
    detail: {
        error: 'account_choice_required',
        connector_id: 'google',
        choices: ['jane@example.com', 'sam@example.com'],
        message: 'google has 2 connected accounts (jane@example.com, sam@example.com). Choose one to disconnect, or disconnect them all.',
    },
};

describe('disconnectConnector', () => {
    beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
    afterEach(() => {
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
    });

    it('asks for the whole connector without removing several accounts', async () => {
        fetchMock().mockResolvedValueOnce(new Response(null, { status: 204 }));
        await disconnectConnector('google');
        const [url, init] = fetchMock().mock.calls[0];
        expect(url).toMatch(/\/connectors\/google$/);
        expect(init.method).toBe('DELETE');
    });

    it('removes every account only when told to', async () => {
        fetchMock().mockResolvedValueOnce(new Response(null, { status: 204 }));
        await disconnectConnector('google', { all: true });
        expect(fetchMock().mock.calls[0][0]).toMatch(/\/connectors\/google\?all=true$/);
    });

    it('hands back the accounts the server found when it refuses', async () => {
        fetchMock().mockResolvedValueOnce(new Response(JSON.stringify(SEVERAL), {
            status: 409, headers: { 'content-type': 'application/json' },
        }));
        const refused = await disconnectConnector('google').catch((e: unknown) => e);
        expect(accountChoices(refused)).toEqual(['jane@example.com', 'sam@example.com']);
    });

    it('treats any other refusal as an error, not a choice', async () => {
        fetchMock().mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'A sign-in is already running' }), {
            status: 409, headers: { 'content-type': 'application/json' },
        }));
        const refused = await disconnectConnector('google').catch((e: unknown) => e);
        expect(accountChoices(refused)).toBeNull();
    });
});
