// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SetupScreen } from '../SetupScreen';
import { useModelStore } from '../../stores/modelStore';
import * as api from '../../services/api';
import type { LemonadeFound, LemonadeTarget, PreflightReport, SetupPlan, SetupRunStatus, SetupStep, SetupTest, StartScreen } from '../../types';
import {
    COLD_BIG, COMPONENTS, EMBED, FLM, GEMMA, GEMMA_CHAT, QWEN, QWEN_READS, WHISPER, gpuStatus, plan, progress, runStatus,
} from '../setup/__tests__/fixtures';
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

/** Render, and return the callback the screen reports ready through. */
function renderSetup(props: { onClose?: () => void } = {}) {
    const onReady = vi.fn();
    render(<SetupScreen onReady={onReady} {...props} />);
    return onReady;
}

const planScreen = () => screen.findByRole('region', { name: 'Recommended setup' });

/** Render and let the checks finish, which lands on the recommendation. */
async function open(props: { onClose?: () => void } = {}) {
    const onReady = renderSetup(props);
    await planScreen();
    return onReady;
}

const clickStart = () => fireEvent.click(screen.getByRole('button', { name: /^ ?(Download and start|Test and start)$/ }));
const clickContinue = () => fireEvent.click(screen.getByRole('button', { name: /^ ?Continue$/ }));

/** The recommendation's one button: the download starts. */
async function startLocal() {
    const onReady = await open();
    clickStart();
    return onReady;
}

async function toPc() {
    fireEvent.click(screen.getByRole('button', { name: 'Customize' }));
    await screen.findByRole('heading', { name: 'What we found on this PC' });
}

async function toModels() {
    await toPc();
    clickContinue();
    await screen.findByRole('heading', { name: 'Choose your models' });
}

async function toKey() {
    fireEvent.click(screen.getByRole('button', { name: 'Use a cloud model' }));
    await screen.findByRole('heading', { name: 'Connect a cloud provider' });
}

const rail = () => within(screen.getByRole('navigation', { name: 'Setup steps' }));
const railLabels = () => [...document.querySelectorAll('.setup-rail-step > span:last-child')].map((l) => l.textContent);
const railStep = (name: string) => rail().getByText(name).closest('.setup-rail-step');
const job = (name: string) => within(screen.getByRole('region', { name }));
const chatRadio = (name: RegExp) => job('Chat').getByRole('radio', { name });
const visionRadio = (name: RegExp) => job('Reading images').getByRole('radio', { name });
const startChatting = (timeout = 1000) => screen.findByRole('button', { name: 'Start chatting' }, { timeout });

describe('SetupScreen: checking this PC', () => {
    it('moves on to the recommendation by itself, with nothing to click', async () => {
        let answer!: (r: PreflightReport) => void;
        mockedApi.getOnboardingPreflight.mockImplementation(() => new Promise((r) => { answer = r; }));
        renderSetup();
        expect(await screen.findByRole('heading', { name: 'Checking this PC' })).toBeInTheDocument();
        await waitFor(() => expect(mockedApi.getSetupPlan).toHaveBeenCalled());
        expect(screen.queryAllByRole('button')).toEqual([]);
        expect(railStep('Check this PC')).toHaveClass('is-current');
        answer(HARDWARE);
        await planScreen();
        expect(railStep('Check this PC')).toHaveClass('is-done');
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
    });

    it('keeps going when the hardware could not be read, and says so on the PC screen', async () => {
        mockedApi.getOnboardingPreflight.mockRejectedValue(new Error('timed out'));
        await open();
        await toPc();
        expect(screen.getByText(/couldn.t be read/)).toHaveTextContent('timed out');
    });

    it('lists the device, memory and disk on the PC screen, with the backends', async () => {
        mockedApi.getOnboardingPreflight.mockResolvedValue(HARDWARE);
        mockedApi.getSetupPlan.mockResolvedValue({
            ...COLD_BIG(),
            backends: [
                { recipe: 'llamacpp', backend: 'vulkan', state: 'installed', default: true },
                { recipe: 'llamacpp', backend: 'cpu', state: 'installable', default: false },
                { recipe: 'flm', backend: 'npu', state: 'unsupported', default: false },
            ],
        });
        await open();
        await toPc();
        const list = screen.getByRole('list', { name: 'This PC' });
        expect(list).toHaveTextContent('AMD Radeon 8060S · Ryzen AI NPU');
        expect(list).toHaveTextContent('96 GB');
        expect(list).toHaveTextContent('400 GB free');
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        const backends = screen.getByRole('region', { name: 'Backends on this PC' });
        expect(within(backends).getByText('vulkan').closest('li')).toHaveTextContent('Installed');
        expect(within(backends).getByText('cpu').closest('li')).toHaveTextContent('Downloads when needed');
        expect(within(backends).getByText('npu').closest('li')).toHaveTextContent('Not supported here');
    });

    it('says the device is checked later when no model server runs yet, not that it is missing', async () => {
        mockedApi.getOnboardingPreflight.mockResolvedValue({ ...HARDWARE, gpu_name: null, npu_detected: null, lemonade_running: false });
        await open();
        await toPc();
        expect(screen.getByText('Checked once the model server is running')).toBeInTheDocument();
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    it("doesn't warn about the NPU before a model server can be asked", async () => {
        const npu = 'GAIA could not query Lemonade for NPU availability; the NPU result for the recommended model is unknown.';
        mockedApi.getOnboardingPreflight.mockResolvedValue({ ...HARDWARE, lemonade_running: false, warnings: [npu, 'Low memory.'] });
        await open();
        await toPc();
        expect(screen.getByText('Low memory.')).toBeInTheDocument();
        expect(screen.queryByText(npu)).not.toBeInTheDocument();
    });

    it('shows a real blocker as an error on the PC screen', async () => {
        mockedApi.getOnboardingPreflight.mockResolvedValue({ ...HARDWARE, compatible: false, blockers: ['Needs 30 GB of disk; 4 GB free.'] });
        await open();
        await toPc();
        expect(screen.getByRole('alert')).toHaveTextContent('Needs 30 GB of disk');
    });

    // Product bug: blockers and warnings only render behind Customize, so "Download and start" never shows them.
    it('shows a real blocker before the user can start a download', async () => {
        mockedApi.getOnboardingPreflight.mockResolvedValue({ ...HARDWARE, compatible: false, blockers: ['Needs 30 GB of disk; 4 GB free.'] });
        await open();
        expect(screen.getByText(/Needs 30 GB of disk/)).toBeInTheDocument();
    });

    it("says the model list couldn't be read and reads it again on Try again", async () => {
        mockedApi.getSetupPlan.mockRejectedValueOnce(new Error('Lemonade answered but could not list its models: 500'));
        renderSetup();
        expect(await screen.findByRole('alert')).toHaveTextContent('Lemonade answered but could not list its models: 500');
        mockedApi.getSetupPlan.mockResolvedValue(COLD_BIG());
        fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
        await planScreen();
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    // Product bug: the models screen holds this choice, but it can't open without a plan.
    it("offers setup's own set when the model list can't be read", async () => {
        mockedApi.getSetupPlan.mockRejectedValue(new Error('500'));
        renderSetup();
        await screen.findByRole('alert');
        expect(screen.getByRole('button', { name: 'Use the recommended set' })).toBeInTheDocument();
    });
});

describe('SetupScreen: a cold PC (no model server yet)', () => {
    const SERVER_RUNNING: SetupRunStatus = { state: 'running', server_only: true, text: 'Starting the local model server', steps: [] };
    const SERVER_READY: SetupRunStatus = { state: 'ready', server_only: true, steps: [] };

    it('starts only the model server by itself, then recommends from what it reads', async () => {
        mockedApi.getSetupPlan.mockResolvedValueOnce(COLD_SERVER).mockResolvedValue(COLD_BIG());
        mockedApi.startSetup.mockResolvedValueOnce(SERVER_RUNNING);
        mockedApi.getSetupStatus.mockResolvedValueOnce({ state: 'idle' }).mockResolvedValue(SERVER_READY);
        renderSetup();
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledExactlyOnceWith(false, false, { serverOnly: true }));
        expect(screen.getByRole('heading', { name: 'Checking this PC' })).toBeInTheDocument();
        expect(screen.queryByRole('region', { name: 'Recommended setup' })).not.toBeInTheDocument();
        await screen.findByRole('region', { name: 'Recommended setup' }, { timeout: 5000 });
        expect(screen.getByRole('button', { name: /Download and start/ })).toBeInTheDocument();
        expect(mockedApi.startSetup).toHaveBeenCalledTimes(1);
    });

    // Product bug: the auto-start reads "no run yet" before the status read answers, and starts a second one.
    it('waits for the status read before starting the server, so a reopen attaches to the run under way', async () => {
        let answer!: (s: SetupRunStatus) => void;
        mockedApi.getSetupPlan.mockResolvedValue(COLD_SERVER);
        mockedApi.getSetupStatus.mockImplementation(() => new Promise((r) => { answer = r; }));
        renderSetup();
        await waitFor(() => expect(mockedApi.getSetupPlan).toHaveBeenCalled());
        await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
        answer(SERVER_RUNNING);
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
    });

    it('reads the device again once the model server is up', async () => {
        mockedApi.getOnboardingPreflight.mockResolvedValue({ ...HARDWARE, gpu_name: null, npu_detected: null, lemonade_running: false });
        mockedApi.getSetupPlan.mockResolvedValueOnce(COLD_SERVER).mockResolvedValue(COLD_BIG());
        mockedApi.startSetup.mockResolvedValue(SERVER_READY);
        await open();
        expect(mockedApi.getOnboardingPreflight).toHaveBeenCalledTimes(2);
    });

    it('says why the server would not start, with Try again', async () => {
        mockedApi.getSetupPlan.mockResolvedValue(COLD_SERVER);
        mockedApi.startSetup.mockResolvedValueOnce({ state: 'failed', server_only: true, error: 'Lemonade did not start within 20 seconds.', command: 'gaia init --profile gaia', steps: [] })
            .mockResolvedValueOnce(SERVER_RUNNING);
        renderSetup();
        expect(await screen.findByRole('alert')).toHaveTextContent('Lemonade did not start within 20 seconds.');
        expect(railStep('Check this PC')).toHaveClass('is-failed');
        fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledTimes(2));
        expect(mockedApi.startSetup).toHaveBeenLastCalledWith(false, false, { serverOnly: true });
    });

    it("gives the user's server its own remedy, and can switch to GAIA's own", async () => {
        mockedApi.getSetupPlan.mockResolvedValue(COLD_SERVER);
        mockedApi.startSetup.mockResolvedValueOnce({
            state: 'failed', server_only: true, steps: [], error: "Your Lemonade Server at 127.0.0.1:9 isn't answering.",
            problem: { kind: 'server_unreachable', message: "Your Lemonade Server at 127.0.0.1:9 isn't answering.", start: 'Start Lemonade Server from the Lemonade tray icon.', use_gaia: true, switch_hint: null },
        }).mockResolvedValueOnce(SERVER_RUNNING);
        renderSetup();
        expect(await screen.findByText('Start Lemonade Server from the Lemonade tray icon.')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: /Use GAIA.s own instead/ }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenLastCalledWith(false, true, { serverOnly: true }));
    });
});

describe('SetupScreen: the recommendation', () => {
    it('shows what it will do before anything starts, with no stale error', async () => {
        mockedApi.getOnboardingPreflight.mockResolvedValue(HARDWARE);
        await open();
        const hero = within(await planScreen());
        expect(hero.getByText('Recommended for this PC')).toBeInTheDocument();
        expect(hero.getByText('Run Qwen3.6-35B-A3B on this PC')).toBeInTheDocument();
        expect(hero.getByText('29.6 GB')).toBeInTheDocument();
        expect(hero.getByText('AMD Radeon 8060S')).toBeInTheDocument();
        expect(hero.getByText('382.7 GB')).toBeInTheDocument();
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
        expect(mockedApi.downloadSetupModel).not.toHaveBeenCalled();
    });

    it('gives its reasons, with real numbers when the model fits', async () => {
        await open();
        const hero = await planScreen();
        expect(hero).toHaveTextContent('The model we evaluated as the default for a PC like this one.');
        expect(hero).toHaveTextContent('Fits this PC: needs 27.1 GB and this PC has 96.0 GB.');
        expect(hero).toHaveTextContent('Works offline, and your chats stay on this PC.');
    });

    it('calls a saved pick the user\'s choice, not a recommendation', async () => {
        mockedApi.getSetupPlan.mockResolvedValue(plan([QWEN, GEMMA, EMBED], { chat_model: { id: QWEN.id, user_set: true, size_gb: 23.3, downloaded: false } }));
        await open();
        const hero = await planScreen();
        expect(hero).toHaveTextContent('Your choice for this PC');
        expect(hero).not.toHaveTextContent('The model we evaluated');
    });

    it('names the verified model that would not fit, and why', async () => {
        const big = { ...QWEN, fits: false as const, need_gb: 27.1, capacity_gb: 16, planned: false, selected: false, recommended: false };
        const small = { ...GEMMA_CHAT, chat_choice: true };
        mockedApi.getSetupPlan.mockResolvedValue(plan([small, big, EMBED], { chat_model: { id: GEMMA.id, user_set: false, size_gb: 5.97, downloaded: false } }));
        await open();
        expect(await planScreen()).toHaveTextContent(`${QWEN.name}: Needs about 27.1 GB; this PC has 16.0 GB.`);
    });

    it('opens everything that will download, components and models', async () => {
        mockedApi.getSetupPlan.mockResolvedValue({ ...COLD_BIG(), components: COMPONENTS });
        await open();
        const list = screen.getByText('See everything that will be downloaded').closest('details') as HTMLElement;
        expect(within(list).getByText('Model runtime')).toBeInTheDocument();
        expect(within(list).getByText(QWEN.name)).toBeInTheDocument();
    });

    it('offers Test and start when there is nothing to download', async () => {
        const on = (e: typeof QWEN) => ({ ...e, downloaded: true, size_gb: 0 });
        mockedApi.getSetupPlan.mockResolvedValue(plan([on(QWEN), on(GEMMA), on(EMBED)]));
        await open();
        fireEvent.click(screen.getByRole('button', { name: /Test and start/ }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalled());
        expect(await startChatting()).toBeInTheDocument();
    });

    it('offers Not now only when setup was reopened from Settings', async () => {
        const onClose = vi.fn();
        await open({ onClose });
        fireEvent.click(screen.getByRole('button', { name: 'Not now' }));
        expect(onClose).toHaveBeenCalledTimes(1);
        cleanup();
        await open();
        expect(screen.queryByRole('button', { name: 'Not now' })).not.toBeInTheDocument();
    });
});

describe('SetupScreen: first run, local', () => {
    it('downloads the recommended set with keep-trying off, then says it is ready', async () => {
        await startLocal();
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledExactlyOnceWith(false, false, {
            acknowledgeLowDisk: false, keepTrying: false, extraModels: [],
        }));
        expect(mockedApi.chooseSetupChatModel).not.toHaveBeenCalled();
        expect(mockedApi.chooseSetupVisionModel).not.toHaveBeenCalled();
        expect(mockedApi.skipSetupModel).not.toHaveBeenCalled();
        expect(await startChatting()).toBeInTheDocument();
        expect(screen.getByRole('heading', { name: 'GAIA is ready' })).toBeInTheDocument();
    });

    it('shows the models before any byte downloads; nothing starts until Continue', async () => {
        await open();
        await toModels();
        expect(chatRadio(/Qwen3\.6-35B-A3B.*Recommended/)).toBeChecked();
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
        clickContinue();
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledTimes(1));
    });

    it('passes keep-trying through only when the user ticks it', async () => {
        await open();
        await toModels();
        fireEvent.click(screen.getByRole('checkbox', { name: /Keep trying if the connection drops/ }));
        clickContinue();
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledWith(false, false, expect.objectContaining({ keepTrying: true })));
    });

    it('saves a skipped model and an extra one before downloading', async () => {
        const p = plan([QWEN, GEMMA, EMBED, WHISPER]);
        mockedApi.getSetupPlan.mockResolvedValue(p);
        mockedApi.skipSetupModel.mockImplementation(async (id, skipped) => ({ ...p, catalog: p.catalog!.map((e) => (e.id === id ? { ...e, skipped, selected: !skipped } : e)) }));
        await open();
        await toModels();
        fireEvent.click(screen.getByRole('checkbox', { name: /embeddinggemma/ }));
        fireEvent.click(screen.getByRole('checkbox', { name: /Speech to text/ }));
        clickContinue();
        await waitFor(() => expect(mockedApi.skipSetupModel).toHaveBeenCalledExactlyOnceWith(EMBED.id, true));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledWith(false, false, expect.objectContaining({ extraModels: [WHISPER.id] })));
    });

    it('saves another chat model, and the vision model beside it, before downloading', async () => {
        const p = plan([QWEN_READS, { ...GEMMA_CHAT, planned: false, selected: false }, EMBED]);
        mockedApi.getSetupPlan.mockResolvedValue(p);
        mockedApi.chooseSetupVisionModel.mockResolvedValue({ ...p, vision_model: GEMMA.id });
        await open();
        await toModels();
        fireEvent.click(screen.getByRole('button', { name: 'Choose a different vision model' }));
        fireEvent.click(visionRadio(/Gemma-4-E4B-it/));
        clickContinue();
        await waitFor(() => expect(mockedApi.chooseSetupVisionModel).toHaveBeenCalledExactlyOnceWith(GEMMA.id));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledWith(false, false, expect.objectContaining({ extraModels: [GEMMA.id] })));
        expect(mockedApi.chooseSetupChatModel).not.toHaveBeenCalled();
    });

    it('goes Back through the Customize screens with the selection kept, by button and Alt+Left', async () => {
        await open();
        await toModels();
        fireEvent.click(screen.getByRole('checkbox', { name: /embeddinggemma/ }));
        fireEvent.click(screen.getByRole('button', { name: 'Back' }));
        expect(await screen.findByRole('heading', { name: 'What we found on this PC' })).toBeInTheDocument();
        clickContinue();
        expect(await screen.findByRole('checkbox', { name: /embeddinggemma/ })).not.toBeChecked();
        fireEvent.keyDown(document, { key: 'ArrowLeft', altKey: true });
        expect(screen.getByRole('heading', { name: 'What we found on this PC' })).toBeInTheDocument();
        fireEvent.keyDown(document, { key: 'ArrowLeft', altKey: true });
        expect(await planScreen()).toBeInTheDocument();
    });

    it('calls onReady from Start chatting', async () => {
        const onReady = await startLocal();
        fireEvent.click(await startChatting());
        await waitFor(() => expect(onReady).toHaveBeenCalledExactlyOnceWith(undefined));
    });

    it('names the model that answers, lists vision and documents, and offers a first task', async () => {
        const done = (e: typeof QWEN) => ({ ...e, downloaded: true, size_gb: 0 });
        mockedApi.getSetupPlan.mockResolvedValueOnce(COLD_BIG()).mockResolvedValue(plan([done(QWEN), done(GEMMA), done(EMBED)]));
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

describe('SetupScreen: the rail', () => {
    it('lists the main steps, with no Customize steps until asked', async () => {
        await open();
        expect(railLabels()).toEqual(['Check this PC', 'Your setup', 'Download', 'Test', 'Ready']);
        expect(railStep('Your setup')).toHaveAttribute('aria-current', 'step');
    });

    it('adds the Customize steps, with no GPU step on a discrete GPU', async () => {
        await open();
        await toModels();
        expect(railLabels()).toEqual(['Check this PC', 'Your setup', 'This PC', 'Models', 'Download', 'Test', 'Ready']);
    });

    it('reopens a step already passed, and not one ahead', async () => {
        await open();
        await toModels();
        expect(rail().queryByRole('button', { name: /Download/ })).not.toBeInTheDocument();
        fireEvent.click(rail().getByRole('button', { name: /This PC/ }));
        expect(await screen.findByRole('heading', { name: 'What we found on this PC' })).toBeInTheDocument();
    });

    it('marks the step that failed', async () => {
        mockedApi.startSetup.mockResolvedValue(FAILED);
        await startLocal();
        await screen.findByRole('alert');
        expect(railStep('Download')).toHaveClass('is-failed');
    });

    it('locks every step once the download has started', async () => {
        const run = runStatus([progress({ id: QWEN.id, state: 'downloading', bytes_done: 1e9, bytes_total: 23.3e9 })]);
        mockedApi.startSetup.mockResolvedValue(run);
        mockedApi.getSetupStatus.mockResolvedValueOnce({ state: 'idle' }).mockResolvedValue(run);
        await startLocal();
        await screen.findByRole('button', { name: 'Cancel' });
        expect(rail().queryAllByRole('button')).toEqual([]);
    });
});

describe('SetupScreen: warnings and confirmations', () => {
    const BIG = { ...QWEN, fits: false as const, need_gb: 27.1, capacity_gb: 16 };

    it('asks before downloading a model too big for memory, with the safe choice focused', async () => {
        mockedApi.getSetupPlan.mockResolvedValue(plan([BIG, GEMMA, EMBED]));
        await open();
        clickStart();
        const sheet = await screen.findByRole('alertdialog', { name: 'Qwen3.6-35B-A3B may not fit this PC. Download it anyway?' });
        expect(sheet).toHaveTextContent('Needs about 27.1 GB; this PC has 16.0 GB.');
        expect(within(sheet).getByRole('button', { name: 'Pick a smaller one' })).toHaveFocus();
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
        fireEvent.click(within(sheet).getByRole('button', { name: 'Download anyway' }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalled());
    });

    it('stays put on Pick a smaller one, with nothing downloading', async () => {
        mockedApi.getSetupPlan.mockResolvedValue(plan([BIG, GEMMA, EMBED]));
        await open();
        clickStart();
        fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Pick a smaller one' }));
        expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
        expect(await planScreen()).toBeInTheDocument();
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
    });

    it('asks the same from the models screen', async () => {
        mockedApi.getSetupPlan.mockResolvedValue(plan([BIG, GEMMA, EMBED]));
        await open();
        await toModels();
        clickContinue();
        expect(await screen.findByRole('alertdialog')).toBeInTheDocument();
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
    });

    it('confirms the server’s own no-fit answer before choosing that chat model', async () => {
        const p = COLD_BIG();
        const chosen: SetupPlan = {
            ...p,
            chat_model: { id: GEMMA.id, user_set: true, size_gb: 5.97, downloaded: false },
            catalog: p.catalog!.map((e) => (e.id === QWEN.id ? { ...e, planned: false, selected: false } : e)),
        };
        mockedApi.chooseSetupChatModel
            .mockRejectedValueOnce(new api.ApiError('x', 409, 'x', null, [], {
                code: 'no_fit', message: 'Gemma-4-E4B-it-GGUF may not run on this PC: too little free memory right now.', acknowledge: 'acknowledge_no_fit',
            }))
            .mockResolvedValueOnce(chosen);
        await open();
        await toModels();
        fireEvent.click(chatRadio(/Gemma-4-E4B-it/));
        clickContinue();
        const sheet = await screen.findByRole('alertdialog', { name: 'This model may not run on this PC' });
        expect(sheet).toHaveTextContent('too little free memory right now');
        fireEvent.click(within(sheet).getByRole('button', { name: 'Use it anyway' }));
        await waitFor(() => expect(mockedApi.chooseSetupChatModel).toHaveBeenLastCalledWith(GEMMA.id, true));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalled());
    });

    const SHORT = () => plan([QWEN, GEMMA, EMBED], { disk: { path: 'D:\\hub', free_gb: 11.2, source: 'lemonade' } });

    it('says the disk is short on the recommendation', async () => {
        mockedApi.getSetupPlan.mockResolvedValue(SHORT());
        await open();
        expect(screen.getByRole('alert')).toHaveTextContent('Needs 29.6 GB; only 11.2 GB free on the model drive. Free 18.4 GB, or untick more models.');
    });

    // Product bug: the button sends acknowledge_low_disk by itself, so the server's own check never asks.
    it('asks before downloading past a disk shortfall from the recommendation', async () => {
        mockedApi.getSetupPlan.mockResolvedValue(SHORT());
        await open();
        clickStart();
        expect(await screen.findByRole('alertdialog')).toBeInTheDocument();
    });

    it('downloads past a disk shortfall from the models screen only after Continue anyway', async () => {
        mockedApi.getSetupPlan.mockResolvedValue(SHORT());
        await open();
        await toModels();
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: 'Continue anyway' }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledWith(false, false, expect.objectContaining({ acknowledgeLowDisk: true })));
    });

    const LOW_DISK = () => new api.ApiError('x', 409, 'x', null, [], {
        code: 'low_disk', message: 'Needs 29.6 GB; only 20.1 GB free on C:\\hub. Free 9.5 GB.', acknowledge: 'acknowledge_low_disk',
        need_gb: 29.6, free_gb: 20.1, free_up_gb: 9.5, path: 'C:\\hub', drop: [],
    });

    it("confirms the server's low-disk answer, then downloads with it acknowledged", async () => {
        mockedApi.startSetup.mockRejectedValueOnce(LOW_DISK()).mockResolvedValueOnce(READY);
        await startLocal();
        const sheet = await screen.findByRole('alertdialog', { name: 'Not enough free disk space' });
        expect(sheet).toHaveTextContent('Free 9.5 GB.');
        fireEvent.click(within(sheet).getByRole('button', { name: 'Download anyway' }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenLastCalledWith(false, false, expect.objectContaining({ acknowledgeLowDisk: true })));
    });

    it("opens the choices on Change models instead of the server's low-disk answer", async () => {
        mockedApi.startSetup.mockRejectedValueOnce(LOW_DISK());
        await startLocal();
        const sheet = await screen.findByRole('alertdialog', { name: 'Not enough free disk space' });
        fireEvent.click(within(sheet).getByRole('button', { name: 'Change models' }));
        expect(await screen.findByRole('heading', { name: 'What we found on this PC' })).toBeInTheDocument();
        expect(mockedApi.startSetup).toHaveBeenCalledTimes(1);
    });

    it('lets an FLM-only user finish with their NPU model and nothing to download', async () => {
        const p = plan([QWEN, GEMMA, { ...EMBED, downloaded: true, size_gb: 0 }, FLM]);
        mockedApi.getSetupPlan.mockResolvedValue(p);
        const afterChat = { ...p, chat_model: { id: FLM.id, user_set: true, size_gb: 0, downloaded: true }, catalog: p.catalog!.map((e) => (e.id === QWEN.id ? { ...e, planned: false, selected: false } : e.id === FLM.id ? { ...e, planned: true, selected: true } : e)) };
        mockedApi.chooseSetupChatModel.mockResolvedValue(afterChat);
        mockedApi.skipSetupModel.mockImplementation(async (id, skipped) => ({ ...afterChat, catalog: afterChat.catalog.map((e) => (e.id === id ? { ...e, skipped, selected: !skipped } : e)) }));
        await open();
        await toModels();
        fireEvent.click(chatRadio(/Gemma 4 E2B \(NPU\)/));
        fireEvent.click(visionRadio(/Skip for now/));
        clickContinue();
        await waitFor(() => expect(mockedApi.chooseSetupChatModel).toHaveBeenCalledWith(FLM.id, false));
        await waitFor(() => expect(mockedApi.skipSetupModel).toHaveBeenCalledWith(GEMMA.id, true));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledWith(false, false, expect.objectContaining({ extraModels: [] })));
    });

    it('says everything is already here on the models screen, and finishes from there', async () => {
        const on = (e: typeof QWEN) => ({ ...e, downloaded: true, size_gb: 0 });
        mockedApi.getSetupPlan.mockResolvedValue(plan([on(QWEN), on(GEMMA), on(EMBED)]));
        await open();
        await toPc();
        clickContinue();
        expect(await screen.findByText('Everything is already on this PC.')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: /Finish setup/ }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalled());
        expect(await startChatting()).toBeInTheDocument();
    });
});

describe('SetupScreen: the GPU memory step', () => {
    it('appears on an integrated GPU, and Skip moves on to the models with nothing downloading', async () => {
        mockedApi.getGpuMemory.mockResolvedValue(gpuStatus());
        await open();
        await toPc();
        expect(railLabels()).toEqual(['Check this PC', 'Your setup', 'This PC', 'GPU memory (optional)', 'Models', 'Download', 'Test', 'Ready']);
        clickContinue();
        expect(await screen.findByText('With 64.0 GB for the GPU, Qwen3.6-35B-A3B fits.')).toBeInTheDocument();
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: 'Skip for now' }));
        expect(await screen.findByRole('heading', { name: 'Choose your models' })).toBeInTheDocument();
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
    });

    it('stays out of the plain path, which goes straight to the download', async () => {
        mockedApi.getGpuMemory.mockResolvedValue(gpuStatus());
        await startLocal();
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalled());
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

    it('lists every component coming to this PC, with its state from the run', async () => {
        const run: SetupRunStatus = { ...running, components: { server: 'done', backend: 'active' } };
        mockedApi.getSetupPlan.mockResolvedValue({ ...COLD_BIG(), components: COMPONENTS });
        mockedApi.startSetup.mockResolvedValue(run);
        mockedApi.getSetupStatus.mockResolvedValueOnce({ state: 'idle' }).mockResolvedValue(run);
        await startLocal();
        const list = within(await screen.findByRole('region', { name: 'Everything being set up' }));
        expect(list.getByText('Model runtime').closest('li')).toHaveTextContent('From github.com/ggml-org/llama.cpp');
        expect(list.getByText('Model runtime').closest('li')).toHaveTextContent('In progress');
        expect(list.getByText("GAIA's model server").closest('li')).toHaveTextContent('Ready');
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
                code: 'low_disk', message: 'Needs 0.7 GB; only 0.1 GB free on C:\\hub. Free 0.6 GB.', acknowledge: 'acknowledge_low_disk',
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
        renderSetup();
        expect(await screen.findByRole('progressbar', { name: 'Qwen3.6-35B-A3B, Downloading' })).toBeInTheDocument();
        expect(screen.queryByRole('region', { name: 'Recommended setup' })).not.toBeInTheDocument();
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
    });

    const HALF = () => plan([{ ...QWEN, partial: true, partial_gb: 7.6, size_gb: 15.7 }, GEMMA, EMBED]);

    it("offers to resume last time's download on reopen, and never resumes on its own", async () => {
        mockedApi.getSetupPlan.mockResolvedValue(HALF());
        renderSetup();
        const banner = await screen.findByText('Last time’s download didn’t finish. Resume?');
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
        await planScreen();
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
        fireEvent.click(within(banner.closest('.setup-banner') as HTMLElement).getByRole('button', { name: 'Resume' }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledTimes(1));
    });

    it('opens the choices from the resume offer on Change models, and drops the offer', async () => {
        mockedApi.getSetupPlan.mockResolvedValue(HALF());
        await open();
        fireEvent.click(screen.getByRole('button', { name: 'Change models' }));
        expect(await screen.findByRole('heading', { name: 'What we found on this PC' })).toBeInTheDocument();
        expect(screen.queryByText('Last time’s download didn’t finish. Resume?')).not.toBeInTheDocument();
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
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

    it("shows a failed run's error with Try again when no model row explains it, and no CLI command", async () => {
        mockedApi.startSetup.mockResolvedValueOnce(FAILED).mockResolvedValueOnce(READY);
        await startLocal();
        expect(await screen.findByRole('alert')).toHaveTextContent('Not enough disk space');
        expect(screen.queryByText(/gaia init/)).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledTimes(2));
        expect(await startChatting()).toBeInTheDocument();
    });

    it('shows why setup could not start', async () => {
        mockedApi.startSetup.mockRejectedValue(new Error('Setup is already running in another window'));
        await startLocal();
        expect(await screen.findByRole('alert')).toHaveTextContent('already running in another window');
    });

    it('shows an earlier failed run with its error', async () => {
        mockedApi.getSetupStatus.mockResolvedValue(FAILED);
        renderSetup();
        expect(await screen.findByRole('alert')).toHaveTextContent('Not enough disk space');
    });
});

describe('SetupScreen: optional extras added during the download', () => {
    const first = runStatus([progress({ id: QWEN.id, state: 'downloading', bytes_done: 1e9, bytes_total: 23.3e9 })]);
    const second = runStatus([progress({ id: WHISPER.id, roles: ['speech'], state: 'downloading', bytes_done: 0.1e9, bytes_total: 1.6e9 })]);
    let status: SetupRunStatus;

    beforeEach(() => {
        status = { state: 'idle' };
        mockedApi.getSetupStatus.mockImplementation(async () => status);
        mockedApi.getSetupPlan.mockResolvedValue(plan([QWEN, GEMMA, EMBED, WHISPER]));
        mockedApi.startSetup.mockResolvedValueOnce(first).mockResolvedValueOnce(second);
    });

    async function downloading() {
        await open();
        status = first;
        clickStart();
        return within(await screen.findByRole('region', { name: 'Optional' }));
    }

    it('fetches a queued extra in a follow-up run, and is not ready until that ends', async () => {
        const optional = await downloading();
        expect(optional.getByText('Turns speech and recordings into text').closest('li')).toHaveTextContent('1.6 GB');
        fireEvent.click(optional.getByRole('button', { name: 'Add' }));
        expect(optional.getByRole('button', { name: 'Added' })).toHaveAttribute('aria-pressed', 'true');
        expect(mockedApi.startSetup).toHaveBeenCalledTimes(1);

        status = READY;
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledTimes(2), { timeout: 4000 });
        expect(mockedApi.startSetup).toHaveBeenLastCalledWith(false, false, { keepTrying: false, extraModels: [WHISPER.id] });
        expect(screen.queryByRole('button', { name: 'Start chatting' })).not.toBeInTheDocument();

        status = READY;
        expect(await startChatting(4000)).toBeInTheDocument();
    });

    it('runs nothing more for an extra taken back before the run ended', async () => {
        const optional = await downloading();
        fireEvent.click(optional.getByRole('button', { name: 'Add' }));
        fireEvent.click(optional.getByRole('button', { name: 'Added' }));
        status = READY;
        expect(await startChatting(4000)).toBeInTheDocument();
        expect(mockedApi.startSetup).toHaveBeenCalledTimes(1);
    });
});

describe('SetupScreen: testing', () => {
    const row = (job: SetupTest['job'], state: SetupTest['state'], over: Partial<SetupTest> = {}): SetupTest => ({
        job, subject: null, state, error: null, kind: null, seconds: null, ...over,
    });
    const models = [progress({ id: QWEN.id, state: 'verifying', bytes_done: 23.3e9, bytes_total: 23.3e9 })];
    const verifying = runStatus(models, {
        state: 'verifying',
        tests: [row('server', 'passed'), row('chat', 'running', { subject: QWEN.id })],
    });
    const failedRun = (kind: SetupTest['kind']) => runStatus(models, {
        state: 'failed',
        error: 'chat test failed',
        tests: [row('server', 'passed'), row('chat', 'failed', { subject: QWEN.id, kind, error: 'would not answer' })],
    });

    it('moves to the Test step once tests start, with the tests still to come listed', async () => {
        mockedApi.startSetup.mockResolvedValue(verifying);
        mockedApi.getSetupStatus.mockResolvedValueOnce({ state: 'idle' }).mockResolvedValue(verifying);
        await startLocal();
        expect(await screen.findByRole('heading', { name: 'Testing everything' })).toBeInTheDocument();
        expect(screen.getByText('Chat: loads and answers').closest('li')).toHaveTextContent('Running');
        expect(screen.getByText('Reading images: names a test image\'s colour').closest('li')).toHaveTextContent('Waiting');
        expect(screen.getByText('A real chat with GAIA').closest('li')).toHaveTextContent('Waiting');
        expect(railStep('Test')).toHaveClass('is-active');
        expect(railStep('Download')).toHaveClass('is-done');
    });

    it('is not ready until the tests pass', async () => {
        mockedApi.startSetup.mockResolvedValue(failedRun('stuck'));
        await startLocal();
        await screen.findByRole('heading', { name: 'Testing everything' });
        expect(screen.queryByRole('button', { name: 'Start chatting' })).not.toBeInTheDocument();
        expect(railStep('Test')).toHaveClass('is-failed');
    });

    it('tests again with the same choices', async () => {
        mockedApi.startSetup.mockResolvedValueOnce(failedRun('stuck')).mockResolvedValueOnce(READY);
        await startLocal();
        fireEvent.click(await screen.findByRole('button', { name: 'Test again' }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledTimes(2));
        expect(mockedApi.startSetup.mock.calls[1]).toEqual(mockedApi.startSetup.mock.calls[0]);
        expect(await startChatting()).toBeInTheDocument();
    });

    it('sends a memory failure back to the choices on Choose another model', async () => {
        mockedApi.startSetup.mockResolvedValue(failedRun('memory'));
        await startLocal();
        fireEvent.click(await screen.findByRole('button', { name: 'Choose another model' }));
        expect(await screen.findByRole('heading', { name: 'What we found on this PC' })).toBeInTheDocument();
    });

    it('deletes a broken model and downloads it again', async () => {
        mockedApi.startSetup.mockResolvedValue(failedRun('broken'));
        mockedApi.removeSetupModel.mockResolvedValue({ ...COLD_BIG(), freed_gb: 23.3 });
        mockedApi.downloadSetupModel.mockResolvedValue(failedRun('broken'));
        await startLocal();
        fireEvent.click(await screen.findByRole('button', { name: `Download ${QWEN.name} again` }));
        expect(mockedApi.removeSetupModel).not.toHaveBeenCalled();
        fireEvent.click(await screen.findByRole('button', { name: 'Delete and download again' }));
        await waitFor(() => expect(mockedApi.removeSetupModel).toHaveBeenCalledWith(QWEN.id));
        await waitFor(() => expect(mockedApi.downloadSetupModel).toHaveBeenCalledWith(QWEN.id, { keepTrying: false }));
    });

    it('reaches ready when only an optional extra failed', async () => {
        mockedApi.startSetup.mockResolvedValue({
            ...READY, tests: [row('chat', 'passed', { subject: QWEN.id }), row('speech', 'failed', { subject: WHISPER.id, kind: 'broken', error: 'no audio' })],
        });
        await startLocal();
        expect(await startChatting()).toBeInTheDocument();
    });

    // Product bug: the run goes straight to Ready, so the failed row is never on screen.
    it('still says an optional extra failed once setup is ready', async () => {
        mockedApi.startSetup.mockResolvedValue({
            ...READY, tests: [row('chat', 'passed', { subject: QWEN.id }), row('speech', 'failed', { subject: WHISPER.id, kind: 'broken', error: 'no audio' })],
        });
        await startLocal();
        await startChatting();
        expect(screen.getByText(/Speech to text/)).toBeInTheDocument();
    });
});

describe('SetupScreen: cloud', () => {
    function connects(provider: 'fireworks' | 'amd') {
        mockedApi.connectProviderWaitingForOs.mockResolvedValue({} as Awaited<ReturnType<typeof api.connectProviderWaitingForOs>>);
        mockedApi.listProviderModels.mockResolvedValue({
            provider,
            models: [
                { id: `${provider}.m2`, context_length: null, downloaded: true, labels: [], rank: 2, note: null, evidence: null },
                { id: `${provider}.m1`, context_length: null, downloaded: true, labels: [], rank: 1, note: null, evidence: null },
            ],
        });
        mockedApi.selectModel.mockResolvedValue({ provider, model: `${provider}.m1`, label: 'm1', remote: true, is_default: false });
        mockedApi.listSessions.mockResolvedValue({ sessions: [], total: 0 });
    }

    it('shows how to get a Fireworks key, with the key hidden as it is typed', async () => {
        await open();
        await toKey();
        expect(screen.getByRole('radio', { name: /Fireworks AI/ })).toHaveAttribute('aria-checked', 'true');
        expect(screen.getByRole('link', { name: /Open Fireworks API keys/ })).toHaveAttribute('href', 'https://app.fireworks.ai/settings/users/api-keys');
        expect(screen.getByText('Create a new API key and copy it.')).toBeInTheDocument();
        expect(screen.getByLabelText('API key')).toHaveAttribute('type', 'password');
    });

    it('swaps the steps for a guide link on the AMD gateway', async () => {
        await open();
        await toKey();
        fireEvent.click(screen.getByRole('radio', { name: /AMD LLM Gateway/ }));
        expect(screen.getByRole('link', { name: 'How to get an AMD LLM Gateway key' })).toHaveAttribute('href', 'https://amd-gaia.ai/docs/guides/llm-gateway');
        expect(screen.queryByText('Get a key in about a minute')).not.toBeInTheDocument();
    });

    it('connects the key, then downloads only the embedding model and finishes', async () => {
        connects('fireworks');
        await open();
        await toKey();
        expect(railLabels()).toEqual(['Check this PC', 'Your setup', 'Cloud key', 'Download', 'Test', 'Ready']);
        fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'fw-key' } });
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: /Test and connect/ }));
        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('fireworks.m1'));
        expect(mockedApi.connectProviderWaitingForOs).toHaveBeenCalledWith('fireworks', { api_key: 'fw-key' }, expect.any(Function), expect.any(Function));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledWith(true, false, expect.objectContaining({ extraModels: [] })));
        expect(mockedApi.chooseSetupChatModel).not.toHaveBeenCalled();
        expect(mockedApi.skipSetupModel).not.toHaveBeenCalled();
        expect(await startChatting()).toBeInTheDocument();
        expect(screen.getByText(/answers in the cloud\./)).toBeInTheDocument();
        expect(localStorage.getItem('gaia-setup-choice')).toBe('fireworks');
    });

    it('shows why the key failed and downloads nothing', async () => {
        mockedApi.connectProviderWaitingForOs.mockRejectedValue(new Error('Fireworks rejected this key.'));
        await open();
        await toKey();
        fireEvent.click(screen.getByRole('button', { name: /Test and connect/ }));
        expect(await screen.findByRole('alert')).toHaveTextContent('Fireworks rejected this key.');
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
    });

    it('fails loudly when the provider lists no chat models', async () => {
        connects('fireworks');
        mockedApi.listProviderModels.mockResolvedValue({ provider: 'fireworks', models: [] });
        await open();
        await toKey();
        fireEvent.click(screen.getByRole('button', { name: /Test and connect/ }));
        expect(await screen.findByRole('alert')).toHaveTextContent('Connected, but the provider listed no chat models.');
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
    });

    it('goes back to the recommendation, local again', async () => {
        await open();
        await toKey();
        fireEvent.click(screen.getByRole('button', { name: 'Back' }));
        await planScreen();
        expect(railLabels()).not.toContain('Cloud key');
    });

    it('says when the OS is asking to release the saved key, instead of a silent wait', async () => {
        const asking = 'macOS is asking GAIA for keychain access to read your saved Fireworks AI key. Look for a system dialog and click Always Allow; this updates once you answer.';
        mockedApi.connectProviderWaitingForOs.mockImplementation((_p, _b, onWaiting) => {
            onWaiting(asking);
            return new Promise(() => {});
        });
        await open();
        await toKey();
        fireEvent.click(screen.getByRole('button', { name: /Test and connect/ }));
        expect(await screen.findByText(/Look for a system dialog/)).toHaveAttribute('role', 'status');
        expect(screen.getByRole('button', { name: /Test and connect/ })).toBeDisabled();
    });

    it("on the user's own Lemonade, connects with no key and says their server is left alone", async () => {
        mockedApi.getLemonadeTarget.mockResolvedValue({
            ...TARGET, mode: 'external', saved_mode: 'external', source: 'config',
            base_url: FOUND.url, saved_base_url: FOUND.url, offer: false,
        });
        connects('amd');
        await open();
        await toKey();
        fireEvent.click(screen.getByRole('radio', { name: /AMD LLM Gateway/ }));
        expect(await screen.findByText(/doesn.t send your server a key or change its setup/)).toBeInTheDocument();
        expect(screen.queryByLabelText('API key')).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: /Test and connect/ }));
        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('amd.m1'));
        expect(mockedApi.connectProviderWaitingForOs).toHaveBeenCalledWith('amd', { api_key: undefined }, expect.any(Function), expect.any(Function));
    });

    it('keeps the gateway choice after a reload and lets the user switch provider', async () => {
        localStorage.setItem('gaia-setup-choice', 'amd');
        await open();
        await toKey();
        expect(screen.getByRole('radio', { name: /AMD LLM Gateway/ })).toHaveAttribute('aria-checked', 'true');
        fireEvent.click(screen.getByRole('radio', { name: /Fireworks AI/ }));
        expect(screen.getByRole('radio', { name: /Fireworks AI/ })).toHaveAttribute('aria-checked', 'true');
        expect(localStorage.getItem('gaia-setup-choice')).toBe('fireworks');
    });

    // Product bug: the connected key lives in component state, so a reload mid-run strands the user on Download.
    it('finishes a cloud run reopened after a reload', async () => {
        localStorage.setItem('gaia-setup-choice', 'fireworks');
        mockedApi.getSetupStatus.mockResolvedValue({ ...READY, skip_chat_model: true });
        renderSetup();
        expect(await screen.findByLabelText('API key').catch(() => startChatting())).toBeInTheDocument();
    });

    // Product bug: a saved cloud choice survives to the plan screen, whose button then starts a cloud run with no key.
    it('runs the local plan from the recommendation, whatever cloud choice was saved', async () => {
        localStorage.setItem('gaia-setup-choice', 'fireworks');
        await startLocal();
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalled());
        expect(mockedApi.startSetup.mock.calls[0][0]).toBe(false);
    });
});

describe('SetupScreen: a Lemonade the user already runs', () => {
    async function offer(found: LemonadeFound | null) {
        mockedApi.getLemonadeTarget.mockResolvedValue(TARGET);
        mockedApi.detectLemonade.mockResolvedValue({ found });
        mockedApi.setLemonadeTarget.mockResolvedValue({ ...TARGET, daemon: null });
        await open();
        await toPc();
    }

    it('offers it with what GAIA does there, and saves the URL on Use mine', async () => {
        await offer(FOUND);
        expect(await screen.findByRole('form', { name: 'Use your Lemonade Server' }))
            .toHaveTextContent('Use the Lemonade you already run at http://localhost:13305/api/v1 (v10.3.0)');
        expect(screen.getByText(TARGET.what_gaia_does)).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Use mine' }));
        await waitFor(() => expect(mockedApi.setLemonadeTarget).toHaveBeenCalledExactlyOnceWith(
            { mode: 'external', base_url: FOUND.url, api_key: undefined },
        ));
        expect(await screen.findByText(/GAIA will use your Lemonade Server at http:\/\/localhost:13305\/api\/v1/)).toBeInTheDocument();
    });

    it('reads the device and the models again from their server', async () => {
        await offer(FOUND);
        const plans = mockedApi.getSetupPlan.mock.calls.length;
        fireEvent.click(await screen.findByRole('button', { name: 'Use mine' }));
        await screen.findByText(/GAIA will use your Lemonade Server/);
        await waitFor(() => expect(mockedApi.getSetupPlan.mock.calls.length).toBeGreaterThan(plans));
        expect(mockedApi.getOnboardingPreflight).toHaveBeenCalledTimes(2);
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
    });

    // Product bug: the check starts GAIA's server by itself, and the offer waits behind Customize.
    it("offers it on a cold PC before installing GAIA's own server", async () => {
        mockedApi.getLemonadeTarget.mockResolvedValue(TARGET);
        mockedApi.detectLemonade.mockResolvedValue({ found: FOUND });
        mockedApi.getSetupPlan.mockResolvedValue(COLD_SERVER);
        mockedApi.startSetup.mockResolvedValue({ state: 'running', server_only: true, steps: [] });
        renderSetup();
        await waitFor(() => expect(mockedApi.getSetupPlan).toHaveBeenCalled());
        await new Promise((r) => setTimeout(r, 50));
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
    });

    it('keeps the newest plan when an older request answers last', async () => {
        let answerFirst!: (p: SetupPlan) => void;
        mockedApi.getSetupPlan
            .mockImplementationOnce(() => new Promise((r) => { answerFirst = r; }))
            .mockResolvedValue(COLD_BIG());
        mockedApi.getSetupStatus.mockResolvedValue({ state: 'ready', server_only: true, steps: [] });
        await open();
        await act(async () => { answerFirst(COLD_SERVER); });
        expect(screen.getByRole('region', { name: 'Recommended setup' })).toBeInTheDocument();
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
    });

    it("saves GAIA's own when declined", async () => {
        await offer(FOUND);
        fireEvent.click(await screen.findByRole('button', { name: /Use GAIA.s own/ }));
        await waitFor(() => expect(mockedApi.setLemonadeTarget).toHaveBeenCalledExactlyOnceWith({ mode: 'gaia' }));
        expect(await screen.findByText('GAIA will set up its own Lemonade Server.')).toBeInTheDocument();
    });

    it('asks for the key a protected server needs, and never shows it back', async () => {
        await offer({ ...FOUND, status: 'needs_key' });
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
        await offer(FOUND);
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
        await open();
        await toPc();
        expect(await screen.findByText(/Couldn.t check whether your Lemonade Server at http:\/\/localhost:13305\/api\/v1 is running/)).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: /Check again/ }));
        expect(await screen.findByRole('button', { name: 'Use mine' })).toBeInTheDocument();
    });

    it('explains a too-old server and offers no way to use it', async () => {
        const message = 'The Lemonade Server at http://localhost:8000/api/v1 is v9.0.0; GAIA needs v10.0.0 or newer.';
        await offer({ ...FOUND, status: 'too_old', message });
        expect(await screen.findByText(message)).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Use mine' })).not.toBeInTheDocument();
    });

    it('does not look for one once a choice is saved', async () => {
        await open();
        await toPc();
        await waitFor(() => expect(mockedApi.getLemonadeTarget).toHaveBeenCalled());
        expect(mockedApi.detectLemonade).not.toHaveBeenCalled();
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
        expect(await startChatting()).toBeInTheDocument();
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
    const gateway = (reachable: boolean) => ({
        providers: [{
            id: 'amd' as const, name: 'AMD LLM Gateway', remote: true, privacy_notice: '',
            network: { reachable, detail: reachable ? null : "Can't reach llm-api.amd.com. It needs AMD's network or VPN" },
        }],
        active: { provider: 'local' as const, model: 'Gemma-4-E4B-it-GGUF', label: 'Gemma', remote: false, is_default: true },
        lemonade_error: null,
    });
    const amd = () => screen.getByRole('radio', { name: /AMD LLM Gateway/ });

    it("greys out the gateway choice and says it needs AMD's network", async () => {
        mockedApi.listProviders.mockResolvedValue(gateway(false));
        await open();
        await toKey();
        await waitFor(() => expect(amd()).toBeDisabled());
        expect(amd()).toHaveTextContent("Unavailable: needs AMD's network or VPN.");
        expect(screen.getByRole('radio', { name: /Fireworks AI/ })).toBeEnabled();
    });

    it('enables the gateway after Check again, once it answers', async () => {
        mockedApi.listProviders.mockResolvedValue(gateway(false));
        await open();
        await toKey();
        await waitFor(() => expect(amd()).toBeDisabled());
        mockedApi.listProviders.mockResolvedValue(gateway(true));
        fireEvent.click(screen.getByRole('button', { name: 'Check the AMD gateway again' }));
        await waitFor(() => expect(amd()).toBeEnabled());
        expect(amd()).not.toHaveTextContent('Unavailable');
        expect(screen.queryByRole('button', { name: 'Check the AMD gateway again' })).not.toBeInTheDocument();
    });

    it('refuses to connect on a saved gateway choice while there is no network, then allows it', async () => {
        localStorage.setItem('gaia-setup-choice', 'amd');
        mockedApi.listProviders.mockResolvedValue(gateway(false));
        await open();
        await toKey();
        const connect = screen.getByRole('button', { name: /Test and connect/ });
        await waitFor(() => expect(connect).toBeDisabled());
        mockedApi.listProviders.mockResolvedValue(gateway(true));
        fireEvent.click(screen.getByRole('button', { name: 'Check the AMD gateway again' }));
        await waitFor(() => expect(connect).toBeEnabled());
    });

    it('keeps the gateway unavailable when Check again still finds no network', async () => {
        mockedApi.listProviders.mockResolvedValue(gateway(false));
        await open();
        await toKey();
        await waitFor(() => expect(amd()).toBeDisabled());
        fireEvent.click(screen.getByRole('button', { name: 'Check the AMD gateway again' }));
        await waitFor(() => expect(mockedApi.listProviders).toHaveBeenCalledTimes(2));
        expect(amd()).toBeDisabled();
    });
});
