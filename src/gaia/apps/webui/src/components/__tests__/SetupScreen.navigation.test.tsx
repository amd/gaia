// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SetupScreen } from '../SetupScreen';
import { useModelStore } from '../../stores/modelStore';
import * as api from '../../services/api';
import type { SetupRunStatus, SetupStep, StartScreen, LemonadeTarget } from '../../types';
import { COLD_BIG, QWEN, progress, runStatus } from '../setup/__tests__/fixtures';
import { NO_BACKENDS, NO_GPU_SPLIT } from '../../test/setupApiMocks';

vi.mock('../../services/api', async (importOriginal) => {
    const real = await importOriginal<typeof api>();
    const mocked = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]));
    return { ...mocked, ApiError: real.ApiError, setupConfirmFrom: real.setupConfirmFrom };
});
const mockedApi = vi.mocked(api);

function steps(server: SetupStep['status'], models: SetupStep['status']): SetupStep[] {
    return [
        { key: 'server', label: 'Install the model server', detail: '', status: server },
        { key: 'models', label: 'Download models', detail: '4 GB', status: models },
    ];
}
const READY: SetupRunStatus = { state: 'ready', steps: steps('done', 'done'), models: [], chat_model: QWEN.id };
const TARGET = { mode: 'gaia', saved_mode: null, source: 'gaia', base_url: null, saved_base_url: null, api_key_stored: false, gaia_installed: false, offer: false, what_gaia_does: '' } as LemonadeTarget;
const START: StartScreen = { cards: [], working_folder: { path: '/w', name: 'w', gaia_folder: false, choosable: true } };

beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
    vi.clearAllMocks();
    useModelStore.setState({ active: null, error: null, restoreNotice: null });
    mockedApi.getOnboardingPreflight.mockRejectedValue(new Error('no probe'));
    mockedApi.getSetupStatus.mockResolvedValue({ state: 'idle' });
    mockedApi.getCloudPrices.mockResolvedValue({ provider: 'fireworks', models: [], source: null, note: '' });
    mockedApi.startSetup.mockResolvedValue(READY);
    mockedApi.getLemonadeTarget.mockResolvedValue(TARGET);
    mockedApi.getSetupPlan.mockResolvedValue(COLD_BIG());
    mockedApi.getGpuMemory.mockResolvedValue(NO_GPU_SPLIT);
    mockedApi.getModelServer.mockResolvedValue(NO_BACKENDS);
    mockedApi.getStartScreen.mockResolvedValue(START);
    mockedApi.listProviders.mockResolvedValue({ providers: [], active: null, lemonade_error: null } as never);
    mockedApi.getActiveModel.mockResolvedValue({ provider: 'local', model: QWEN.id, label: 'Qwen', remote: false, is_default: true });
    mockedApi.connectProviderWaitingForOs.mockResolvedValue({} as never);
    mockedApi.listProviderModels.mockResolvedValue({ provider: 'fireworks', models: [{ id: 'fireworks.m1', context_length: null, downloaded: true, labels: [], rank: 1, note: null, evidence: null }] });
    mockedApi.selectModel.mockResolvedValue({ provider: 'fireworks', model: 'fireworks.m1', label: 'm1', remote: true, is_default: false });
    mockedApi.listSessions.mockResolvedValue({ sessions: [], total: 0 });
});

const rail = () => within(screen.getByRole('navigation', { name: 'Setup steps' }));
const stepNav = () => within(screen.getByRole('navigation', { name: 'Move between setup steps' }));
/** The checks stop on what they found; Continue goes on to the plan. */
async function toPlan() {
    fireEvent.click(await screen.findByRole('button', { name: 'Continue' }));
    await screen.findByRole('region', { name: 'Recommended setup' });
}
const RUNNING = runStatus([progress({ id: QWEN.id, state: 'downloading', bytes_done: 1e9, bytes_total: 23.3e9 })]);

async function cloudRunGoing() {
    mockedApi.startSetup.mockResolvedValue(RUNNING);
    mockedApi.getSetupStatus.mockResolvedValueOnce({ state: 'idle' }).mockResolvedValue(RUNNING);
    render(<SetupScreen onReady={vi.fn()} />);
    await toPlan();
    fireEvent.click(screen.getByRole('radio', { name: 'Use a cloud model' }));
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    await screen.findByRole('heading', { name: 'Connect a cloud provider' });
    fireEvent.change(screen.getByLabelText('Fireworks AI API key'), { target: { value: 'k' } });
    fireEvent.click(screen.getByRole('button', { name: /Test and connect/ }));
    await screen.findByRole('button', { name: 'Cancel' });
    expect(mockedApi.startSetup).toHaveBeenCalledTimes(1);
}

describe('moving between steps during a run', () => {
    it('a reopened key step cannot start a second run while one is going', async () => {
        await cloudRunGoing();
        // Your setup reopens on the screen chosen there: the cloud key.
        fireEvent.click(rail().getByRole('button', { name: /Your setup/ }));
        await screen.findByRole('heading', { name: 'Connect a cloud provider' });
        expect(screen.getByRole('button', { name: /Test and connect/ })).toBeDisabled();
        expect(mockedApi.startSetup).toHaveBeenCalledTimes(1);
    });

    it('Back on a reopened key step keeps the running cloud run cloud', async () => {
        await cloudRunGoing();
        fireEvent.click(rail().getByRole('button', { name: /Your setup/ }));
        await screen.findByRole('heading', { name: 'Connect a cloud provider' });
        fireEvent.click(screen.getByRole('button', { name: 'Back' }));
        await screen.findByRole('heading', { name: 'Your setup' });
        fireEvent.click(rail().getByRole('button', { name: /Download/ }));
        await screen.findByRole('button', { name: 'Cancel' });
        fireEvent.click(rail().getByRole('button', { name: /Your setup/ }));
        expect(await screen.findByRole('heading', { name: 'Connect a cloud provider' })).toBeInTheDocument();
    });

    it('Customize and the cloud choice wait while a run is going', async () => {
        await cloudRunGoing();
        fireEvent.click(rail().getByRole('button', { name: /Your setup/ }));
        await screen.findByRole('heading', { name: 'Connect a cloud provider' });
        fireEvent.click(screen.getByRole('button', { name: 'Back' }));
        await screen.findByRole('heading', { name: 'Your setup' });
        expect(screen.getByRole('button', { name: 'Customize' })).toBeDisabled();
        expect(screen.getByRole('radio', { name: 'Use a cloud model' })).toBeDisabled();
    });

    it('going back from the plan before any download stays on the checks', async () => {
        render(<SetupScreen onReady={vi.fn()} />);
        await toPlan();
        fireEvent.click(stepNav().getByRole('button', { name: 'Previous step' }));
        await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
        expect(screen.getByRole('heading', { name: /^(Checking this PC|This PC is checked)$/ })).toBeInTheDocument();
    });

    it('during a second run, the old Ready screen cannot be reopened', async () => {
        const onReady = vi.fn();
        mockedApi.getSetupStatus.mockResolvedValueOnce({ state: 'idle' }).mockResolvedValue(READY);
        render(<SetupScreen onReady={onReady} />);
        await toPlan();
        fireEvent.click(screen.getByRole('button', { name: /^ ?(Download and start|Test and start)$/ }));
        await screen.findByRole('button', { name: 'Start chatting' });
        mockedApi.startSetup.mockResolvedValue(RUNNING);
        mockedApi.getSetupStatus.mockResolvedValue(RUNNING);
        fireEvent.click(rail().getByRole('button', { name: /Your setup/ }));
        await screen.findByRole('heading', { name: 'Your setup' });
        fireEvent.click(screen.getByRole('button', { name: /^ ?(Download and start|Test and start)$/ }));
        await screen.findByRole('button', { name: 'Cancel' });
        expect(rail().queryByRole('button', { name: /Ready/ })).toBeNull();
        expect(screen.queryByRole('button', { name: 'Start chatting' })).toBeNull();
        expect(onReady).not.toHaveBeenCalled();
    });

    it('opening a step from the rail moves focus to its title', async () => {
        mockedApi.startSetup.mockResolvedValue(RUNNING);
        mockedApi.getSetupStatus.mockResolvedValueOnce({ state: 'idle' }).mockResolvedValue(RUNNING);
        render(<SetupScreen onReady={vi.fn()} />);
        await toPlan();
        fireEvent.click(screen.getByRole('button', { name: /^ ?(Download and start|Test and start)$/ }));
        await screen.findByRole('button', { name: 'Cancel' });
        const b = rail().getByRole('button', { name: /Your setup/ });
        b.focus();
        fireEvent.click(b);
        const title = await screen.findByRole('heading', { name: 'Your setup' });
        await waitFor(() => expect(document.activeElement).toBe(title));
    });
});
