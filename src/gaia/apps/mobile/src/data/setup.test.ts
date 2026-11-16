// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { createFakeClient } from '@/api/fake';
import type { ModelServerInfo, SetupCheck } from '@/api/client.types';
import { GaiaNetworkError } from '@/api/errors';

import { applyTestedBackend, gateFromCheck, runSetupCheck } from './setup';

const check = (c: Partial<SetupCheck>): SetupCheck => ({ ready: false, steps: [], ...c });
const instant = async () => {};

describe('gateFromCheck', () => {
    it('is ready when the check says so', () => {
        expect(gateFromCheck(check({ ready: true }))).toEqual({ state: 'ready' });
    });

    it('treats an unreachable server on a PC set up before as an outage, not a first run', () => {
        expect(gateFromCheck(check({ stage: 'server', initialized: true, model_list_unread: true }))).toEqual({
            state: 'serverDown',
            modelListUnread: true,
        });
        expect(gateFromCheck(check({ stage: 'server', initialized: false })).state).toBe('needed');
    });

    it('needs setup for any other unfinished stage', () => {
        expect(gateFromCheck(check({ stage: 'setup' })).state).toBe('needed');
    });
});

describe('runSetupCheck', () => {
    it('waits out a refused connection only at launch', async () => {
        const refused = new GaiaNetworkError('http://pc', new Error('refused'));
        const client = createFakeClient({ setupChecks: [refused, check({ ready: true })] });
        expect(await runSetupCheck(client, { waitForStart: true, sleep: instant })).toEqual({ state: 'ready' });

        const again = createFakeClient({ setupChecks: [refused, check({ ready: true })] });
        expect((await runSetupCheck(again, { sleep: instant })).state).toBe('unknown');
    });

    it('reports a check it could not ask as unknown, never as not set up', async () => {
        const client = createFakeClient({});
        expect(await runSetupCheck(client, { sleep: instant })).toMatchObject({ state: 'unknown', upgradeFailed: false });
    });

    it('starts the embedded update when none is running, waits for it, and checks again', async () => {
        const client = createFakeClient({
            setupChecks: [check({ stage: 'upgrade' }), check({ ready: true })],
            setupStatuses: [{ state: 'idle' }, { state: 'running' }, { state: 'ready' }],
        });
        let upgrading = false;
        const gate = await runSetupCheck(client, { sleep: instant, onUpgrading: () => { upgrading = true; } });
        expect(gate).toEqual({ state: 'ready' });
        expect(upgrading).toBe(true);
        expect(client.setupStarts).toEqual([{ skipChatModel: false, gaiaLemonade: false, upgradeEmbedded: true, serverOnly: false }]);
    });

    it('follows an update already running instead of starting another', async () => {
        const client = createFakeClient({
            setupChecks: [check({ stage: 'upgrade' }), check({ ready: true })],
            setupStatuses: [{ state: 'running', upgrade_embedded: true }, { state: 'ready' }],
        });
        await runSetupCheck(client, { sleep: instant });
        expect(client.setupStarts).toEqual([]);
    });

    it('says the update failed, with the run\'s own words', async () => {
        const client = createFakeClient({
            setupChecks: [check({ stage: 'upgrade' })],
            setupStatuses: [{ state: 'idle' }, { state: 'failed', error: 'disk full' }],
        });
        const gate = await runSetupCheck(client, { sleep: instant });
        expect(gate).toMatchObject({ state: 'unknown', upgradeFailed: true });
        expect((gate as { error: Error }).error.message).toBe('disk full');
    });
});

describe('applyTestedBackend', () => {
    const server = (s: Partial<ModelServerInfo>): ModelServerInfo => ({ base_url: 'http://x', owner: 'gaia', reachable: true, ...s });
    const run = async (s: Partial<ModelServerInfo>) => {
        const client = createFakeClient({ modelServer: server(s) });
        const set = await applyTestedBackend(client);
        return { set, saved: client.backendSets };
    };
    const vulkan = [{ name: 'vulkan' }, { name: 'cpu' }] as ModelServerInfo['available_backends'];

    it('saves vulkan when the PC lists it and it is not already the pick', async () => {
        expect(await run({ available_backends: vulkan, default_backend: 'rocm' })).toEqual({ set: true, saved: ['vulkan'] });
        expect(await run({ available_backends: vulkan, preferred_backend: 'cpu', default_backend: 'vulkan' })).toEqual({ set: true, saved: ['vulkan'] });
    });

    it('leaves the backend alone when it is vulkan, vulkan is missing, or the server is down', async () => {
        expect((await run({ available_backends: vulkan, preferred_backend: 'vulkan' })).saved).toEqual([]);
        expect((await run({ available_backends: vulkan, default_backend: 'vulkan' })).saved).toEqual([]);
        expect((await run({ available_backends: [{ name: 'cpu' }] as ModelServerInfo['available_backends'] })).saved).toEqual([]);
        expect((await run({ available_backends: vulkan, reachable: false })).saved).toEqual([]);
    });
});
