// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SetupScreen } from '../SetupScreen';
import { useModelStore } from '../../stores/modelStore';
import * as api from '../../services/api';
import type { LemonadeFound, LemonadeTarget, PreflightReport, SetupCheck, SetupPlan, SetupRunStatus, SetupStep, StartScreen } from '../../types';

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

const PLAN: SetupPlan = {
    lemonade: { mode: 'gaia', url: 'http://localhost:13305/api/v1', reachable: true },
    chat_model: { id: 'Qwen3.6-35B-A3B-GGUF', user_set: false, size_gb: 23.3, downloaded: false },
    recommended_model: 'Qwen3.6-35B-A3B-GGUF',
    decided_after_server: false,
    memory_gb: 96,
    memory_source: 'AMD iGPU',
    options: [
        { id: 'Qwen3.6-35B-A3B-GGUF', size_gb: 23.3, fits: true, reason: null },
        { id: 'Gemma-4-E4B-it-GGUF', size_gb: 5.6, fits: true, reason: null },
    ],
    models: [],
    download_gb: 29.2,
};

const HARDWARE = {
    compatible: true, blockers: [], warnings: [], ram_gb: 96, gpu_name: 'AMD Radeon 8060S',
    npu_detected: true, disk_free_gb: 400, lemonade_running: true,
} as unknown as PreflightReport;

const START: StartScreen = {
    cards: [{ skill: 'check-in', title: 'Check in', note: 'Pick up where you left off', prompt: 'Good morning.', needs_input: false }],
    working_folder: { path: '/w', name: 'w', gaia_folder: false },
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
    mockedApi.getSetupPlan.mockRejectedValue(new Error('not checked'));
    mockedApi.getStartScreen.mockResolvedValue(START);
});

async function start(choice?: RegExp) {
    render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
    await waitFor(() => expect(mockedApi.getSetupStatus).toHaveBeenCalled());
    if (choice) fireEvent.click(screen.getByRole('radio', { name: choice }));
    fireEvent.click(screen.getByRole('button', { name: 'Start setup' }));
}

describe('SetupScreen', () => {
    it('reads as progress, with pending steps and no error, before setup starts', async () => {
        render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
        expect(screen.getByText('A few minutes of setup, then your first chat.')).toBeInTheDocument();
        expect(screen.queryByText('The local model server is not installed.')).not.toBeInTheDocument();
        expect(screen.getByText(/Install the model server/)).toBeInTheDocument();
        expect(screen.getByText(/Download models/)).toBeInTheDocument();
        await waitFor(() => expect(mockedApi.getSetupStatus).toHaveBeenCalled());
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument();
    });

    it('says it is all set once setup has finished', async () => {
        await start();
        expect(await screen.findByRole('button', { name: 'Start chatting' })).toBeInTheDocument();
        expect(screen.getByText('All set.')).toBeInTheDocument();
    });

    it('on this PC, starts the model server first and downloads only once the model is named', async () => {
        mockedApi.getSetupPlan
            .mockResolvedValueOnce({ ...PLAN, decided_after_server: true, lemonade: { ...PLAN.lemonade, reachable: false } })
            .mockResolvedValue(PLAN);
        mockedApi.startSetup
            .mockResolvedValueOnce({ state: 'ready', server_only: true, steps: steps('done', 'pending') })
            .mockResolvedValueOnce(READY);
        render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
        expect(await screen.findByText(/Nothing large downloads until/)).toBeInTheDocument();

        fireEvent.click(screen.getByRole('button', { name: 'Start setup' }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledExactlyOnceWith(false, true));

        // The server answers: the model that fits, and the whole download, before it starts.
        const download = await screen.findByRole('button', { name: 'Download 29.2 GB and finish setup' });
        expect(screen.getByText('Qwen3.6-35B-A3B-GGUF', { selector: 'strong' })).toBeInTheDocument();
        expect(screen.getByText(/The model server is running/)).toBeInTheDocument();
        expect(mockedApi.startSetup).toHaveBeenCalledTimes(1);

        fireEvent.click(download);
        // The model the screen named is the one setup downloads; it can't re-pick.
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenLastCalledWith(false, false, 'Qwen3.6-35B-A3B-GGUF'));
        expect(await screen.findByRole('button', { name: 'Start chatting' })).toBeInTheDocument();
        expect(screen.queryByLabelText('API key')).not.toBeInTheDocument();
    });

    it('after a reload, a server-only setup that finished goes straight to the model', async () => {
        mockedApi.getSetupStatus.mockResolvedValue({ state: 'ready', server_only: true, steps: steps('done', 'pending') });
        mockedApi.getSetupPlan.mockResolvedValue(PLAN);
        render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
        expect(await screen.findByRole('button', { name: 'Download 29.2 GB and finish setup' })).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Start chatting' })).not.toBeInTheDocument();
    });

    it('shows which model is downloading, how much, how fast and the time left', async () => {
        mockedApi.getSetupPlan.mockResolvedValue(PLAN);
        mockedApi.startSetup.mockResolvedValue({
            state: 'running',
            steps: steps('done', 'active'),
            text: 'Downloading Gemma-4-E4B-it-GGUF',
            percent: 25,
            download: {
                model: 'Gemma-4-E4B-it-GGUF', index: 1, count: 2,
                bytes_done: 1e9, bytes_total: 4e9, speed_bps: 5e7, eta_s: 60,
            },
        });
        mockedApi.getSetupStatus.mockResolvedValueOnce({ state: 'idle' }).mockReturnValue(new Promise(() => {}));
        render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
        fireEvent.click(await screen.findByRole('button', { name: /finish setup/ }));
        expect(await screen.findByText(
            'Downloading Gemma-4-E4B-it-GGUF (1 of 2): 1.0 GB of 4.0 GB · 50 MB/s · about 1 min left',
        )).toBeInTheDocument();
        expect(screen.getByRole('progressbar')).toHaveAttribute('value', '25');
    });

    it('shows the bytes, without a total, for a pull that reports none', async () => {
        mockedApi.getSetupPlan.mockResolvedValue(PLAN);
        mockedApi.startSetup.mockResolvedValue({
            state: 'running',
            steps: steps('done', 'active'),
            text: 'Downloading Gemma-4-E4B-it-GGUF',
            percent: null,
            download: {
                model: 'Gemma-4-E4B-it-GGUF', index: null, count: null,
                bytes_done: 5e8, bytes_total: null, speed_bps: 0, eta_s: null,
            },
        });
        mockedApi.getSetupStatus.mockResolvedValueOnce({ state: 'idle' }).mockReturnValue(new Promise(() => {}));
        render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
        fireEvent.click(await screen.findByRole('button', { name: /finish setup/ }));
        expect(await screen.findByText('Downloading Gemma-4-E4B-it-GGUF: 0.5 GB')).toBeInTheDocument();
        expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    });

    it("won't start a download that doesn't fit the disk, and says why", async () => {
        const problem = 'The models need 29.2 GB and the disk that holds them has 10.0 GB free.';
        mockedApi.getSetupPlan.mockResolvedValue({ ...PLAN, disk_free_gb: 10, disk_problem: problem });
        render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
        expect(await screen.findByRole('alert')).toHaveTextContent(problem);
        expect(screen.getByRole('button', { name: /finish setup/ })).toBeDisabled();
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

        // The top model is picked for them; they can pick another before chatting.
        const picker = screen.getByRole('combobox', { name: 'Cloud chat model' });
        expect(picker).toHaveValue('fireworks.m1');
        fireEvent.change(picker, { target: { value: 'fireworks.m2' } });
        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenLastCalledWith('fireworks.m2'));
        expect(await screen.findByText(/GAIA is ready\./)).toHaveTextContent('m2 answers in the cloud.');
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

    it('shows the cloud download size and drops the chat model step for a cloud choice', async () => {
        const check: SetupCheck = {
            ...CHECK,
            reasons: ["Model 'Gemma-4-E4B-it-GGUF' is not downloaded", "Model 'user.embeddinggemma-300m-GGUF' is not downloaded"],
            steps: [{ key: 'models', label: 'Download the models', detail: 'about 6 GB', status: 'pending' }],
            cloud_steps: [{ key: 'models', label: 'Download the models', detail: 'the embedding model, under 1 GB', status: 'pending' }],
        };
        render(<SetupScreen initial={check} onReady={vi.fn()} />);
        await waitFor(() => expect(mockedApi.getSetupStatus).toHaveBeenCalled());
        expect(screen.getByText(/about 6 GB/)).toBeInTheDocument();
        expect(screen.getByText('Chat model')).toBeInTheDocument();

        fireEvent.click(screen.getByRole('radio', { name: /AMD LLM Gateway/ }));
        expect(screen.getByText(/under 1 GB/)).toBeInTheDocument();
        expect(screen.queryByText(/about 6 GB/)).not.toBeInTheDocument();
        expect(screen.queryByText('Chat model')).not.toBeInTheDocument();
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
        await waitFor(() => expect(onReady).toHaveBeenCalledExactlyOnceWith(undefined));
    });
});

describe('SetupScreen: system check', () => {
    it('lists the device, memory and disk it found', async () => {
        mockedApi.getOnboardingPreflight.mockResolvedValue(HARDWARE);
        render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
        const list = await screen.findByRole('list', { name: 'This PC' });
        expect(list).toHaveTextContent('AMD Radeon 8060S · Ryzen AI NPU');
        expect(list).toHaveTextContent('96 GB');
        expect(list).toHaveTextContent('400 GB free');
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    it('says the device is checked later when no model server runs yet, not that it is missing', async () => {
        mockedApi.getOnboardingPreflight.mockResolvedValue({ ...HARDWARE, gpu_name: null, npu_detected: null, lemonade_running: false });
        render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
        expect(await screen.findByText('Checked once the model server is running')).toBeInTheDocument();
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    it("doesn't warn about the NPU before a model server can be asked", async () => {
        const npu = 'GAIA could not query Lemonade for NPU availability; the NPU result for the recommended model is unknown.';
        mockedApi.getOnboardingPreflight.mockResolvedValue({ ...HARDWARE, lemonade_running: false, warnings: [npu, 'Low memory.'] });
        render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
        expect(await screen.findByText('Low memory.')).toBeInTheDocument();
        expect(screen.queryByText(npu)).not.toBeInTheDocument();
    });

    it('shows a real blocker as an error', async () => {
        mockedApi.getOnboardingPreflight.mockResolvedValue({ ...HARDWARE, compatible: false, blockers: ['Needs 30 GB of disk; 4 GB free.'] });
        render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
        expect(await screen.findByRole('alert')).toHaveTextContent('Needs 30 GB of disk');
    });
});

describe('SetupScreen: the chat model', () => {
    it('names the model setup will download, and its size, before it starts', async () => {
        mockedApi.getSetupPlan.mockResolvedValue(PLAN);
        render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
        expect(await screen.findByText('Qwen3.6-35B-A3B-GGUF', { selector: 'strong' })).toBeInTheDocument();
        expect(screen.getByText(/will answer your chats \(23\.3 GB\)\. This PC has 96 GB for models \(AMD iGPU\)/)).toBeInTheDocument();
        expect(screen.getByText(/chat model Qwen3\.6-35B-A3B-GGUF, plus the embedding model \(29\.2 GB\)/)).toBeInTheDocument();
        expect(screen.queryByText(/about 6 GB/)).not.toBeInTheDocument();
    });

    it('lets the user pick a smaller model before the download', async () => {
        mockedApi.getSetupPlan.mockResolvedValue(PLAN);
        mockedApi.chooseSetupChatModel.mockResolvedValue({
            ...PLAN, chat_model: { id: 'Gemma-4-E4B-it-GGUF', user_set: true, size_gb: 5.6, downloaded: false }, download_gb: 5.9,
        });
        render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
        fireEvent.click(await screen.findByRole('radio', { name: /Gemma-4-E4B-it/ }));
        await waitFor(() => expect(mockedApi.chooseSetupChatModel).toHaveBeenCalledExactlyOnceWith('Gemma-4-E4B-it-GGUF'));
        await waitFor(() => expect(screen.getByRole('radio', { name: /Gemma-4-E4B-it/ })).toHaveAttribute('aria-checked', 'true'));
        expect(screen.getByText(/\(5\.9 GB\)/)).toBeInTheDocument();
    });

    it("offers no choice while the model server can't be asked", async () => {
        mockedApi.getSetupPlan.mockResolvedValue({
            ...PLAN, lemonade: { ...PLAN.lemonade, reachable: false },
            chat_model: { ...PLAN.chat_model, user_set: true },
            options: PLAN.options.map((o) => ({ ...o, fits: null })),
        });
        render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
        expect(await screen.findByText('Qwen3.6-35B-A3B-GGUF', { selector: 'strong' })).toBeInTheDocument();
        expect(screen.queryByRole('radiogroup', { name: 'Chat model' })).not.toBeInTheDocument();
    });

    it("never offers to replace the user's own default_model", async () => {
        mockedApi.getSetupPlan.mockResolvedValue({
            ...PLAN, chat_model: { id: 'user.Qwen3.8-Flash-Next-GGUF', user_set: true, size_gb: 82.9, downloaded: true },
        });
        render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
        expect(await screen.findByText(/your default_model; change it in Settings/)).toBeInTheDocument();
        expect(screen.queryByRole('radiogroup', { name: 'Chat model' })).not.toBeInTheDocument();
    });

    it('says "You chose" once the user picked the model', async () => {
        mockedApi.getSetupPlan.mockResolvedValue(PLAN);
        mockedApi.chooseSetupChatModel.mockResolvedValue({
            ...PLAN, chat_model: { id: 'Gemma-4-E4B-it-GGUF', user_set: true, size_gb: 5.6, downloaded: false },
        });
        mockedApi.startSetup.mockResolvedValue({ state: 'running', chat_model: 'Gemma-4-E4B-it-GGUF', steps: [] });
        mockedApi.getSetupStatus.mockResolvedValueOnce({ state: 'idle' })
            .mockResolvedValue({ state: 'running', chat_model: 'Gemma-4-E4B-it-GGUF', steps: [] });
        render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
        fireEvent.click(await screen.findByRole('radio', { name: /Gemma-4-E4B-it/ }));
        await waitFor(() => expect(screen.getByRole('radio', { name: /Gemma-4-E4B-it/ })).toHaveAttribute('aria-checked', 'true'));
        fireEvent.click(await screen.findByRole('button', { name: /finish setup/ }));
        expect(await screen.findByText(/You chose/)).toHaveTextContent('You chose Gemma-4-E4B-it-GGUF.');
        expect(screen.queryByText(/Setup chose/)).not.toBeInTheDocument();
    });

    it("greys out a model this PC can't hold, with the reason", async () => {
        mockedApi.getSetupPlan.mockResolvedValue({
            ...PLAN,
            options: [PLAN.options[0], PLAN.options[1], { id: 'Big-GGUF', size_gb: 80, fits: false, reason: 'needs ~84 GB of memory' }],
        });
        render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
        const big = await screen.findByRole('radio', { name: /Big/ });
        expect(big).toBeDisabled();
        expect(big).toHaveTextContent('needs ~84 GB of memory');
    });

    it("says the pick comes once GAIA's own server is running, without claiming a size", async () => {
        mockedApi.getSetupPlan.mockResolvedValue({
            ...PLAN, decided_after_server: true, download_gb: 5.9, lemonade: { ...PLAN.lemonade, reachable: false },
        });
        render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
        expect(await screen.findByText(/Picked once the model server is running/)).toBeInTheDocument();
        expect(screen.queryByRole('radio', { name: /Gemma-4-E4B-it/ })).not.toBeInTheDocument();
        expect(screen.queryByText(/5\.9 GB/)).not.toBeInTheDocument();
    });

    it('names the model setup actually chose while it runs, and why it passed over a larger one', async () => {
        const reason = 'not enough free memory right now: it needs about 26.1 GB and only 20.4 GB is free';
        mockedApi.getSetupPlan.mockResolvedValue(PLAN);
        const running: SetupRunStatus = {
            state: 'running',
            chat_model: 'Gemma-4-E4B-it-GGUF',
            passed_over: [{ id: 'Qwen3.6-35B-A3B-GGUF', reason }],
            steps: [{ key: 'models', label: 'Download the models', detail: 'chat model Gemma-4-E4B-it-GGUF, plus the embedding model', status: 'active' }],
        };
        mockedApi.startSetup.mockResolvedValue(running);
        mockedApi.getSetupStatus.mockResolvedValueOnce({ state: 'idle' }).mockResolvedValue(running);
        render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
        fireEvent.click(await screen.findByRole('button', { name: /finish setup/ }));
        expect(await screen.findByText(/Setup chose/)).toHaveTextContent('Setup chose Gemma-4-E4B-it-GGUF for this PC.');
        expect(screen.getByText(`Not Qwen3.6-35B-A3B-GGUF: ${reason}.`)).toBeInTheDocument();
    });

    it('says why the plan passed over a larger model before the download', async () => {
        const reason = 'not enough free memory right now: it needs about 26.1 GB and only 20.4 GB is free';
        mockedApi.getSetupPlan.mockResolvedValue({
            ...PLAN,
            chat_model: { id: 'Gemma-4-E4B-it-GGUF', user_set: false, size_gb: 5.6, downloaded: false },
            recommended_model: 'Gemma-4-E4B-it-GGUF',
            passed_over: [{ id: 'Qwen3.6-35B-A3B-GGUF', reason }],
            options: [{ ...PLAN.options[0], fits: false, reason }, PLAN.options[1]],
            download_gb: 5.9,
        });
        render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
        expect(await screen.findByText(`Not Qwen3.6-35B-A3B-GGUF: ${reason}.`)).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Download 5.9 GB and finish setup' })).toBeInTheDocument();
    });
});

describe('SetupScreen: Use mine', () => {
    it('drops the install step and names the server once the user picks theirs', async () => {
        mockedApi.getLemonadeTarget.mockResolvedValue(TARGET);
        mockedApi.detectLemonade.mockResolvedValue({ found: FOUND });
        mockedApi.setLemonadeTarget.mockResolvedValue({ ...TARGET, daemon: null });
        mockedApi.getSetupPlan
            .mockResolvedValueOnce({ ...PLAN, decided_after_server: true, lemonade: { ...PLAN.lemonade, reachable: false } })
            .mockResolvedValueOnce({ ...PLAN, lemonade: { mode: 'external', url: FOUND.url, reachable: true } });
        render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
        expect(await screen.findByText(/Install the model server/)).toBeInTheDocument();

        fireEvent.click(await screen.findByRole('button', { name: 'Use mine' }));
        expect(await screen.findByText('Use your Lemonade Server')).toBeInTheDocument();
        expect(screen.queryByText(/Install the model server/)).not.toBeInTheDocument();
        expect(screen.getByText('Qwen3.6-35B-A3B-GGUF', { selector: 'strong' })).toBeInTheDocument();
        expect(mockedApi.getSetupPlan).toHaveBeenCalledTimes(2);
        // The device is read again now that a model server answers.
        expect(mockedApi.getOnboardingPreflight).toHaveBeenCalledTimes(2);
    });

    it('keeps the newest plan when an older request answers last', async () => {
        mockedApi.getLemonadeTarget.mockResolvedValue(TARGET);
        mockedApi.detectLemonade.mockResolvedValue({ found: FOUND });
        mockedApi.setLemonadeTarget.mockResolvedValue({ ...TARGET, daemon: null });
        let answerFirst!: (p: SetupPlan) => void;
        mockedApi.getSetupPlan
            .mockImplementationOnce(() => new Promise((r) => { answerFirst = r; }))
            .mockResolvedValueOnce({ ...PLAN, lemonade: { mode: 'external', url: FOUND.url, reachable: true } });
        render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
        fireEvent.click(await screen.findByRole('button', { name: 'Use mine' }));
        expect(await screen.findByText('Use your Lemonade Server')).toBeInTheDocument();

        answerFirst({ ...PLAN, decided_after_server: true });
        await new Promise((r) => setTimeout(r, 0));
        expect(screen.getByText('Use your Lemonade Server')).toBeInTheDocument();
        expect(screen.queryByText(/Picked once the model server is running/)).not.toBeInTheDocument();
    });
});

describe('SetupScreen: ready', () => {
    it('names the model setup reported, over the one the plan expected', async () => {
        mockedApi.getSetupPlan.mockResolvedValue(PLAN);
        mockedApi.startSetup.mockResolvedValue({ ...READY, chat_model: 'Gemma-4-E4B-it-GGUF' });
        render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
        await waitFor(() => expect(mockedApi.getSetupStatus).toHaveBeenCalled());
        fireEvent.click(await screen.findByRole('button', { name: /finish setup/ }));
        expect(await screen.findByText(/GAIA is ready\./)).toHaveTextContent('GAIA is ready. Gemma-4-E4B-it-GGUF answers on this PC.');
    });

    it('names the one model that answers and offers a first task', async () => {
        mockedApi.getSetupPlan.mockResolvedValue(PLAN);
        mockedApi.startSetup.mockResolvedValue({ ...READY, chat_model: 'Qwen3.6-35B-A3B-GGUF' });
        mockedApi.getActiveModel.mockResolvedValue({ provider: 'local', model: 'Qwen3.6-35B-A3B-GGUF', label: 'Qwen', remote: false, is_default: true });
        const onReady = vi.fn();
        render(<SetupScreen initial={CHECK} onReady={onReady} />);
        await waitFor(() => expect(mockedApi.getSetupStatus).toHaveBeenCalled());
        fireEvent.click(await screen.findByRole('button', { name: /finish setup/ }));

        const done = await screen.findByText(/GAIA is ready\./);
        expect(done).toHaveTextContent('GAIA is ready. Qwen3.6-35B-A3B-GGUF answers on this PC.');
        fireEvent.click(await screen.findByRole('button', { name: /Check in/ }));
        await waitFor(() => expect(onReady).toHaveBeenCalledExactlyOnceWith(START.cards[0]));
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

    it("says it couldn't check a server that didn't answer in time, and checks again", async () => {
        mockedApi.getLemonadeTarget.mockResolvedValue(TARGET);
        mockedApi.detectLemonade
            .mockResolvedValueOnce({ found: null, unchecked: [FOUND.url] })
            .mockResolvedValueOnce({ found: FOUND, unchecked: [] });
        render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
        expect(await screen.findByText(/Couldn.t check whether your Lemonade Server at http:\/\/localhost:13305\/api\/v1 is running/)).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: /Check again/ }));
        expect(await screen.findByRole('button', { name: 'Use mine' })).toBeInTheDocument();
        expect(screen.queryByText(/Couldn.t check/)).not.toBeInTheDocument();
    });

    it('says it is looking while detection runs', async () => {
        mockedApi.getLemonadeTarget.mockResolvedValue(TARGET);
        let answer!: (r: { found: LemonadeFound | null; unchecked: string[] }) => void;
        mockedApi.detectLemonade.mockImplementation(() => new Promise((r) => { answer = r; }));
        render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
        expect(await screen.findByText(/Looking for a Lemonade Server you already run/)).toBeInTheDocument();
        answer({ found: null, unchecked: [] });
        await waitFor(() => expect(screen.queryByText(/Looking for a Lemonade/)).not.toBeInTheDocument());
    });

    it('stays quiet when no server is running', async () => {
        mockedApi.getLemonadeTarget.mockResolvedValue(TARGET);
        mockedApi.detectLemonade.mockResolvedValue({ found: null, unchecked: [] });
        render(<SetupScreen initial={CHECK} onReady={vi.fn()} />);
        await waitFor(() => expect(mockedApi.detectLemonade).toHaveBeenCalled());
        expect(screen.queryByText(/Couldn.t check/)).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Use mine' })).not.toBeInTheDocument();
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
