// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SetupScreen } from '../SetupScreen';
import { useModelStore } from '../../stores/modelStore';
import * as api from '../../services/api';
import type { LemonadeFound, LemonadeTarget, PreflightReport, SetupPlan, SetupRunStatus, SetupStep, StartScreen } from '../../types';
import { COLD_BIG, EMBED, FLM, GEMMA, GPT_OSS, QWEN, gpuStatus, plan, progress, runStatus } from '../setup/__tests__/fixtures';
import { NO_GPU_SPLIT } from '../../test/setupApiMocks';

vi.mock('../../services/api', async (importOriginal) => {
    const real = await importOriginal<typeof api>();
    const mocked = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]));
    // The error class and its reader stay real, so a 409 reads as one.
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
const FAILED: SetupRunStatus = {
    state: 'failed',
    steps: steps('done', 'failed'),
    error: 'Not enough disk space for Gemma-4-E4B-it-GGUF',
    command: 'gaia init --profile gaia',
    models: [],
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

const COLD_SERVER: SetupPlan = {
    ...COLD_BIG(),
    lemonade: { mode: 'gaia', url: null, reachable: false },
    decided_after_server: true,
    catalog: COLD_BIG().catalog!.map((e) => ({ ...e, downloaded: null, fits: null, need_gb: null, capacity_gb: null })),
};

const HARDWARE = {
    compatible: true, blockers: [], warnings: [], ram_gb: 96, gpu_name: 'AMD Radeon 8060S',
    npu_detected: true, disk_free_gb: 400, lemonade_running: true,
} as unknown as PreflightReport;

const START: StartScreen = {
    cards: [{ skill: 'check-in', title: 'Check in', note: 'Pick up where you left off', prompt: 'Good morning.', needs_input: false }],
    working_folder: { path: '/w', name: 'w', gaia_folder: false, choosable: true },
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
    mockedApi.getSetupPlan.mockResolvedValue(COLD_BIG());
    mockedApi.getGpuMemory.mockResolvedValue(NO_GPU_SPLIT);
    mockedApi.getStartScreen.mockResolvedValue(START);
    mockedApi.listProviders.mockResolvedValue({ providers: [], active: null, lemonade_error: null } as unknown as Awaited<ReturnType<typeof api.listProviders>>);
    mockedApi.getActiveModel.mockResolvedValue({ provider: 'local', model: QWEN.id, label: 'Qwen', remote: false, is_default: true });
});

/** Render and wait for the first reads. */
async function open() {
    const onReady = vi.fn();
    render(<SetupScreen onReady={onReady} />);
    await waitFor(() => expect(mockedApi.getSetupStatus).toHaveBeenCalled());
    await waitFor(() => expect(mockedApi.getSetupPlan).toHaveBeenCalled());
    return onReady;
}

/** Choose where GAIA runs (local unless given), then Continue. */
async function continueFromWhere(choice?: RegExp) {
    if (choice) fireEvent.click(screen.getByRole('radio', { name: choice }));
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
}

/** Local all the way: where, the models step's Continue, and the download starts. */
async function startLocal() {
    const onReady = await open();
    await continueFromWhere();
    await screen.findByRole('group', { name: 'Choose your models' });
    fireEvent.click(screen.getByRole('button', { name: /^ ?Continue$/ }));
    return onReady;
}

const stepNumbers = () => [...document.querySelectorAll('.setup-step-num')].map((n) => n.textContent).filter((x) => x && /^\d+$/.test(x));

describe('SetupScreen: reopened to add local models', () => {
    async function reopen() {
        const onReady = vi.fn();
        const onClose = vi.fn();
        render(<SetupScreen onReady={onReady} onClose={onClose} />);
        await waitFor(() => expect(mockedApi.getSetupStatus).toHaveBeenCalled());
        await waitFor(() => expect(mockedApi.getSetupPlan).toHaveBeenCalled());
        return { onReady, onClose };
    }

    it('offers a way back, which first-run setup does not', async () => {
        await open();
        expect(screen.queryByRole('button', { name: 'Back to GAIA' })).not.toBeInTheDocument();
        cleanup();
        const { onClose } = await reopen();
        fireEvent.click(screen.getByRole('button', { name: 'Back to GAIA' }));
        expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('starts on local models, even after a finished cloud setup', async () => {
        localStorage.setItem('gaia-setup-choice', 'fireworks');
        mockedApi.getSetupStatus.mockResolvedValue({ ...READY, skip_chat_model: true });
        await reopen();
        expect(await screen.findByRole('radio', { name: /On this PC/ })).toBeChecked();
        expect(screen.getByRole('button', { name: 'Continue' })).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Start chatting' })).not.toBeInTheDocument();
    });

    it('picks up a setup still running', async () => {
        mockedApi.getSetupStatus.mockResolvedValue(runStatus([progress({ id: QWEN.id, state: 'downloading', bytes_done: 1e9, bytes_total: 23.3e9 })]));
        await reopen();
        await waitFor(() => expect(document.querySelector('.setup-steps')).toHaveTextContent('Qwen'));
        expect(screen.queryByRole('button', { name: 'Continue' })).not.toBeInTheDocument();
    });

    it('ends on the model it installed, so a cloud user really moves to this PC', async () => {
        mockedApi.selectModel.mockResolvedValue({ provider: 'local', model: QWEN.id, label: 'Qwen', remote: false, is_default: true });
        const { onReady } = await reopen();
        await continueFromWhere();
        await screen.findByRole('group', { name: 'Choose your models' });
        fireEvent.click(screen.getByRole('button', { name: /^ ?Continue$/ }));
        fireEvent.click(await screen.findByRole('button', { name: 'Start chatting' }));
        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith(QWEN.id));
        await waitFor(() => expect(onReady).toHaveBeenCalled());
    });

    it('stays and says so when the switch to the installed model fails', async () => {
        mockedApi.selectModel.mockRejectedValue(new Error('The model did not load'));
        const { onReady } = await reopen();
        await continueFromWhere();
        await screen.findByRole('group', { name: 'Choose your models' });
        fireEvent.click(screen.getByRole('button', { name: /^ ?Continue$/ }));
        fireEvent.click(await screen.findByRole('button', { name: 'Start chatting' }));
        expect(await screen.findByText('The model did not load')).toBeInTheDocument();
        expect(onReady).not.toHaveBeenCalled();
    });

    it('never switches models at the end of first-run setup', async () => {
        const onReady = await startLocal();
        fireEvent.click(await screen.findByRole('button', { name: 'Start chatting' }));
        await waitFor(() => expect(onReady).toHaveBeenCalled());
        expect(mockedApi.selectModel).not.toHaveBeenCalled();
    });
});

describe('SetupScreen: first run, local', () => {
    it('reads as progress before anything starts, with no stale error', async () => {
        await open();
        expect(screen.getByText('A few minutes of setup, then your first chat.')).toBeInTheDocument();
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        expect(screen.getByText('Choose your models')).toBeInTheDocument();
        expect(screen.getByText('Download and check the models')).toBeInTheDocument();
    });

    it('shows the models before any byte downloads; nothing starts until Continue', async () => {
        await open();
        await continueFromWhere();
        expect(await screen.findByRole('group', { name: 'Choose your models' })).toBeInTheDocument();
        expect(screen.getByRole('checkbox', { name: /Qwen3\.6-35B-A3B.*Recommended/ })).toBeChecked();
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
        expect(mockedApi.downloadSetupModel).not.toHaveBeenCalled();
    });

    it('downloads the recommended set with keep-trying off, then says it is ready', async () => {
        await startLocal();
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledExactlyOnceWith(false, false, {
            acknowledgeLowDisk: false, keepTrying: false, extraModels: [],
        }));
        expect(mockedApi.chooseSetupChatModel).not.toHaveBeenCalled();
        expect(mockedApi.skipSetupModel).not.toHaveBeenCalled();
        expect(await screen.findByRole('button', { name: 'Start chatting' })).toBeInTheDocument();
        expect(screen.getByText('All set.')).toBeInTheDocument();
    });

    it('passes keep-trying through only when the user ticks it', async () => {
        await open();
        await continueFromWhere();
        fireEvent.click(await screen.findByRole('checkbox', { name: /Keep trying if the connection drops/ }));
        fireEvent.click(screen.getByRole('button', { name: /^ ?Continue$/ }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledWith(false, false, expect.objectContaining({ keepTrying: true })));
    });

    it('saves a skipped model and an extra one before downloading', async () => {
        mockedApi.skipSetupModel.mockImplementation(async (id, skipped) => {
            const p = COLD_BIG();
            return { ...p, catalog: p.catalog!.map((e) => (e.id === id ? { ...e, skipped, selected: !skipped } : e)) };
        });
        await open();
        await continueFromWhere();
        fireEvent.click(await screen.findByRole('checkbox', { name: /Gemma-4-E4B-it/ }));
        fireEvent.click(screen.getByText('More models GAIA can download (1)'));
        fireEvent.click(screen.getByRole('checkbox', { name: /gpt-oss 20B/ }));
        fireEvent.click(screen.getByRole('button', { name: /^ ?Continue$/ }));
        await waitFor(() => expect(mockedApi.skipSetupModel).toHaveBeenCalledExactlyOnceWith(GEMMA.id, true));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledWith(false, false, expect.objectContaining({ extraModels: [GPT_OSS.id] })));
    });

    it('goes Back from the models with the selection kept, by button and Alt+Left', async () => {
        await open();
        await continueFromWhere();
        fireEvent.click(await screen.findByRole('checkbox', { name: /Gemma-4-E4B-it/ }));
        fireEvent.click(screen.getByRole('button', { name: 'Back' }));
        expect(screen.queryByRole('group', { name: 'Choose your models' })).not.toBeInTheDocument();
        expect(screen.getByRole('radiogroup', { name: 'Where GAIA runs' })).toBeInTheDocument();
        await continueFromWhere();
        expect(await screen.findByRole('checkbox', { name: /Gemma-4-E4B-it/ })).not.toBeChecked();
        fireEvent.keyDown(document, { key: 'ArrowLeft', altKey: true });
        expect(screen.getByRole('radiogroup', { name: 'Where GAIA runs' })).toBeInTheDocument();
    });

    it('numbers the steps contiguously with no GPU step on a discrete GPU', async () => {
        await open();
        await continueFromWhere();
        await screen.findByRole('group', { name: 'Choose your models' });
        expect(screen.queryByText('Give the GPU more memory')).not.toBeInTheDocument();
        expect(stepNumbers()).toEqual(['3', '4']);
        const labels = [...document.querySelectorAll('.setup-step-label')].map((l) => l.textContent);
        expect(labels).toEqual(['Check this PC', 'Choose where GAIA runs', 'Choose your models', 'Download and check the models']);
    });

    it('calls onReady from Start chatting', async () => {
        const onReady = await startLocal();
        fireEvent.click(await screen.findByRole('button', { name: 'Start chatting' }));
        await waitFor(() => expect(onReady).toHaveBeenCalledExactlyOnceWith(undefined));
    });

    it('names the model that answers, lists vision and documents, and offers a first task', async () => {
        const done = (e: typeof QWEN) => ({ ...e, downloaded: true, size_gb: 0 });
        mockedApi.getSetupPlan.mockResolvedValueOnce(COLD_BIG()).mockResolvedValue(plan([done(QWEN), done(GEMMA), done(EMBED), GPT_OSS]));
        const onReady = await startLocal();
        const card = await screen.findByText(/GAIA is ready\./);
        expect(card).toHaveTextContent('GAIA is ready. Qwen3.6-35B-A3B answers on this PC.');
        const summary = await screen.findByText('Vision');
        expect(summary.closest('dl')).toHaveTextContent('ChatQwen3.6-35B-A3BVisionGemma-4-E4B-itDocumentsembeddinggemma-300m');
        fireEvent.click(await screen.findByRole('button', { name: /Check in/ }));
        await waitFor(() => expect(onReady).toHaveBeenCalledExactlyOnceWith(START.cards[0]));
    });

    it('says vision is off in the summary when Gemma was skipped', async () => {
        const done = (e: typeof QWEN) => ({ ...e, downloaded: true, size_gb: 0 });
        mockedApi.getSetupPlan.mockResolvedValueOnce(COLD_BIG())
            .mockResolvedValue(plan([done(QWEN), { ...GEMMA, skipped: true, selected: false }, done(EMBED)]));
        await startLocal();
        expect(await screen.findByText('Off. Add Gemma-4-E4B-it in Settings to read images.')).toBeInTheDocument();
        expect((await screen.findByText('Not installed (add later in Settings)')).closest('div')).toHaveTextContent('Gemma-4-E4B-it');
    });
});

describe('SetupScreen: a cold PC (no model server yet)', () => {
    it('starts only the model server first, then shows the models read from it', async () => {
        mockedApi.getSetupPlan.mockResolvedValueOnce(COLD_SERVER).mockResolvedValue(COLD_BIG());
        mockedApi.startSetup.mockResolvedValueOnce({ state: 'running', server_only: true, text: 'Starting the local model server', steps: [{ key: 'server', label: 'Install the local model server', detail: 'about 1 minute', status: 'active' }] });
        mockedApi.getSetupStatus.mockResolvedValueOnce({ state: 'idle' })
            .mockResolvedValue({ state: 'ready', server_only: true, steps: [{ key: 'server', label: 'Install the local model server', detail: 'about 1 minute', status: 'done' }] });
        await open();
        expect(screen.getByText('Install the local model server')).toBeInTheDocument();
        await continueFromWhere();
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledExactlyOnceWith(false, false, { serverOnly: true }));
        expect(await screen.findByText('Starting the local model server')).toBeInTheDocument();
        expect(screen.queryByRole('group', { name: 'Choose your models' })).not.toBeInTheDocument();
        expect(await screen.findByRole('group', { name: 'Choose your models' }, { timeout: 5000 })).toBeInTheDocument();
        expect(screen.getByRole('checkbox', { name: /Qwen3\.6-35B-A3B/ })).toHaveAccessibleName(/Download 23\.3 GB/);
        expect(mockedApi.startSetup).toHaveBeenCalledTimes(1);
    });

    it('says why the server would not start, with Try again', async () => {
        mockedApi.getSetupPlan.mockResolvedValue(COLD_SERVER);
        mockedApi.startSetup.mockResolvedValueOnce({ state: 'failed', server_only: true, error: 'Lemonade did not start within 20 seconds.', command: 'gaia init --profile gaia', steps: [] })
            .mockResolvedValueOnce({ state: 'running', server_only: true, text: 'Starting the local model server', steps: [] });
        await open();
        await continueFromWhere();
        expect(await screen.findByRole('alert')).toHaveTextContent('Lemonade did not start within 20 seconds.');
        fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenLastCalledWith(false, false, { serverOnly: true }));
    });
});

describe('SetupScreen: warnings and confirmations', () => {
    it('asks before downloading a model too big for memory, with the safe choice focused', async () => {
        const big = { ...QWEN, fits: false as const, need_gb: 27.1, capacity_gb: 16 };
        mockedApi.getSetupPlan.mockResolvedValue(plan([big, GEMMA, EMBED]));
        await open();
        await continueFromWhere();
        fireEvent.click(await screen.findByRole('button', { name: /^ ?Continue$/ }));
        const sheet = await screen.findByRole('alertdialog', { name: 'Qwen3.6-35B-A3B may not fit this PC. Download it anyway?' });
        expect(sheet).toHaveTextContent('Needs about 27.1 GB; this PC has 16.0 GB.');
        expect(within(sheet).getByRole('button', { name: 'Pick a smaller one' })).toHaveFocus();
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
        fireEvent.click(within(sheet).getByRole('button', { name: 'Download anyway' }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalled());
    });

    it('goes back to the list on Pick a smaller one', async () => {
        const big = { ...QWEN, fits: false as const, need_gb: 27.1, capacity_gb: 16 };
        mockedApi.getSetupPlan.mockResolvedValue(plan([big, GEMMA, EMBED]));
        await open();
        await continueFromWhere();
        fireEvent.click(await screen.findByRole('button', { name: /^ ?Continue$/ }));
        fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Pick a smaller one' }));
        expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
        expect(screen.getByRole('group', { name: 'Choose your models' })).toBeInTheDocument();
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
    });

    it('confirms the server’s own no-fit answer before choosing that chat model', async () => {
        mockedApi.chooseSetupChatModel
            .mockRejectedValueOnce(new api.ApiError('x', 409, 'x', null, [], {
                code: 'no_fit', message: 'Gemma-4-E4B-it-GGUF may not run on this PC: too little free memory right now.', acknowledge: 'acknowledge_no_fit',
            }))
            .mockResolvedValueOnce({ ...COLD_BIG(), chat_model: { id: GEMMA.id, user_set: true, size_gb: 5.97, downloaded: false } });
        await open();
        await continueFromWhere();
        fireEvent.change(await screen.findByRole('combobox', { name: 'Default chat model' }), { target: { value: GEMMA.id } });
        fireEvent.click(screen.getByRole('button', { name: /^ ?Continue$/ }));
        const sheet = await screen.findByRole('alertdialog', { name: 'This model may not run on this PC' });
        expect(sheet).toHaveTextContent('too little free memory right now');
        fireEvent.click(within(sheet).getByRole('button', { name: 'Use it anyway' }));
        await waitFor(() => expect(mockedApi.chooseSetupChatModel).toHaveBeenLastCalledWith(GEMMA.id, true));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalled());
    });

    it('downloads past a disk shortfall only after Continue anyway', async () => {
        mockedApi.getSetupPlan.mockResolvedValue(plan([QWEN, GEMMA, EMBED], { disk: { path: 'D:\\hub', free_gb: 11.2, source: 'lemonade' } }));
        await open();
        await continueFromWhere();
        fireEvent.click(await screen.findByRole('button', { name: 'Continue anyway' }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledWith(false, false, expect.objectContaining({ acknowledgeLowDisk: true })));
    });

    it("confirms the server's low-disk answer, then downloads with it acknowledged", async () => {
        mockedApi.startSetup
            .mockRejectedValueOnce(new api.ApiError('x', 409, 'x', null, [], {
                code: 'low_disk', message: 'Needs 29.6 GB; only 20.1 GB free on C:\\hub. Free 9.5 GB.', acknowledge: 'acknowledge_low_disk',
                need_gb: 29.6, free_gb: 20.1, free_up_gb: 9.5, path: 'C:\\hub', drop: [],
            }))
            .mockResolvedValueOnce(READY);
        await startLocal();
        const sheet = await screen.findByRole('alertdialog', { name: 'Not enough free disk space' });
        expect(sheet).toHaveTextContent('Free 9.5 GB.');
        fireEvent.click(within(sheet).getByRole('button', { name: 'Download anyway' }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenLastCalledWith(false, false, expect.objectContaining({ acknowledgeLowDisk: true })));
    });

    it("lets an FLM-only user finish with their NPU model and nothing to download", async () => {
        const p = plan([QWEN, GEMMA, { ...EMBED, downloaded: true, size_gb: 0 }, FLM]);
        mockedApi.getSetupPlan.mockResolvedValue(p);
        const afterChat = { ...p, chat_model: { id: FLM.id, user_set: true, size_gb: 0, downloaded: true }, catalog: p.catalog!.map((e) => (e.id === QWEN.id ? { ...e, planned: false, selected: false } : e.id === FLM.id ? { ...e, planned: true, selected: true } : e)) };
        mockedApi.chooseSetupChatModel.mockResolvedValue(afterChat);
        mockedApi.skipSetupModel.mockImplementation(async (id, skipped) => ({ ...afterChat, catalog: afterChat.catalog.map((e) => (e.id === id ? { ...e, skipped, selected: !skipped } : e)) }));
        await open();
        await continueFromWhere();
        fireEvent.click(await screen.findByRole('checkbox', { name: /Qwen3\.6-35B-A3B/ }));
        fireEvent.click(screen.getByRole('checkbox', { name: /Gemma-4-E4B-it/ }));
        fireEvent.click(screen.getByRole('button', { name: /^ ?Continue$/ }));
        await waitFor(() => expect(mockedApi.chooseSetupChatModel).toHaveBeenCalledWith(FLM.id, false));
        await waitFor(() => expect(mockedApi.skipSetupModel).toHaveBeenCalledWith(GEMMA.id, true));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledWith(false, false, expect.objectContaining({ extraModels: [] })));
    });

    it('goes straight to one screen when everything is already on this PC', async () => {
        const on = (e: typeof QWEN) => ({ ...e, downloaded: true, size_gb: 0 });
        mockedApi.getSetupPlan.mockResolvedValue(plan([on(QWEN), on(GEMMA), on(EMBED)]));
        await open();
        await continueFromWhere();
        expect(await screen.findByText('Everything is already on this PC.')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: /Finish setup/ }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalled());
        expect(await screen.findByRole('button', { name: 'Start chatting' })).toBeInTheDocument();
    });

    it("says the model list couldn't be read, and offers setup's own set as a choice", async () => {
        mockedApi.getSetupPlan.mockRejectedValue(new Error('Lemonade answered but could not list its models: 500'));
        await open();
        await continueFromWhere();
        expect(await screen.findByRole('alert')).toHaveTextContent("Couldn’t read the model list from Lemonade: Lemonade answered but could not list its models: 500");
        fireEvent.click(screen.getByRole('button', { name: 'Use the recommended set' }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledWith(false, false, { keepTrying: false }));
    });
});

describe('SetupScreen: the GPU memory step', () => {
    it('appears on an integrated GPU where a bigger share unlocks a model, and Skip downloads', async () => {
        mockedApi.getGpuMemory.mockResolvedValue(gpuStatus());
        await open();
        await continueFromWhere();
        await screen.findByRole('group', { name: 'Choose your models' });
        expect(screen.getByText('Give the GPU more memory')).toBeInTheDocument();
        const labels = [...document.querySelectorAll('.setup-step-label')].map((l) => l.textContent);
        expect(labels).toEqual(['Check this PC', 'Choose where GAIA runs', 'Choose your models', 'Give the GPU more memory', 'Download and check the models']);
        fireEvent.click(screen.getByRole('button', { name: /^ ?Continue$/ }));
        expect(await screen.findByText('With 64.0 GB for the GPU, Qwen3.6-35B-A3B fits.')).toBeInTheDocument();
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: 'Skip for now' }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalled());
    });

    it('disappears once the user unticks every model it would help', async () => {
        mockedApi.getGpuMemory.mockResolvedValue(gpuStatus({ presets: [{ setting_gb: 64, gpu_gb: 64, unlocks: [QWEN.id] }] }));
        await open();
        await continueFromWhere();
        await screen.findByText('Give the GPU more memory');
        fireEvent.click(screen.getByRole('checkbox', { name: /Qwen3\.6-35B-A3B/ }));
        expect(screen.queryByText('Give the GPU more memory')).not.toBeInTheDocument();
    });
});

describe('SetupScreen: downloading', () => {
    const running = runStatus([
        progress({ id: EMBED.id, roles: ['embedding'], state: 'ready', bytes_done: 0.3e9, bytes_total: 0.3e9 }),
        progress({ id: QWEN.id, state: 'downloading', bytes_done: 7.6e9, bytes_total: 23.3e9 }),
        progress({ id: GEMMA.id, roles: ['vision'], state: 'queued', bytes_total: 5.97e9 }),
    ]);

    it('shows each model and the overall progress, with Cancel instead of Back', async () => {
        mockedApi.startSetup.mockResolvedValue(running);
        mockedApi.getSetupStatus.mockResolvedValueOnce({ state: 'idle' }).mockResolvedValue(running);
        await startLocal();
        expect(await screen.findByRole('progressbar', { name: 'Qwen3.6-35B-A3B, Downloading' })).toBeInTheDocument();
        expect(screen.getByRole('progressbar', { name: 'Overall download' })).toHaveAttribute('aria-valuenow', '26');
        expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument();
    });

    it('retries a failed model from where it stopped, and can skip it', async () => {
        const failed = runStatus([
            progress({ id: EMBED.id, roles: ['embedding'], state: 'ready', bytes_done: 0.3e9, bytes_total: 0.3e9 }),
            progress({ id: QWEN.id, state: 'failed', bytes_done: 14.2e9, bytes_total: 23.3e9, error_kind: 'network', error: 'Connection reset' }),
        ], { state: 'failed', error: 'A model failed' });
        mockedApi.startSetup.mockResolvedValue(failed);
        mockedApi.downloadSetupModel.mockResolvedValue({ ...failed, state: 'failed', models: [failed.models![0], { ...failed.models![1], state: 'downloading' }] });
        await startLocal();
        const retry = await screen.findByRole('button', { name: 'Retry' });
        expect(retry).toHaveAccessibleDescription(/The connection dropped while downloading Qwen3\.6-35B-A3B/);
        fireEvent.click(retry);
        await waitFor(() => expect(mockedApi.downloadSetupModel).toHaveBeenCalledWith(QWEN.id, { keepTrying: false }));
    });

    it('keeps the models that downloaded when a later one fails, and deletes nothing', async () => {
        const failed = runStatus([
            progress({ id: EMBED.id, roles: ['embedding'], state: 'unchecked', bytes_done: 0.3e9, bytes_total: 0.3e9 }),
            progress({ id: QWEN.id, state: 'unchecked', bytes_done: 23.3e9, bytes_total: 23.3e9 }),
            progress({ id: GEMMA.id, roles: ['vision'], state: 'failed', bytes_done: 1e9, bytes_total: 5.97e9, error_kind: 'network', error: 'Connection reset', can_retry: true }),
        ], { state: 'failed', error: 'A model failed' });
        mockedApi.startSetup.mockResolvedValue(failed);
        mockedApi.downloadSetupModel.mockResolvedValue(failed);
        await startLocal();
        const qwen = (await screen.findByText('Qwen3.6-35B-A3B', { selector: '.dl-name bdi' })).closest('li')!;
        expect(within(qwen).getByText('Downloaded and kept. GAIA checks that it loads when you finish setup.')).toBeInTheDocument();
        expect(within(qwen).queryByRole('button')).not.toBeInTheDocument();
        expect(screen.getAllByRole('button', { name: 'Retry' })).toHaveLength(1);
        fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
        await waitFor(() => expect(mockedApi.downloadSetupModel).toHaveBeenCalledWith(GEMMA.id, { keepTrying: false }));
        expect(mockedApi.removeSetupModel).not.toHaveBeenCalled();
    });

    it('offers Finish setup once the only rows left are kept, unchecked models', async () => {
        const stopped = runStatus([
            progress({ id: EMBED.id, roles: ['embedding'], state: 'unchecked', bytes_done: 0.3e9, bytes_total: 0.3e9 }),
            progress({ id: QWEN.id, state: 'unchecked', bytes_done: 23.3e9, bytes_total: 23.3e9 }),
        ], { state: 'failed', error: 'The model server stopped answering.' });
        mockedApi.startSetup.mockResolvedValue(stopped);
        await startLocal();
        expect(await screen.findByRole('button', { name: /Finish setup/ })).toBeInTheDocument();
        // Why it stopped stays on screen in fixed words; the raw text is behind Details.
        const why = screen.getAllByRole('alert').find((a) => a.textContent?.includes('Setup stopped before it could check'))!;
        expect(why).toHaveTextContent('They are kept; Finish setup checks them now.');
        expect(within(why).getByText('The model server stopped answering.').closest('details')).not.toHaveAttribute('open');
        expect(mockedApi.removeSetupModel).not.toHaveBeenCalled();
    });

    it('shows no error once a failed download was retried and every model is done', async () => {
        // The run exited 1 because Qwen failed; the user retried it and it finished.
        const recovered = runStatus([
            progress({ id: EMBED.id, roles: ['embedding'], state: 'unchecked', bytes_done: 0.3e9, bytes_total: 0.3e9 }),
            progress({ id: GEMMA.id, roles: ['vision'], state: 'ready', bytes_done: 5.97e9, bytes_total: 5.97e9 }),
            progress({ id: QWEN.id, state: 'ready', bytes_done: 23.3e9, bytes_total: 23.3e9 }),
        ], { state: 'failed', model_failed: true, error: '`gaia init --profile gaia` failed (exit 1): Failed to download Qwen3.6-35B-A3B-GGUF' });
        mockedApi.startSetup.mockResolvedValue(recovered);
        await startLocal();
        expect(await screen.findByRole('button', { name: /Finish setup/ })).toBeInTheDocument();
        expect(screen.queryAllByRole('alert').filter((a) => a.textContent)).toEqual([]);
        expect(screen.queryByText(/gaia init/)).not.toBeInTheDocument();
    });

    it("keeps the server's remedy beside Finish setup when the server is why it stopped", async () => {
        const stopped = runStatus([
            progress({ id: QWEN.id, state: 'unchecked', bytes_done: 23.3e9, bytes_total: 23.3e9 }),
        ], {
            state: 'failed',
            error: "Your Lemonade Server at 127.0.0.1:9 isn't answering.",
            problem: { kind: 'server_unreachable', message: "Your Lemonade Server at 127.0.0.1:9 isn't answering.", start: 'Start Lemonade Server from the Lemonade tray icon.', use_gaia: true, switch_hint: null },
        });
        mockedApi.startSetup.mockResolvedValue(stopped);
        await startLocal();
        expect(await screen.findByRole('button', { name: /Finish setup/ })).toBeInTheDocument();
        expect(screen.getByText('Start Lemonade Server from the Lemonade tray icon.')).toBeInTheDocument();
    });

    it('deletes and re-downloads a model that failed its load check only after the user confirms', async () => {
        const failed = runStatus([
            progress({ id: QWEN.id, state: 'failed', bytes_done: 23.3e9, bytes_total: 23.3e9, error_kind: 'verify', error: 'would not load', can_retry: true }),
        ], { state: 'failed', error: 'A model would not load' });
        mockedApi.startSetup.mockResolvedValue(failed);
        mockedApi.removeSetupModel.mockResolvedValue({ ...COLD_BIG(), freed_gb: 23.3 });
        mockedApi.downloadSetupModel.mockResolvedValue(failed);
        await startLocal();
        fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));
        expect(mockedApi.removeSetupModel).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: 'Delete and download again' }));
        await waitFor(() => expect(mockedApi.removeSetupModel).toHaveBeenCalledWith(QWEN.id));
        await waitFor(() => expect(mockedApi.downloadSetupModel).toHaveBeenCalledWith(QWEN.id, { keepTrying: false }));
    });

    it('asks before retrying past a disk that is still too full', async () => {
        const failed = runStatus([
            progress({ id: QWEN.id, state: 'failed', bytes_done: 22.6e9, bytes_total: 23.3e9, error_kind: 'disk_full', error: 'No space left on device' }),
        ], { state: 'failed', error: 'A model failed' });
        mockedApi.startSetup.mockResolvedValue(failed);
        mockedApi.downloadSetupModel
            .mockRejectedValueOnce(new api.ApiError('x', 409, 'x', null, [], {
                code: 'low_disk', message: 'Needs 0.7 GB; only 0.1 GB free on C:\hub. Free 0.6 GB.', acknowledge: 'acknowledge_low_disk',
            }))
            .mockResolvedValueOnce({ ...failed, models: [{ ...failed.models![0], state: 'downloading' }] });
        await startLocal();
        fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));
        const sheet = await screen.findByRole('alertdialog', { name: 'Not enough free disk space' });
        expect(mockedApi.downloadSetupModel).toHaveBeenCalledTimes(1);
        expect(mockedApi.downloadSetupModel).toHaveBeenLastCalledWith(QWEN.id, { keepTrying: false });
        fireEvent.click(within(sheet).getByRole('button', { name: 'Download anyway' }));
        await waitFor(() => expect(mockedApi.downloadSetupModel).toHaveBeenLastCalledWith(QWEN.id, { keepTrying: false, acknowledgeLowDisk: true }));
    });

    it('cancels and resumes the whole run with the same choices', async () => {
        const cancelled = runStatus([
            progress({ id: EMBED.id, roles: ['embedding'], state: 'ready', bytes_done: 0.3e9, bytes_total: 0.3e9 }),
            progress({ id: QWEN.id, state: 'cancelled', bytes_done: 0.4e9, bytes_total: 23.3e9 }),
            progress({ id: GEMMA.id, roles: ['vision'], state: 'cancelled', bytes_total: 5.97e9 }),
        ], { state: 'cancelled' });
        const small = runStatus([progress({ id: QWEN.id, state: 'downloading', bytes_done: 0.4e9, bytes_total: 23.3e9 })]);
        mockedApi.startSetup.mockResolvedValueOnce(small).mockResolvedValueOnce(READY);
        mockedApi.cancelSetup.mockResolvedValue({ cancelled: true });
        mockedApi.getSetupStatus.mockResolvedValueOnce({ state: 'idle' }).mockResolvedValue(cancelled);
        await startLocal();
        fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }));
        await waitFor(() => expect(mockedApi.cancelSetup).toHaveBeenCalledWith());
        fireEvent.click(await screen.findByRole('button', { name: 'Resume all' }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledTimes(2));
        expect(mockedApi.startSetup.mock.calls[1]).toEqual(mockedApi.startSetup.mock.calls[0]);
    });

    it('attaches to a download already running when reopened, instead of offering to start', async () => {
        mockedApi.getSetupStatus.mockResolvedValue(running);
        await open();
        expect(await screen.findByRole('progressbar', { name: 'Qwen3.6-35B-A3B, Downloading' })).toBeInTheDocument();
        expect(screen.queryByRole('radiogroup', { name: 'Where GAIA runs' })).not.toBeInTheDocument();
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
    });

    it("offers to resume last time's download on reopen, and never resumes on its own", async () => {
        const half = { ...QWEN, partial: true, partial_gb: 7.6, size_gb: 15.7 };
        mockedApi.getSetupPlan.mockResolvedValue(plan([half, GEMMA, EMBED]));
        await open();
        const banner = await screen.findByText('Last time’s download didn’t finish. Resume?');
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
        fireEvent.click(within(banner.closest('.setup-banner') as HTMLElement).getByRole('button', { name: 'Resume' }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledTimes(1));
    });

    it('says it lost contact with GAIA, dims the rows, and reconnects', async () => {
        mockedApi.startSetup.mockResolvedValue(running);
        mockedApi.getSetupStatus.mockResolvedValueOnce({ state: 'idle' }).mockRejectedValueOnce(new Error('Failed to fetch')).mockResolvedValue(running);
        await startLocal();
        expect(await screen.findByText('Lost contact with GAIA: Failed to fetch', {}, { timeout: 4000 })).toBeInTheDocument();
        expect(document.querySelector('.is-stale')).not.toBeNull();
        fireEvent.click(screen.getByRole('button', { name: 'Reconnect' }));
        await waitFor(() => expect(screen.queryByText(/Lost contact/)).not.toBeInTheDocument());
    });

    it("shows a failed run's error with Try again when no model row explains it", async () => {
        mockedApi.startSetup.mockResolvedValueOnce(FAILED).mockResolvedValueOnce(READY);
        await startLocal();
        expect(await screen.findByRole('alert')).toHaveTextContent('Not enough disk space');
        expect(screen.getByText('gaia init --profile gaia')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledTimes(2));
        expect(await screen.findByRole('button', { name: 'Start chatting' })).toBeInTheDocument();
    });

    it('shows why setup could not start', async () => {
        mockedApi.startSetup.mockRejectedValue(new Error('Setup is already running in another window'));
        await startLocal();
        expect(await screen.findByRole('alert')).toHaveTextContent('already running in another window');
    });
});

describe('SetupScreen: cloud', () => {
    it('lists only the embedding model, then asks for the key after the install', async () => {
        let finish!: (s: SetupRunStatus) => void;
        mockedApi.startSetup.mockImplementation(() => new Promise((r) => { finish = r; }));
        mockedApi.connectProviderWaitingForOs.mockResolvedValue({} as Awaited<ReturnType<typeof api.connectProviderWaitingForOs>>);
        mockedApi.listProviderModels.mockResolvedValue({
            provider: 'fireworks',
            models: [
                { id: 'fireworks.m2', context_length: null, downloaded: true, labels: [], rank: 2, note: null, evidence: null },
                { id: 'fireworks.m1', context_length: null, downloaded: true, labels: [], rank: 1, note: null, evidence: null },
            ],
        });
        mockedApi.selectModel.mockResolvedValue({ provider: 'fireworks', model: 'fireworks.m1', label: 'm1', remote: true, is_default: false });
        mockedApi.listSessions.mockResolvedValue({ sessions: [], total: 0 });
        await open();
        await continueFromWhere(/Fireworks AI/);
        expect(await screen.findByText('Cloud models need no download.')).toBeInTheDocument();
        expect(screen.getAllByRole('checkbox', { name: /embeddinggemma/ })).toHaveLength(1);
        fireEvent.click(screen.getByRole('button', { name: /^ ?Continue$/ }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledWith(true, false, expect.objectContaining({ extraModels: [] })));
        expect(mockedApi.chooseSetupChatModel).not.toHaveBeenCalled();
        expect(screen.getByText('Connect Fireworks AI')).toBeInTheDocument();
        expect(screen.queryByLabelText('API key')).not.toBeInTheDocument();

        finish(READY);
        const key = await screen.findByLabelText('API key');
        expect(key).toHaveAttribute('type', 'password');
        expect(screen.queryByRole('button', { name: 'Start chatting' })).not.toBeInTheDocument();
        fireEvent.change(key, { target: { value: 'fw-key' } });
        fireEvent.click(screen.getByRole('button', { name: /Connect/ }));
        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('fireworks.m1'));
        expect(mockedApi.connectProviderWaitingForOs).toHaveBeenCalledWith('fireworks', { api_key: 'fw-key' }, expect.any(Function), expect.any(Function));
        expect(await screen.findByRole('button', { name: 'Start chatting' })).toBeInTheDocument();
    });

    it('says when the OS is asking to release the saved key, instead of a silent wait', async () => {
        const asking = 'macOS is asking GAIA for keychain access to read your saved Fireworks AI key. Look for a system dialog and click Always Allow; this updates once you answer.';
        mockedApi.connectProviderWaitingForOs.mockImplementation((_p, _b, onWaiting) => {
            onWaiting(asking);
            return new Promise(() => {});
        });
        await open();
        await continueFromWhere(/Fireworks AI/);
        fireEvent.click(await screen.findByRole('button', { name: /^ ?Continue$/ }));
        fireEvent.click(await screen.findByRole('button', { name: /Connect/ }));
        expect(await screen.findByText(/Look for a system dialog/)).toHaveAttribute('role', 'status');
        expect(screen.getByRole('button', { name: /Connect/ })).toBeDisabled();
    });

    it("on the user's own Lemonade, connects with no key and says their server is left alone", async () => {
        mockedApi.getLemonadeTarget.mockResolvedValue({
            ...TARGET, mode: 'external', saved_mode: 'external', source: 'config',
            base_url: FOUND.url, saved_base_url: FOUND.url, offer: false,
        });
        mockedApi.connectProviderWaitingForOs.mockResolvedValue({} as Awaited<ReturnType<typeof api.connectProviderWaitingForOs>>);
        mockedApi.listProviderModels.mockResolvedValue({
            provider: 'amd',
            models: [{ id: 'amd.m1', context_length: null, downloaded: true, labels: [], rank: 1, note: null, evidence: null }],
        });
        mockedApi.selectModel.mockResolvedValue({ provider: 'amd', model: 'amd.m1', label: 'm1', remote: true, is_default: false });
        await open();
        await continueFromWhere(/AMD LLM Gateway/);
        fireEvent.click(await screen.findByRole('button', { name: /^ ?Continue$/ }));
        expect(await screen.findByText(/doesn.t send your server a key or change its setup/)).toBeInTheDocument();
        expect(screen.queryByLabelText('API key')).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: /Connect/ }));
        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('amd.m1'));
        expect(mockedApi.connectProviderWaitingForOs).toHaveBeenCalledWith('amd', { api_key: undefined }, expect.any(Function), expect.any(Function));
    });

    it('keeps the gateway choice after a reload and lets the user switch provider', async () => {
        mockedApi.getSetupStatus.mockResolvedValue({ ...READY, skip_chat_model: true });
        localStorage.setItem('gaia-setup-choice', 'amd');
        render(<SetupScreen onReady={vi.fn()} />);
        expect(await screen.findByText('Connect AMD LLM Gateway')).toBeInTheDocument();
        cleanup();
        // A new tab with no saved choice still knows it was a cloud run.
        localStorage.clear();
        render(<SetupScreen onReady={vi.fn()} />);
        expect(await screen.findByText('Connect Fireworks AI')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('radio', { name: 'AMD LLM Gateway' }));
        expect(screen.getByText('Connect AMD LLM Gateway')).toBeInTheDocument();
        expect(localStorage.getItem('gaia-setup-choice')).toBe('amd');
    });
});

describe('SetupScreen: system check', () => {
    it('lists the device, memory and disk it found', async () => {
        mockedApi.getOnboardingPreflight.mockResolvedValue(HARDWARE);
        render(<SetupScreen onReady={vi.fn()} />);
        const list = await screen.findByRole('list', { name: 'This PC' });
        expect(list).toHaveTextContent('AMD Radeon 8060S · Ryzen AI NPU');
        expect(list).toHaveTextContent('96 GB');
        expect(list).toHaveTextContent('400 GB free');
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    it('says the device is checked later when no model server runs yet, not that it is missing', async () => {
        mockedApi.getOnboardingPreflight.mockResolvedValue({ ...HARDWARE, gpu_name: null, npu_detected: null, lemonade_running: false });
        render(<SetupScreen onReady={vi.fn()} />);
        expect(await screen.findByText('Checked once the model server is running')).toBeInTheDocument();
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    const checkStep = () => screen.getByText('Check this PC').closest('li');

    it('marks the check done only once the device is known', async () => {
        mockedApi.getOnboardingPreflight.mockResolvedValue(HARDWARE);
        render(<SetupScreen onReady={vi.fn()} />);
        await screen.findByRole('list', { name: 'This PC' });
        expect(checkStep()).toHaveClass('is-done');
    });

    it("doesn't mark the check done when the hardware couldn't be read", async () => {
        mockedApi.getOnboardingPreflight.mockRejectedValue(new Error('timed out'));
        render(<SetupScreen onReady={vi.fn()} />);
        await screen.findByText(/couldn.t be read \(timed out\)/);
        expect(checkStep()).not.toHaveClass('is-done');
    });

    it('reads the device again once the model server is up', async () => {
        mockedApi.getOnboardingPreflight.mockResolvedValue({ ...HARDWARE, gpu_name: null, npu_detected: null, lemonade_running: false });
        mockedApi.getSetupPlan.mockResolvedValueOnce(COLD_SERVER).mockResolvedValue(COLD_BIG());
        mockedApi.startSetup.mockResolvedValue({ state: 'ready', server_only: true, steps: [] });
        render(<SetupScreen onReady={vi.fn()} />);
        await screen.findByText('Checked once the model server is running');
        mockedApi.getOnboardingPreflight.mockResolvedValue(HARDWARE);
        fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
        await waitFor(() => expect(mockedApi.getOnboardingPreflight).toHaveBeenCalledTimes(2));
        await waitFor(() => expect(checkStep()).toHaveClass('is-done'));
    });

    it("doesn't warn about the NPU before a model server can be asked", async () => {
        const npu = 'GAIA could not query Lemonade for NPU availability; the NPU result for the recommended model is unknown.';
        mockedApi.getOnboardingPreflight.mockResolvedValue({ ...HARDWARE, lemonade_running: false, warnings: [npu, 'Low memory.'] });
        render(<SetupScreen onReady={vi.fn()} />);
        expect(await screen.findByText('Low memory.')).toBeInTheDocument();
        expect(screen.queryByText(npu)).not.toBeInTheDocument();
    });

    it('shows a real blocker as an error', async () => {
        mockedApi.getOnboardingPreflight.mockResolvedValue({ ...HARDWARE, compatible: false, blockers: ['Needs 30 GB of disk; 4 GB free.'] });
        render(<SetupScreen onReady={vi.fn()} />);
        expect(await screen.findByRole('alert')).toHaveTextContent('Needs 30 GB of disk');
    });
});

describe('SetupScreen: a Lemonade the user already runs', () => {
    function offer(found: LemonadeFound | null) {
        mockedApi.getLemonadeTarget.mockResolvedValue(TARGET);
        mockedApi.detectLemonade.mockResolvedValue({ found });
        mockedApi.setLemonadeTarget.mockResolvedValue({ ...TARGET, daemon: null });
        render(<SetupScreen onReady={vi.fn()} />);
    }

    it('offers it with what GAIA does there, and saves the URL on Use mine', async () => {
        offer(FOUND);
        expect(await screen.findByText('Use the Lemonade you already run at http://localhost:13305/api/v1 (v10.3.0)')).toBeInTheDocument();
        expect(screen.getByText(TARGET.what_gaia_does)).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Use mine' }));
        await waitFor(() => expect(mockedApi.setLemonadeTarget).toHaveBeenCalledExactlyOnceWith(
            { mode: 'external', base_url: FOUND.url, api_key: undefined },
        ));
        expect(await screen.findByText(/GAIA will use your Lemonade Server at http:\/\/localhost:13305\/api\/v1/)).toBeInTheDocument();
    });

    it('drops the install step and reads the models from their server', async () => {
        mockedApi.getSetupPlan
            .mockResolvedValueOnce(COLD_SERVER)
            .mockResolvedValue({ ...COLD_BIG(), lemonade: { mode: 'external', url: FOUND.url, reachable: true } });
        offer(FOUND);
        expect(await screen.findByText('Install the local model server')).toBeInTheDocument();
        fireEvent.click(await screen.findByRole('button', { name: 'Use mine' }));
        await waitFor(() => expect(screen.queryByText('Install the local model server')).not.toBeInTheDocument());
        await continueFromWhere();
        expect(await screen.findByRole('group', { name: 'Choose your models' })).toBeInTheDocument();
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
        expect(mockedApi.getOnboardingPreflight).toHaveBeenCalledTimes(2);
    });

    it('keeps the newest plan when an older request answers last', async () => {
        let answerFirst!: (p: SetupPlan) => void;
        mockedApi.getSetupPlan
            .mockImplementationOnce(() => new Promise((r) => { answerFirst = r; }))
            .mockResolvedValue({ ...COLD_BIG(), lemonade: { mode: 'external', url: FOUND.url, reachable: true } });
        offer(FOUND);
        fireEvent.click(await screen.findByRole('button', { name: 'Use mine' }));
        await screen.findByText(/GAIA will use your Lemonade Server/);
        answerFirst(COLD_SERVER);
        await new Promise((r) => setTimeout(r, 0));
        expect(screen.queryByText('Install the local model server')).not.toBeInTheDocument();
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
        await screen.findByText(/GAIA will use your Lemonade Server/);
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
        render(<SetupScreen onReady={vi.fn()} />);
        expect(await screen.findByText(/Couldn.t check whether your Lemonade Server at http:\/\/localhost:13305\/api\/v1 is running/)).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: /Check again/ }));
        expect(await screen.findByRole('button', { name: 'Use mine' })).toBeInTheDocument();
    });

    it('explains a too-old server and offers no way to use it', async () => {
        const message = 'The Lemonade Server at http://localhost:8000/api/v1 is v9.0.0; GAIA needs v10.0.0 or newer.';
        offer({ ...FOUND, status: 'too_old', message });
        expect(await screen.findByText(message)).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Use mine' })).not.toBeInTheDocument();
    });

    it('does not look for one once a choice is saved', async () => {
        render(<SetupScreen onReady={vi.fn()} />);
        await waitFor(() => expect(mockedApi.getLemonadeTarget).toHaveBeenCalled());
        expect(mockedApi.detectLemonade).not.toHaveBeenCalled();
    });

    it('shows an earlier failed run with its error', async () => {
        mockedApi.getSetupStatus.mockResolvedValue(FAILED);
        render(<SetupScreen onReady={vi.fn()} />);
        expect(await screen.findByRole('alert')).toHaveTextContent('Not enough disk space');
    });
});

describe("SetupScreen: the user's server isn't answering", () => {
    const RAW = "Lemonade Server at http://127.0.0.1:9/api/v1 is not reachable: HTTPConnectionPool(host='127.0.0.1', port=9)";
    const UNREACHABLE: SetupRunStatus = {
        state: 'failed',
        steps: steps('failed', 'pending'),
        error: "Your Lemonade Server at 127.0.0.1:9 isn't answering.",
        problem: {
            kind: 'server_unreachable',
            message: "Your Lemonade Server at 127.0.0.1:9 isn't answering.",
            start: 'Start Lemonade Server from the Lemonade tray icon.',
            use_gaia: true,
            switch_hint: null,
        },
        command: 'gaia init --profile gaia',
        log_tail: [`❌ ${RAW}`],
        models: [],
    };

    it('says so plainly, says how to start it, and keeps the exception in Details', async () => {
        mockedApi.startSetup.mockResolvedValue(UNREACHABLE);
        await startLocal();
        expect(await screen.findByRole('alert')).toHaveTextContent("Your Lemonade Server at 127.0.0.1:9 isn't answering.");
        expect(screen.getByText('Start Lemonade Server from the Lemonade tray icon.')).toBeInTheDocument();
        expect(screen.queryByText(/HTTPConnectionPool/)).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: /Details/ }));
        expect(screen.getByText(/HTTPConnectionPool/)).toBeInTheDocument();
    });

    it("switches to GAIA's own server and runs setup again", async () => {
        mockedApi.startSetup.mockResolvedValueOnce(UNREACHABLE).mockResolvedValueOnce(READY);
        await startLocal();
        fireEvent.click(await screen.findByRole('button', { name: /Use GAIA.s own instead/ }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenLastCalledWith(false, true, expect.any(Object)));
        expect(await screen.findByRole('button', { name: 'Start chatting' })).toBeInTheDocument();
    });

    it("doesn't offer a switch LEMONADE_BASE_URL would override, and says what does", async () => {
        const hint = "LEMONADE_BASE_URL points GAIA at this server. To use GAIA's own instead, unset it and restart GAIA.";
        mockedApi.startSetup.mockResolvedValue({ ...UNREACHABLE, problem: { ...UNREACHABLE.problem!, use_gaia: false, switch_hint: hint } });
        await startLocal();
        expect(await screen.findByText(hint)).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /Use GAIA.s own instead/ })).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
    });
});

describe('SetupScreen: an unreachable AMD gateway (#5604)', () => {
    const NO_NET = "Can't reach llm-api.amd.com. It needs AMD's network or VPN";
    const gateway = (reachable: boolean) => ({
        providers: [{
            id: 'amd' as const, name: 'AMD LLM Gateway', remote: true, privacy_notice: '',
            network: { reachable, detail: reachable ? null : NO_NET },
        }],
        active: { provider: 'local' as const, model: 'Gemma-4-E4B-it-GGUF', label: 'Gemma', remote: false, is_default: true },
        lemonade_error: null,
    });

    it("greys out the gateway choice and says it needs AMD's network", async () => {
        mockedApi.listProviders.mockResolvedValue(gateway(false));
        render(<SetupScreen onReady={vi.fn()} />);
        const amd = await screen.findByRole('radio', { name: /AMD LLM Gateway/ });
        await waitFor(() => expect(amd).toBeDisabled());
        expect(amd).toHaveTextContent("It needs AMD's network or VPN");
        expect(screen.getByRole('radio', { name: /Fireworks AI/ })).toBeEnabled();
    });

    it('refuses to continue on a saved gateway choice while there is no network, then re-enables it', async () => {
        localStorage.setItem('gaia-setup-choice', 'amd');
        mockedApi.listProviders.mockResolvedValue(gateway(false));
        render(<SetupScreen onReady={vi.fn()} />);
        await waitFor(() => expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled());
        expect(screen.getByText(/Choose another option, or connect and check again/)).toBeInTheDocument();
        mockedApi.listProviders.mockResolvedValue(gateway(true));
        fireEvent.click(screen.getByRole('button', { name: 'Check the AMD gateway again' }));
        await waitFor(() => expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled());
    });
});
