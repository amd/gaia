// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SetupScreen } from '../SetupScreen';
import { useModelStore } from '../../stores/modelStore';
import * as api from '../../services/api';
import type { LemonadeFound, LemonadeTarget, SetupCheck, SetupRunStatus, SetupStep } from '../../types';

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

const TARGET: LemonadeTarget = {
    mode: 'gaia', saved_mode: null, source: 'gaia', base_url: null, saved_base_url: null,
    api_key_stored: false, gaia_installed: false, offer: true,
    what_gaia_does: 'GAIA registers its embedding model on your server.',
};
const FOUND: LemonadeFound = {
    url: 'http://localhost:13305/api/v1', status: 'ok', version: '10.3.0', minimum: '10.0.0',
    message: 'Lemonade Server v10.3.0 is running at http://localhost:13305/api/v1.',
};

beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
    vi.clearAllMocks();
    useModelStore.setState({ active: null, error: null, restoreNotice: null });
    mockedApi.getOnboardingPreflight.mockRejectedValue(new Error('no probe'));
    mockedApi.getSetupStatus.mockResolvedValue({ state: 'idle' });
    mockedApi.getCloudPrices.mockResolvedValue({ provider: 'fireworks', models: [], source: null, note: 'Prices are set by the organization that runs the gateway.' });
    mockedApi.getSpending.mockResolvedValue({
        month: '2026-10', total_usd: 0, turns: 0, unpriced_turns: 0, limit_usd: null, state: 'no_limit', by_model: [], price_source: 'list price',
    });
    mockedApi.startSetup.mockResolvedValue(READY);
    mockedApi.getLemonadeTarget.mockResolvedValue({ ...TARGET, offer: false });
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

    it('stops naming the missing model once setup has started', async () => {
        await start();
        expect(await screen.findByRole('button', { name: 'Start chatting' })).toBeInTheDocument();
        expect(screen.queryByText('The local model server is not installed.')).not.toBeInTheDocument();
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

        mockedApi.getCloudPrices.mockResolvedValue({
            provider: 'fireworks', source: 'Fireworks AI list price', note: null,
            models: [{
                model: 'fireworks.m1', name: 'm1', rank: 1, note: 'best overall',
                price: { input_per_million: 0.3, cached_input_per_million: 0.006, output_per_million: 1.2, source: 'Fireworks AI list price' },
            }],
        });
        finish(READY);
        const key = await screen.findByLabelText('API key');
        expect(key).toHaveAttribute('type', 'password');
        // Prices show before the key is entered (#5258).
        expect(await screen.findByText('$0.30 in · $1.20 out per 1M tokens')).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Start chatting' })).not.toBeInTheDocument();

        fireEvent.change(key, { target: { value: 'fw-key' } });
        fireEvent.click(screen.getByRole('button', { name: /Connect/ }));

        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('fireworks.m1'));
        expect(mockedApi.connectProvider).toHaveBeenCalledWith('fireworks', { api_key: 'fw-key' });
        expect(await screen.findByRole('button', { name: 'Start chatting' })).toBeInTheDocument();
    });

    it("on the user's own Lemonade, connects with no key and says their server is left alone", async () => {
        mockedApi.getLemonadeTarget.mockResolvedValue({
            ...TARGET, mode: 'external', saved_mode: 'external', source: 'config',
            base_url: FOUND.url, saved_base_url: FOUND.url, offer: false,
        });
        mockedApi.connectProvider.mockResolvedValue({} as Awaited<ReturnType<typeof api.connectProvider>>);
        mockedApi.listProviderModels.mockResolvedValue({
            provider: 'amd',
            models: [{ id: 'amd.m1', context_length: null, downloaded: true, labels: [], rank: 1, note: null, evidence: null }],
        });
        mockedApi.selectModel.mockResolvedValue({ provider: 'amd', model: 'amd.m1', label: 'm1', remote: true, is_default: false });

        await start(/AMD LLM Gateway/);
        expect(await screen.findByText(/doesn.t send your server a key or change its setup/)).toBeInTheDocument();
        expect(screen.queryByText(/key goes to GAIA/)).not.toBeInTheDocument();
        expect(screen.queryByLabelText('API key')).not.toBeInTheDocument();

        fireEvent.click(screen.getByRole('button', { name: /Connect/ }));
        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('amd.m1'));
        expect(mockedApi.connectProvider).toHaveBeenCalledWith('amd', { api_key: undefined });
    });

    it('still asks for the key when LEMONADE_BASE_URL picks the server', async () => {
        mockedApi.getLemonadeTarget.mockResolvedValue({
            ...TARGET, mode: 'external', source: 'env', base_url: FOUND.url, offer: false,
        });
        await start(/AMD LLM Gateway/);
        expect(await screen.findByLabelText('API key')).toBeInTheDocument();
        expect(screen.getByText(/key goes to GAIA/)).toBeInTheDocument();
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

describe('SetupScreen: a Lemonade the user already runs', () => {
    function offer(found: LemonadeFound | null) {
        mockedApi.getLemonadeTarget.mockResolvedValue(TARGET);
        mockedApi.detectLemonade.mockResolvedValue({ found });
        mockedApi.setLemonadeTarget.mockResolvedValue({ ...TARGET, daemon: null });
        render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
    }

    it('offers it with what GAIA does there, and saves the URL on Use mine', async () => {
        offer(FOUND);
        expect(await screen.findByText('Use the Lemonade you already run at http://localhost:13305/api/v1 (v10.3.0)')).toBeInTheDocument();
        expect(screen.getByText(TARGET.what_gaia_does)).toBeInTheDocument();
        expect(screen.queryByLabelText('Lemonade API key')).not.toBeInTheDocument();

        fireEvent.click(screen.getByRole('button', { name: 'Use mine' }));
        await waitFor(() => expect(mockedApi.setLemonadeTarget).toHaveBeenCalledExactlyOnceWith(
            { mode: 'external', base_url: FOUND.url, api_key: undefined },
        ));
        expect(await screen.findByText(/GAIA will use your Lemonade Server at http:\/\/localhost:13305\/api\/v1/)).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Use mine' })).not.toBeInTheDocument();
    });

    it("saves GAIA's own when declined", async () => {
        offer(FOUND);
        fireEvent.click(await screen.findByRole('button', { name: /Use GAIA.s own/ }));
        await waitFor(() => expect(mockedApi.setLemonadeTarget).toHaveBeenCalledExactlyOnceWith({ mode: 'gaia' }));
        expect(await screen.findByText('GAIA will set up its own Lemonade Server.')).toBeInTheDocument();
    });

    it('asks for the key a protected server needs, and never shows it back', async () => {
        offer({ ...FOUND, status: 'needs_key' });
        const key = await screen.findByLabelText('Lemonade API key');
        expect(key).toHaveAttribute('type', 'password');
        expect(screen.getByRole('button', { name: 'Use mine' })).toBeDisabled();

        fireEvent.change(key, { target: { value: 'sk-secret' } });
        fireEvent.click(screen.getByRole('button', { name: 'Use mine' }));
        await waitFor(() => expect(mockedApi.setLemonadeTarget).toHaveBeenCalledExactlyOnceWith(
            { mode: 'external', base_url: FOUND.url, api_key: 'sk-secret' },
        ));
        await screen.findByRole('status');
        expect(screen.queryByDisplayValue('sk-secret')).not.toBeInTheDocument();
        expect(document.body.textContent).not.toContain('sk-secret');
    });

    it("shows the server's rejection verbatim and reveals the key field", async () => {
        offer(FOUND);
        const message = 'The Lemonade Server at http://localhost:13305/api/v1 rejected the API key. Check the key it was started with (its LEMONADE_API_KEY).';
        mockedApi.setLemonadeTarget.mockRejectedValue(Object.assign(new Error(message), { reason: 'bad_key' }));

        fireEvent.click(await screen.findByRole('button', { name: 'Use mine' }));
        expect(await screen.findByRole('alert')).toHaveTextContent(message);
        expect(screen.getByLabelText('Lemonade API key')).toBeInTheDocument();
    });

    it('explains a too-old server and offers no way to use it', async () => {
        const message = 'The Lemonade Server at http://localhost:8000/api/v1 is v9.0.0; GAIA needs v10.0.0 or newer.';
        offer({ ...FOUND, status: 'too_old', message });
        expect(await screen.findByText(message)).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Use mine' })).not.toBeInTheDocument();
    });

    it('does not look for one once a choice is saved', async () => {
        render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
        await waitFor(() => expect(mockedApi.getLemonadeTarget).toHaveBeenCalled());
        expect(mockedApi.detectLemonade).not.toHaveBeenCalled();
        expect(screen.queryByRole('button', { name: 'Use mine' })).not.toBeInTheDocument();
    });
});
