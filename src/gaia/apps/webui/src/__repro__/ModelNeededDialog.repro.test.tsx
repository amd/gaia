// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../services/api';
import { useModelNeededStore } from '../stores/modelNeededStore';
import type { ModelNeed, SetupRunStatus } from '../types';
import { ModelNeededDialog } from '../components/ModelNeededDialog';
import { progress, runStatus } from '../components/setup/__tests__/fixtures';

vi.mock('../services/api', async (importOriginal) => {
    const actual = await importOriginal<typeof import('../services/api')>();
    return { ...actual, downloadSetupModel: vi.fn(), getSetupStatus: vi.fn(), cancelSetup: vi.fn(), getModelNeed: vi.fn() };
});
const mocked = vi.mocked(api);

const EMBED_NEED: ModelNeed = {
    job: 'embedding', needed: true,
    model: { id: 'embed-model', name: 'Embed', size_gb: 0.3, source: null }, disk_free_gb: 400,
};
const CHAT_NEED: ModelNeed = {
    job: 'chat', needed: true,
    model: { id: 'chat-model', name: 'Chat', size_gb: 6, source: null }, disk_free_gb: 400,
};

beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mocked.cancelSetup.mockResolvedValue({ cancelled: true });
});
afterEach(() => {
    vi.useRealTimers();
    act(() => useModelNeededStore.setState({ need: null }));
});

describe('repro: ModelNeededDialog', () => {
    it('"Not now" while the download request is in flight leaves no poller behind to answer the next prompt', async () => {
        let started!: (s: SetupRunStatus) => void;
        mocked.downloadSetupModel.mockImplementation(() => new Promise((r) => { started = r; }));
        // The embedding download carries on (the cancel arrived before it existed).
        mocked.getSetupStatus.mockResolvedValue(runStatus([progress({ id: 'embed-model', state: 'ready' })]));
        mocked.getModelNeed.mockImplementation(async (job) => (job === 'chat' ? CHAT_NEED : EMBED_NEED));

        render(<ModelNeededDialog />);
        let first: boolean | undefined;
        await act(async () => { void useModelNeededStore.getState().ensure('embedding').then((go) => { first = go; }); });
        fireEvent.click(screen.getByRole('button', { name: 'Download and continue' }));
        fireEvent.click(screen.getByRole('button', { name: 'Not now' }));
        await act(async () => {});
        expect(first).toBe(false);

        // The user then sends a chat; its model is missing too, so a new prompt opens.
        let chat: boolean | undefined;
        await act(async () => { void useModelNeededStore.getState().ensure('chat').then((go) => { chat = go; }); });
        expect(useModelNeededStore.getState().need?.job).toBe('chat');

        // The earlier request now answers.
        await act(async () => { started({} as SetupRunStatus); });
        await act(async () => { await vi.advanceTimersByTimeAsync(2500); });

        expect.soft(mocked.getSetupStatus).not.toHaveBeenCalled();
        expect.soft(chat, "chat send released although its model was never downloaded").toBeUndefined();
    });
});
