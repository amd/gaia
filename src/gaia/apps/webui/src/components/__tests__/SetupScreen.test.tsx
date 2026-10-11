// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SetupScreen } from '../SetupScreen';
import { useModelStore } from '../../stores/modelStore';
import * as api from '../../services/api';
import type { SetupCheck, SetupRunStatus, SetupStep } from '../../types';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

function steps(server: SetupStep['status'], models: SetupStep['status']): SetupStep[] {
    return [
        { key: 'server', label: 'Install the model server', detail: '', status: server },
        { key: 'models', label: 'Download models', detail: '4 GB', status: models },
    ];
}

const CHECK: SetupCheck = {
    ready: false,
    stage: 'setup',
    reasons: ['The local model server is not installed'],
    // A stale failure from an earlier run must not show before this one starts.
    steps: steps('failed', 'pending'),
};

const READY: SetupRunStatus = { state: 'ready', steps: steps('done', 'done') };
const FAILED: SetupRunStatus = {
    state: 'failed',
    steps: steps('done', 'failed'),
    error: 'Not enough disk space for Gemma-4-E4B-it-GGUF',
    command: 'gaia init --profile chat',
};

beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
    vi.clearAllMocks();
    useModelStore.setState({ active: null, error: null, restoreNotice: null });
    mockedApi.getOnboardingPreflight.mockRejectedValue(new Error('no probe'));
    mockedApi.getSetupStatus.mockResolvedValue({ state: 'idle' });
    mockedApi.startSetup.mockResolvedValue(READY);
});

async function start(choice?: RegExp) {
    render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
    await waitFor(() => expect(mockedApi.getSetupStatus).toHaveBeenCalled());
    if (choice) fireEvent.click(screen.getByRole('radio', { name: choice }));
    fireEvent.click(screen.getByRole('button', { name: 'Start setup' }));
}

describe('SetupScreen', () => {
    it('shows the reason and pending steps without an error before setup starts', async () => {
        render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
        expect(screen.getByText('The local model server is not installed.')).toBeInTheDocument();
        expect(screen.getByText(/Install the model server/)).toBeInTheDocument();
        expect(screen.getByText(/Download models/)).toBeInTheDocument();
        await waitFor(() => expect(mockedApi.getSetupStatus).toHaveBeenCalled());
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument();
    });

    it('installs the local chat model when running on this PC', async () => {
        await start();
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledExactlyOnceWith(false));
        expect(await screen.findByRole('button', { name: 'Start chatting' })).toBeInTheDocument();
        expect(screen.queryByLabelText('API key')).not.toBeInTheDocument();
    });

    it('skips the local chat model for Fireworks AI', async () => {
        await start(/Fireworks AI/);
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledExactlyOnceWith(true));
    });

    it('shows a failed step\'s error with a Try again button', async () => {
        mockedApi.startSetup.mockResolvedValueOnce(FAILED).mockResolvedValueOnce(READY);
        await start();

        expect(await screen.findByRole('alert')).toHaveTextContent('Not enough disk space');
        expect(screen.getByText('gaia init --profile chat')).toBeInTheDocument();
        expect(screen.getAllByRole('alert')).toHaveLength(1);

        fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledTimes(2));
        expect(await screen.findByRole('button', { name: 'Start chatting' })).toBeInTheDocument();
    });

    it('shows why setup could not start', async () => {
        mockedApi.startSetup.mockRejectedValue(new Error('Setup is already running in another window'));
        await start();
        expect(await screen.findByRole('alert')).toHaveTextContent('already running in another window');
    });

    it('asks for the Fireworks key only after the install finishes, then picks the top model', async () => {
        let finish!: (s: SetupRunStatus) => void;
        mockedApi.startSetup.mockImplementation(() => new Promise((r) => { finish = r; }));
        mockedApi.connectProvider.mockResolvedValue({} as Awaited<ReturnType<typeof api.connectProvider>>);
        mockedApi.listProviderModels.mockResolvedValue({
            provider: 'fireworks',
            models: [
                { id: 'fireworks.m2', context_length: null, downloaded: true, labels: [], rank: 2, note: null, evidence: null },
                { id: 'fireworks.m1', context_length: null, downloaded: true, labels: [], rank: 1, note: null, evidence: null },
            ],
        });
        mockedApi.selectModel.mockResolvedValue({ provider: 'fireworks', model: 'fireworks.m1', label: 'm1', remote: true, is_default: false });
        mockedApi.listSessions.mockResolvedValue({ sessions: [], total: 0 });

        await start(/Fireworks AI/);
        expect(screen.getByText('Connect Fireworks AI')).toBeInTheDocument();
        expect(screen.queryByLabelText('API key')).not.toBeInTheDocument();

        finish(READY);
        const key = await screen.findByLabelText('API key');
        expect(key).toHaveAttribute('type', 'password');
        expect(screen.queryByRole('button', { name: 'Start chatting' })).not.toBeInTheDocument();

        fireEvent.change(key, { target: { value: 'fw-key' } });
        fireEvent.click(screen.getByRole('button', { name: /Connect/ }));

        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('fireworks.m1'));
        expect(mockedApi.connectProvider).toHaveBeenCalledWith('fireworks', { api_key: 'fw-key' });
        expect(await screen.findByRole('button', { name: 'Start chatting' })).toBeInTheDocument();
    });

    it('shows the cloud download size and drops the local chat-model reason for a cloud choice', async () => {
        const check: SetupCheck = {
            ...CHECK,
            reasons: ["Model 'Gemma-4-E4B-it-GGUF' is not downloaded", "Model 'user.embeddinggemma-300m-GGUF' is not downloaded"],
            steps: [{ key: 'models', label: 'Download the models', detail: 'about 6 GB', status: 'pending' }],
            cloud_steps: [{ key: 'models', label: 'Download the models', detail: 'the embedding model, under 1 GB', status: 'pending' }],
        };
        render(<SetupScreen initial={check} onReady={vi.fn()} />);
        await waitFor(() => expect(mockedApi.getSetupStatus).toHaveBeenCalled());
        expect(screen.getByText(/about 6 GB/)).toBeInTheDocument();
        expect(screen.getByText("Model 'Gemma-4-E4B-it-GGUF' is not downloaded.")).toBeInTheDocument();

        fireEvent.click(screen.getByRole('radio', { name: /AMD LLM Gateway/ }));
        expect(screen.getByText(/under 1 GB/)).toBeInTheDocument();
        expect(screen.queryByText(/about 6 GB/)).not.toBeInTheDocument();
        expect(screen.getByText("Model 'user.embeddinggemma-300m-GGUF' is not downloaded.")).toBeInTheDocument();
    });

    it('keeps the gateway choice after a reload and lets the user switch provider', async () => {
        await start(/AMD LLM Gateway/);
        await screen.findByLabelText('API key');
        expect(screen.getByText('Connect AMD LLM Gateway')).toBeInTheDocument();
        cleanup();

        // A new tab: sessionStorage is empty, but the run is a finished cloud run.
        sessionStorage.clear();
        mockedApi.getSetupStatus.mockResolvedValue({ ...READY, skip_chat_model: true });
        render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
        expect(await screen.findByText('Connect AMD LLM Gateway')).toBeInTheDocument();

        fireEvent.click(screen.getByRole('radio', { name: 'Fireworks AI' }));
        expect(screen.getByText('Connect Fireworks AI')).toBeInTheDocument();
        expect(screen.getByRole('radio', { name: 'Fireworks AI' })).toHaveAttribute('aria-checked', 'true');
        expect(localStorage.getItem('gaia-setup-choice')).toBe('fireworks');
    });

    it('calls onReady from Start chatting', async () => {
        mockedApi.getActiveModel.mockResolvedValue({ provider: 'local', model: 'Gemma', label: 'Gemma', remote: false, is_default: true });
        const onReady = vi.fn();
        render(<SetupScreen initial={CHECK} onReady={onReady} />);
        await waitFor(() => expect(mockedApi.getSetupStatus).toHaveBeenCalled());
        fireEvent.click(screen.getByRole('button', { name: 'Start setup' }));
        fireEvent.click(await screen.findByRole('button', { name: 'Start chatting' }));
        await waitFor(() => expect(onReady).toHaveBeenCalledOnce());
    });
});
