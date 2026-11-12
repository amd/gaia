// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useChatStore } from '../../stores/chatStore';
import { SetupScreen } from '../SetupScreen';
import { useModelStore } from '../../stores/modelStore';
import * as api from '../../services/api';
import type { LemonadeFound, LemonadeTarget, PreflightReport, SetupPlan, SetupRunStatus, SetupStep, SetupTest, StartScreen } from '../../types';
import {
    COLD_BIG, COMPONENTS, EMBED, FLM, GEMMA, GEMMA_CHAT, QWEN, QWEN_READS, WHISPER, gpuStatus, plan, progress, runStatus,
} from '../setup/__tests__/fixtures';
import { NO_BACKENDS, NO_GPU_SPLIT } from '../../test/setupApiMocks';

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
    cards: [
        { skill: 'research-report', title: 'Research a topic', note: 'A cited report from the web', prompt: 'Research: ', needs_input: true },
        { skill: 'check-in', title: 'Check in', note: 'Pick up where you left off', prompt: 'Good morning.', needs_input: false },
    ],
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
        month: '2026-10', total_usd: 0, turns: 0, unpriced_turns: 0, unreported_turns: 0, limit_usd: null, state: 'no_limit', by_model: [], price_source: 'list price',
    });
    mockedApi.startSetup.mockResolvedValue(READY);
    mockedApi.getLemonadeTarget.mockResolvedValue({ ...TARGET, offer: false });
    mockedApi.getSetupPlan.mockResolvedValue(COLD_BIG());
    mockedApi.getGpuMemory.mockResolvedValue(NO_GPU_SPLIT);
    mockedApi.getModelServer.mockResolvedValue(NO_BACKENDS);
    mockedApi.getStartScreen.mockResolvedValue(START);
    mockedApi.listProviders.mockResolvedValue({ providers: [], active: null, lemonade_error: null } as unknown as Awaited<ReturnType<typeof api.listProviders>>);
    mockedApi.getActiveModel.mockResolvedValue({ provider: 'local', model: QWEN.id, label: 'Qwen', remote: false, is_default: true });
    mockedApi.selectModel.mockResolvedValue({ provider: 'local', model: QWEN.id, label: 'Qwen', remote: false, is_default: true });
    mockedApi.getWarmUp.mockResolvedValue({ state: 'ready', elapsed_seconds: 1 });
});

/** Render, and return the callback the screen reports ready through. */
function renderSetup(props: { onClose?: () => void } = {}) {
    const onReady = vi.fn();
    render(<SetupScreen onReady={onReady} {...props} />);
    return onReady;
}

/** The checks stop on what they found; Continue goes on to the recommendation. */
const planScreen = () => waitFor(() => {
    const plan = screen.queryByRole('region', { name: 'Recommended setup' });
    if (plan) return plan;
    if (screen.queryByRole('heading', { name: /^(Checking this PC|This PC is checked)$/ })) {
        const go = screen.queryByRole('button', { name: 'Continue' });
        if (go) fireEvent.click(go);
    }
    throw new Error('not on the recommendation yet: ' + (document.querySelector('#setup-title')?.textContent ?? 'no title') + ' | ' + [...document.querySelectorAll('button')].map((b) => b.textContent).join(','));
}, { timeout: 4000 });

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

async function toModels() {
    fireEvent.click(screen.getByRole('button', { name: 'Customize' }));
    await screen.findByRole('heading', { name: 'Choose your models' });
}

async function toKey() {
    fireEvent.click(screen.getByRole('radio', { name: 'Use a cloud model' }));
    clickContinue();
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
    it('stops on what it found, and Continue goes on to the recommendation', async () => {
        let answer!: (r: PreflightReport) => void;
        mockedApi.getOnboardingPreflight.mockImplementation(() => new Promise((r) => { answer = r; }));
        renderSetup();
        expect(await screen.findByRole('heading', { name: /^(Checking this PC|This PC is checked)$/ })).toBeInTheDocument();
        await waitFor(() => expect(mockedApi.getSetupPlan).toHaveBeenCalled());
        // While it checks, only "Having trouble?" can be pressed; the logs and report wait behind it.
        const enabled = () => screen.queryAllByRole('button').filter((b) => !(b as HTMLButtonElement).disabled).map((b) => b.textContent?.trim());
        expect(enabled()).toEqual(['Having trouble?']);
        expect(railStep('Check this PC')).toHaveAttribute('aria-current', 'step');
        answer(HARDWARE);
        expect(await screen.findByRole('region', { name: 'What setup found' })).toBeInTheDocument();
        expect(screen.getByRole('heading', { name: /^(Checking this PC|This PC is checked)$/ })).toBeInTheDocument();
        await planScreen();
        expect(railStep('Check this PC')).toHaveClass('is-done');
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
    });

    it('the top Next arrow does what Continue does once the checks are done', async () => {
        renderSetup();
        await screen.findByRole('region', { name: 'What setup found' });
        const next = screen.getByRole('button', { name: 'Next step' });
        expect(next).toBeEnabled();
        fireEvent.click(next);
        expect(await screen.findByRole('region', { name: 'Recommended setup' })).toBeInTheDocument();
        // From the plan, moving on starts a download: the arrow leaves that to the button.
        expect(screen.getByRole('button', { name: 'Next step' })).toBeDisabled();
    });

    it('opening Check this PC again stays there', async () => {
        await open();
        fireEvent.click(rail().getByRole('button', { name: /Check this PC/ }));
        expect(await screen.findByRole('heading', { name: /^(Checking this PC|This PC is checked)$/ })).toBeInTheDocument();
        await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
        expect(screen.getByRole('heading', { name: /^(Checking this PC|This PC is checked)$/ })).toBeInTheDocument();
        expect(screen.getByRole('region', { name: 'What setup found' })).toBeInTheDocument();
    });

    it('keeps going when the hardware could not be read, and says why on Check this PC', async () => {
        mockedApi.getOnboardingPreflight.mockRejectedValue(new Error('timed out'));
        renderSetup();
        const device = (await screen.findByText('Processor, GPU and NPU')).closest('li') as HTMLElement;
        await waitFor(() => expect(device).toHaveTextContent("couldn't be read. Setup still checks it as it goes. timed out"));
        await planScreen();
    });

    it("lists the PC's warnings on Check this PC, but not the NPU one before a model server can be asked", async () => {
        const npu = 'GAIA could not query Lemonade for NPU availability; the NPU result for the recommended model is unknown.';
        mockedApi.getOnboardingPreflight.mockResolvedValue({ ...HARDWARE, lemonade_running: false, warnings: [npu, 'Low memory.'] });
        renderSetup();
        const found = await screen.findByRole('region', { name: 'What setup found' });
        expect(found).toHaveTextContent('Low memory.');
        expect(found).not.toHaveTextContent('NPU availability');
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

    it("waits for the user when looking for their Lemonade failed, instead of installing GAIA's own", async () => {
        mockedApi.getSetupPlan.mockResolvedValue(COLD_SERVER);
        mockedApi.getLemonadeTarget.mockResolvedValue({ ...TARGET, offer: true });
        mockedApi.detectLemonade.mockRejectedValue(new Error('timed out'));
        mockedApi.startSetup.mockResolvedValue(SERVER_RUNNING);
        renderSetup();
        await screen.findByRole('button', { name: 'Check again' });
        await new Promise((r) => setTimeout(r, 50));
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: "Install GAIA's own" }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledExactlyOnceWith(false, false, { serverOnly: true }));
    });

    it("doesn't offer to install GAIA's own once it is already running", async () => {
        mockedApi.getLemonadeTarget.mockResolvedValue({ ...TARGET, offer: true });
        mockedApi.detectLemonade.mockRejectedValue(new Error('timed out'));
        mockedApi.getOnboardingPreflight.mockResolvedValue(HARDWARE);
        renderSetup();
        await screen.findByRole('button', { name: 'Check again' });
        expect(screen.queryByRole('button', { name: "Install GAIA's own" })).not.toBeInTheDocument();
    });

    it('offers Try again when starting the model server is refused', async () => {
        mockedApi.getSetupPlan.mockResolvedValue(COLD_SERVER);
        mockedApi.startSetup.mockRejectedValueOnce(new Error('connection reset')).mockResolvedValue(SERVER_RUNNING);
        renderSetup();
        fireEvent.click(await screen.findByRole('button', { name: 'Try again' }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledTimes(2));
        expect(mockedApi.startSetup).toHaveBeenLastCalledWith(false, false, { serverOnly: true });
    });

    it('starts only the model server by itself, then recommends from what it reads', async () => {
        mockedApi.getSetupPlan.mockResolvedValueOnce(COLD_SERVER).mockResolvedValue(COLD_BIG());
        mockedApi.startSetup.mockResolvedValueOnce(SERVER_RUNNING);
        mockedApi.getSetupStatus.mockResolvedValueOnce({ state: 'idle' }).mockResolvedValue(SERVER_READY);
        renderSetup();
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledExactlyOnceWith(false, false, { serverOnly: true }));
        expect(screen.getByRole('heading', { name: /^(Checking this PC|This PC is checked)$/ })).toBeInTheDocument();
        expect(screen.queryByRole('region', { name: 'Recommended setup' })).not.toBeInTheDocument();
        await planScreen();
        expect(screen.getByRole('button', { name: /Download and start/ })).toBeInTheDocument();
        expect(mockedApi.startSetup).toHaveBeenCalledTimes(1);
    });

    it("says it is getting GAIA's model server ready, with Cancel in the bottom bar that stops it", async () => {
        mockedApi.getSetupPlan.mockResolvedValue(COLD_SERVER);
        mockedApi.startSetup.mockResolvedValue({ ...SERVER_RUNNING, percent: 40 });
        mockedApi.getSetupStatus.mockResolvedValueOnce({ state: 'idle' })
            .mockResolvedValue({ state: 'cancelled', server_only: true, steps: [] });
        mockedApi.cancelSetup.mockResolvedValue({ cancelled: true });
        renderSetup();
        expect(await screen.findByText("Getting GAIA's model server ready…")).toBeInTheDocument();
        expect(screen.getByRole('progressbar', { name: "Getting GAIA's model server ready…" })).toHaveAttribute('value', '40');
        const bar = document.querySelector('.setup-footer') as HTMLElement;
        fireEvent.click(within(bar).getByRole('button', { name: 'Cancel' }));
        await waitFor(() => expect(mockedApi.cancelSetup).toHaveBeenCalledWith());
        expect(await screen.findByRole('button', { name: 'Try again' })).toBeInTheDocument();
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
            problem: { kind: 'server_unreachable', message: "Your Lemonade Server at 127.0.0.1:9 isn't answering.", start: 'Start Lemonade Server from the Lemonade tray icon.', use_gaia: true },
        }).mockResolvedValueOnce(SERVER_RUNNING);
        renderSetup();
        expect(await screen.findByText('Start Lemonade Server from the Lemonade tray icon.')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: /Use GAIA.s own instead/ }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenLastCalledWith(false, true, { serverOnly: true }));
    });
});

describe('SetupScreen: the recommendation', () => {
    it('offers this PC and the cloud as two equal choices, this PC recommended and picked', async () => {
        await open();
        const local = screen.getByRole('radio', { name: /^Run .* on this PC$/ });
        const cloud = screen.getByRole('radio', { name: 'Use a cloud model' });
        expect(local).toBeChecked();
        expect(cloud).not.toBeChecked();
        expect(screen.getByRole('region', { name: 'Recommended setup' })).toHaveTextContent('Recommended for this PC');
        const cloudCard = screen.getByRole('region', { name: 'Cloud model' });
        expect(cloudCard).toHaveTextContent('Nothing to download');
        expect(cloudCard).toHaveTextContent('Each message and the chat history are sent to the provider.');
        expect(cloudCard).not.toHaveTextContent('Recommended');
        // Customize is its own button in the bottom bar, not a link beside the cloud choice.
        const bar = within(document.querySelector('.setup-footer') as HTMLElement);
        expect(bar.getByRole('button', { name: 'Customize' })).toHaveClass('btn-secondary');
        expect(bar.getByRole('button', { name: /Download and start/ })).toHaveClass('btn-primary');
        // Picking the cloud card makes the main action go there, and starts nothing.
        fireEvent.click(cloudCard);
        expect(cloud).toBeChecked();
        expect(bar.queryByRole('button', { name: /Download and start/ })).toBeNull();
        clickContinue();
        await screen.findByRole('heading', { name: 'Connect a cloud provider' });
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
    });

    it('shows what it will do before anything starts, with no stale error', async () => {
        mockedApi.getOnboardingPreflight.mockResolvedValue(HARDWARE);
        await open();
        const hero = within(await planScreen());
        expect(hero.getByText(/You can cancel the download at any time/)).toBeInTheDocument();
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
        expect(hero).toHaveTextContent("GAIA's tested pick for a PC like this one.");
        expect(hero).toHaveTextContent('Fits this PC: needs 27.1 GB of memory, and this PC has 96.0 GB for models.');
        expect(hero).toHaveTextContent('Works offline, and your chats stay on this PC.');
    });

    it('says an NPU pick runs on the NPU, not the graphics chip', async () => {
        mockedApi.getOnboardingPreflight.mockResolvedValue(HARDWARE);
        mockedApi.getSetupPlan.mockResolvedValue(plan([QWEN, GEMMA, EMBED, FLM], { chat_model: { id: FLM.id, user_set: true, size_gb: 9.1, downloaded: false } }));
        await open();
        const hero = within(await planScreen());
        expect(hero.getByText('Ryzen AI NPU')).toBeInTheDocument();
        expect(hero.queryByText('AMD Radeon 8060S')).not.toBeInTheDocument();
        expect(hero.getByText("Reading images is off: the NPU model doesn't read images.")).toBeInTheDocument();
    });

    it('calls a saved pick the user\'s choice, not a recommendation', async () => {
        mockedApi.getSetupPlan.mockResolvedValue(plan([QWEN, GEMMA, EMBED], { chat_model: { id: QWEN.id, user_set: true, size_gb: 23.3, downloaded: false } }));
        await open();
        const hero = await planScreen();
        expect(hero).toHaveTextContent('Your choice for this PC');
        expect(hero).not.toHaveTextContent('The model we evaluated');
    });

    it('names the verified model that would not fit, and why', async () => {
        const big = { ...QWEN, fits: false as const, fit_code: 'memory' as const, need_gb: 27.1, capacity_gb: 16, planned: false, selected: false, recommended: false };
        const small = { ...GEMMA_CHAT, chat_choice: true };
        mockedApi.getSetupPlan.mockResolvedValue(plan([small, big, EMBED], { chat_model: { id: GEMMA.id, user_set: false, size_gb: 5.97, downloaded: false } }));
        await open();
        expect(await planScreen()).toHaveTextContent(`${QWEN.name} is a bigger model that answers better, but it needs about 27.1 GB of memory and this PC has 16.0 GB.`);
    });

    it("calls no model this PC can't run a bigger model", async () => {
        const npu = { ...QWEN, fits: false as const, fit_code: 'npu' as const, need_gb: 27.1, capacity_gb: 16, planned: false, selected: false, recommended: false };
        const small = { ...GEMMA_CHAT, chat_choice: true };
        mockedApi.getSetupPlan.mockResolvedValue(plan([small, npu, EMBED], { chat_model: { id: GEMMA.id, user_set: false, size_gb: 5.97, downloaded: false } }));
        await open();
        expect(await planScreen()).not.toHaveTextContent('is a bigger model');
    });

    it('opens everything that will download, components and models', async () => {
        mockedApi.getSetupPlan.mockResolvedValue({ ...COLD_BIG(), components: COMPONENTS });
        await open();
        const list = screen.getByText('See everything that will be downloaded').closest('details') as HTMLElement;
        expect(within(list).getByText('Graphics support')).toBeInTheDocument();
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
        fireEvent.click(screen.getByRole('button', { name: 'Back to GAIA' }));
        expect(onClose).toHaveBeenCalledTimes(1);
        cleanup();
        await open();
        expect(screen.queryByRole('button', { name: 'Back to GAIA' })).not.toBeInTheDocument();
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

    it('joins a run another window already started instead of showing an error', async () => {
        const live = runStatus([progress({ id: QWEN.id, state: 'downloading', bytes_done: 1e9, bytes_total: 23.3e9 })]);
        mockedApi.startSetup.mockRejectedValueOnce(new api.ApiError('busy', 409, 'busy', null));
        await open();
        mockedApi.getSetupStatus.mockResolvedValue(live);
        clickStart();
        expect(await screen.findByRole('heading', { name: 'Downloading' })).toBeInTheDocument();
        expect(screen.queryByText(/Something went wrong|already running/)).not.toBeInTheDocument();
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
        fireEvent.click(screen.getByRole('button', { name: 'Choose a different model for images' }));
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
        expect(await planScreen()).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Customize' }));
        expect(await screen.findByRole('checkbox', { name: /embeddinggemma/ })).not.toBeChecked();
        fireEvent.keyDown(document, { key: 'ArrowLeft', altKey: true });
        expect(await planScreen()).toBeInTheDocument();
    });

    it('calls onReady from Start chatting', async () => {
        const onReady = await startLocal();
        fireEvent.click(await startChatting());
        await waitFor(() => expect(onReady).toHaveBeenCalledExactlyOnceWith(undefined));
    });

    // Setup tested a chat on this model; an older pick must not be what chat opens on (#6632).
    it('opens chat on the model setup set up and tested, on the first run too', async () => {
        mockedApi.selectModel.mockResolvedValue({ provider: 'local', model: QWEN.id, label: QWEN.name, remote: false, is_default: false });
        const onReady = await startLocal();
        fireEvent.click(await startChatting());
        await waitFor(() => expect(onReady).toHaveBeenCalled());
        expect(mockedApi.selectModel).toHaveBeenCalledExactlyOnceWith(QWEN.id);
    });

    it('names the model that answers, lists vision and documents, and offers a first task', async () => {
        const done = (e: typeof QWEN) => ({ ...e, downloaded: true, size_gb: 0 });
        mockedApi.getSetupPlan.mockResolvedValueOnce(COLD_BIG()).mockResolvedValue(plan([done(QWEN), done(GEMMA), done(EMBED)]));
        const onReady = await startLocal();
        expect(await screen.findByRole('heading', { name: 'GAIA is ready' })).toBeInTheDocument();
        // Said once, by the heading; the card says what answers.
        expect(screen.getByText('Answers come from Qwen3.6-35B-A3B, on this PC.')).toBeInTheDocument();
        expect(screen.queryByText(/GAIA is ready\./)).toBeNull();
        const summary = await screen.findByText('Reading images');
        expect(summary.closest('dl')).toHaveTextContent('ChatQwen3.6-35B-A3BReading imagesGemma-4-E4B-itDocuments and memoryembeddinggemma-300m');
        expect(await screen.findByText('Start with one of these, or just start chatting.')).toBeInTheDocument();
        // Nothing to pick up on a first run.
        expect(screen.queryByRole('button', { name: /Check in/ })).toBeNull();
        fireEvent.click(await screen.findByRole('button', { name: /Research a topic/ }));
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

    it('starts llama.cpp on Vulkan when an untested backend was saved, before anything downloads', async () => {
        mockedApi.getModelServer.mockResolvedValue({ reachable: true, available_backends: [{ name: 'vulkan', state: 'installed' }], default_backend: 'vulkan', preferred_backend: 'rocm' } as never);
        mockedApi.setModelServerBackend.mockResolvedValue({ reachable: true, preferred_backend: 'vulkan' } as never);
        await startLocal();
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalled());
        expect(mockedApi.setModelServerBackend).toHaveBeenCalledWith('vulkan');
        expect(mockedApi.setModelServerBackend.mock.invocationCallOrder[0]).toBeLessThan(mockedApi.startSetup.mock.invocationCallOrder[0]);
    });

    it('lets a first-time user pick the language on the first screen', async () => {
        renderSetup();
        await screen.findByRole('region', { name: 'What setup found' });
        const reads = [mockedApi.getSetupStatus.mock.calls.length, mockedApi.getSetupPlan.mock.calls.length];
        const language = screen.getByRole('combobox', { name: 'Language' });
        fireEvent.change(language, { target: { value: 'ar' } });
        expect(document.documentElement.dir).toBe('rtl');
        // Changing language changes words only: setup is not read, or restarted, again.
        await new Promise((r) => setTimeout(r, 50));
        expect([mockedApi.getSetupStatus.mock.calls.length, mockedApi.getSetupPlan.mock.calls.length]).toEqual(reads);
        expect(localStorage.getItem('gaia-chat-locale')).toBe('ar');
        act(() => useChatStore.getState().setLocale('en'));
        localStorage.removeItem('gaia-chat-locale');
    });

    it("puts every screen's main action in the bar at the bottom, last in it", async () => {
        renderSetup();
        await screen.findByRole('region', { name: 'What setup found' });
        const bar = () => document.querySelector('.setup-footer') as HTMLElement;
        const last = () => { const all = bar().querySelectorAll('button'); return all[all.length - 1]; };
        expect(last()).toHaveTextContent('Continue');
        fireEvent.click(last()!);
        await screen.findByRole('region', { name: 'Recommended setup' });
        expect(last()).toHaveTextContent(/Download and start|Test and start/);
        await toModels();
        expect([...bar().querySelectorAll('button')].map((b) => b.textContent?.trim())).toEqual(['Back', 'Continue']);
    });

    it('keeps the steps flat: Customize is a screen of Your setup, not a step', async () => {
        await open();
        await toModels();
        expect(railLabels()).toEqual(['Check this PC', 'Your setup', 'Download', 'Test', 'Ready']);
        expect(railStep('Your setup')).toHaveAttribute('aria-current', 'step');
        expect(screen.queryByRole('button', { name: 'Change how memory is split' })).toBeNull();
    });

    it('reopens a step already passed, and not one ahead', async () => {
        await open();
        await toModels();
        expect(rail().queryByRole('button', { name: /Download/ })).not.toBeInTheDocument();
        fireEvent.click(rail().getByRole('button', { name: /Check this PC/ }));
        expect(await screen.findByRole('heading', { name: /^(Checking this PC|This PC is checked)$/ })).toBeInTheDocument();
    });

    it('marks the step that failed', async () => {
        mockedApi.startSetup.mockResolvedValue(FAILED);
        await startLocal();
        await screen.findByRole('alert');
        expect(railStep('Download')).toHaveClass('is-failed');
    });

    it('opens an earlier step during the download without stopping it', async () => {
        const run = runStatus([progress({ id: QWEN.id, state: 'downloading', bytes_done: 1e9, bytes_total: 23.3e9 })]);
        mockedApi.startSetup.mockResolvedValue(run);
        mockedApi.getSetupStatus.mockResolvedValueOnce({ state: 'idle' }).mockResolvedValue(run);
        await startLocal();
        await screen.findByRole('button', { name: 'Cancel' });
        // Not yet reached: nothing to open.
        expect(rail().queryByRole('button', { name: /Test/ })).toBeNull();

        fireEvent.click(rail().getByRole('button', { name: /Your setup/ }));
        expect(await screen.findByRole('heading', { name: 'Your setup' })).toBeInTheDocument();
        expect(railStep('Your setup')).toHaveAttribute('aria-current', 'step');
        expect(railStep('Download')).toHaveClass('is-active');
        // The choices that would start a run wait for this one.
        expect(screen.getByRole('status')).toHaveTextContent('Setup is running');
        expect(screen.getByRole('button', { name: /Download and start/ })).toBeDisabled();

        fireEvent.click(screen.getByRole('button', { name: 'Back to Download' }));
        expect(await screen.findByRole('button', { name: 'Cancel' })).toBeInTheDocument();
        expect(mockedApi.startSetup).toHaveBeenCalledTimes(1);
        expect(mockedApi.cancelSetup).not.toHaveBeenCalled();
    });

    it('after setup finished, models can be changed and downloaded again', async () => {
        mockedApi.startSetup.mockResolvedValue(READY);
        mockedApi.getSetupStatus.mockResolvedValueOnce({ state: 'idle' }).mockResolvedValue(READY);
        await startLocal();
        await startChatting();
        fireEvent.click(rail().getByRole('button', { name: /Your setup/ }));
        await screen.findByRole('heading', { name: 'Your setup' });
        await toModels();
        expect(railStep('Ready')).toHaveClass('is-done');
        clickContinue();
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledTimes(2));
        expect(await startChatting()).toBeInTheDocument();
    });

    it('Your setup on the rail reopens the Customize screen once it was chosen', async () => {
        await open();
        await toModels();
        fireEvent.click(rail().getByRole('button', { name: /Check this PC/ }));
        await screen.findByRole('heading', { name: /^(Checking this PC|This PC is checked)$/ });
        fireEvent.click(rail().getByRole('button', { name: /Your setup/ }));
        expect(await screen.findByRole('heading', { name: 'Choose your models' })).toBeInTheDocument();
    });

    it('Back and Next walk the steps setup has reached', async () => {
        const run = runStatus([progress({ id: QWEN.id, state: 'downloading', bytes_done: 1e9, bytes_total: 23.3e9 })]);
        mockedApi.startSetup.mockResolvedValue(run);
        mockedApi.getSetupStatus.mockResolvedValueOnce({ state: 'idle' }).mockResolvedValue(run);
        await startLocal();
        await screen.findByRole('button', { name: 'Cancel' });
        const steps = () => within(screen.getByRole('navigation', { name: 'Move between setup steps' }));
        fireEvent.click(steps().getByRole('button', { name: 'Previous step' }));
        expect(await screen.findByRole('heading', { name: 'Your setup' })).toBeInTheDocument();
        fireEvent.click(steps().getByRole('button', { name: 'Previous step' }));
        expect(await screen.findByRole('heading', { name: /^(Checking this PC|This PC is checked)$/ })).toBeInTheDocument();
        fireEvent.click(steps().getByRole('button', { name: 'Next step' }));
        fireEvent.click(steps().getByRole('button', { name: 'Next step' }));
        expect(await screen.findByRole('button', { name: 'Cancel' })).toBeInTheDocument();
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

    // `gaia init --profile npu` saved an NPU model this PC can't run (#6632): no confirm can make it work.
    it('refuses a saved chat model this PC can\'t run, naming it and why, without asking', async () => {
        const npu = {
            ...FLM, downloaded: false, size_gb: 3.2, planned: true, selected: true, roles: ['chat' as const],
            fits: false as const, need_gb: 3.5, capacity_gb: 16, fit_code: 'npu' as const,
            fit_reason: 'needs an AMD Ryzen AI NPU and its driver',
        };
        const saved = plan([npu, GEMMA, EMBED]);
        mockedApi.getSetupPlan.mockResolvedValue({ ...saved, chat_model: { ...saved.chat_model, id: npu.id, user_set: true } });
        await open();
        clickStart();
        expect(await screen.findByText(
            "Gemma 4 E2B (NPU) can't run on this PC. Runs only on a Ryzen AI NPU, and this PC has none that can run it. Choose another chat model.",
        )).toBeInTheDocument();
        expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
        expect(mockedApi.chooseSetupChatModel).not.toHaveBeenCalled();
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
        expect(await screen.findByRole('heading', { name: 'Choose your models' })).toBeInTheDocument();
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
        // Run on the NPU first: it picks the NPU model the user already has.
        fireEvent.click(screen.getByRole('radio', { name: 'Ryzen AI NPU' }));
        expect(chatRadio(/Gemma 4 E2B \(NPU\)/)).toBeChecked();
        // Reading images is off on the NPU: said plainly, with nothing to pick or download.
        expect(screen.getByText(/runs on the NPU, which doesn't read images, so reading images is off/)).toBeInTheDocument();
        expect(screen.queryByRole('radio', { name: /Skip for now/ })).not.toBeInTheDocument();
        clickContinue();
        await waitFor(() => expect(mockedApi.chooseSetupChatModel).toHaveBeenCalledWith(FLM.id, false));
        await waitFor(() => expect(mockedApi.skipSetupModel).toHaveBeenCalledWith(GEMMA.id, true));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledWith(false, false, expect.objectContaining({ extraModels: [] })));
    });

    it('says everything is already here on the models screen, and finishes from there', async () => {
        const on = (e: typeof QWEN) => ({ ...e, downloaded: true, size_gb: 0 });
        mockedApi.getSetupPlan.mockResolvedValue(plan([on(QWEN), on(GEMMA), on(EMBED)]));
        await open();
        await toModels();
        expect(await screen.findByText('Everything is already on this PC.')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: /Finish setup/ }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalled());
        expect(await startChatting()).toBeInTheDocument();
    });
});

describe('SetupScreen: GPU memory', () => {
    it('is an option on Customize, not a step, and goes back to the models', async () => {
        mockedApi.getGpuMemory.mockResolvedValue(gpuStatus());
        await open();
        await toModels();
        expect(railLabels()).toEqual(['Check this PC', 'Your setup', 'Download', 'Test', 'Ready']);
        expect(screen.getByText(/Graphics \(GPU\) memory: 32\.0 GB of this PC's 128\.0 GB/)).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Change how memory is split' }));
        expect(await screen.findByText('With 64.0 GB for the GPU, Qwen3.6-35B-A3B fits.')).toBeInTheDocument();
        expect(railStep('Your setup')).toHaveAttribute('aria-current', 'step');
        fireEvent.click(screen.getByRole('button', { name: 'Skip for now' }));
        expect(await screen.findByRole('heading', { name: 'Choose your models' })).toBeInTheDocument();
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
    });

    it('opens from Check this PC and goes back there', async () => {
        mockedApi.getGpuMemory.mockResolvedValue(gpuStatus());
        renderSetup();
        await screen.findByRole('region', { name: 'What setup found' });
        fireEvent.click(await screen.findByRole('button', { name: 'Change how memory is split' }));
        expect(await screen.findByRole('heading', { name: 'GPU memory' })).toBeInTheDocument();
        expect(railStep('Check this PC')).toHaveAttribute('aria-current', 'step');
        fireEvent.click(screen.getByRole('button', { name: 'Back' }));
        expect(await screen.findByRole('heading', { name: /^(Checking this PC|This PC is checked)$/ })).toBeInTheDocument();
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

    it('shows each model and the overall progress, with Cancel, and Back only looks', async () => {
        mockedApi.startSetup.mockResolvedValue(running);
        mockedApi.getSetupStatus.mockResolvedValueOnce({ state: 'idle' }).mockResolvedValue(running);
        await startLocal();
        expect(await screen.findByRole('progressbar', { name: 'Qwen3.6-35B-A3B, Downloading' })).toBeInTheDocument();
        expect(screen.getByRole('progressbar', { name: 'Overall download' })).toHaveAttribute('aria-valuenow', '26');
        expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
        // Going back only looks at an earlier step; the download keeps going.
        fireEvent.click(screen.getByRole('button', { name: 'Previous step' }));
        expect(mockedApi.cancelSetup).not.toHaveBeenCalled();
    });

    it('names the part being set up before any model row, with its download and Cancel', async () => {
        const run: SetupRunStatus = { state: 'running', steps: [], models: [], components: { server: 'done', backend: 'active' }, percent: 45 };
        mockedApi.getSetupPlan.mockResolvedValue({ ...COLD_BIG(), components: COMPONENTS });
        mockedApi.startSetup.mockResolvedValue(run);
        mockedApi.getSetupStatus.mockResolvedValueOnce({ state: 'idle' }).mockResolvedValue(run);
        mockedApi.cancelSetup.mockResolvedValue({ cancelled: true });
        await startLocal();
        const line = (await screen.findByText('Now: Graphics support')).closest('p') as HTMLElement;
        expect(line).toHaveTextContent('Now: Graphics support · 45% · 2 of 3 parts ready');
        expect(screen.queryByText('Setting things up…')).toBeNull();
        fireEvent.click(within(document.querySelector('.setup-footer') as HTMLElement).getByRole('button', { name: 'Cancel' }));
        await waitFor(() => expect(mockedApi.cancelSetup).toHaveBeenCalledWith());
    });

    it('lists every component coming to this PC, with its state from the run', async () => {
        const run: SetupRunStatus = { ...running, components: { server: 'done', backend: 'active' } };
        mockedApi.getSetupPlan.mockResolvedValue({ ...COLD_BIG(), components: COMPONENTS });
        mockedApi.startSetup.mockResolvedValue(run);
        mockedApi.getSetupStatus.mockResolvedValueOnce({ state: 'idle' }).mockResolvedValue(run);
        await startLocal();
        const list = within(await screen.findByRole('region', { name: 'Everything being set up' }));
        expect(list.getByText('Graphics support').closest('li')).toHaveTextContent('From github.com/ggml-org/llama.cpp');
        expect(list.getByText('Graphics support').closest('li')).toHaveTextContent('In progress');
        expect(list.getByText("Model server").closest('li')).toHaveTextContent('Ready');
    });

    it('says why a component failed on its row and reruns setup from a Retry there', async () => {
        const error = '`gaia init --profile gaia` failed (exit 1): GAIA 0.25.4rc11 is release candidate v0.25.4-rc11, which installs the GAIA agent built from its own commit. '
            + "Could not read https://github.com/amd/gaia/releases/download/v0.25.4-rc11/gaia-agent-release.json: HTTPSConnectionPool(host='github.com', port=443): Read timed out. (read timeout=10)";
        const stopped = runStatus([
            progress({ id: EMBED.id, roles: ['embedding'], state: 'ready', bytes_done: 0.3e9, bytes_total: 0.3e9 }),
            progress({ id: QWEN.id, state: 'ready', bytes_done: 23.3e9, bytes_total: 23.3e9 }),
        ], { state: 'failed', error, components: { server: 'done', backend: 'done', agent: 'failed' }, component_kinds: { agent: 'transient' } });
        mockedApi.getSetupPlan.mockResolvedValue({ ...COLD_BIG(), components: COMPONENTS });
        mockedApi.startSetup.mockResolvedValue(stopped);
        await startLocal();
        const list = within(await screen.findByRole('region', { name: 'Everything being set up' }));
        const agent = list.getByText('GAIA assistant').closest('li') as HTMLElement;
        expect(agent).toHaveTextContent('GAIA assistant: the download timed out or the connection dropped. Check your internet, then retry.');
        expect(within(agent).getByText(/Read timed out/).closest('details')).not.toHaveAttribute('open');
        // One clearly named way on, not an unlabelled "Finish setup".
        expect(screen.queryByRole('button', { name: /Finish setup/ })).not.toBeInTheDocument();
        expect(screen.queryByText(/Setup stopped before it could check/)).not.toBeInTheDocument();
        const calls = mockedApi.startSetup.mock.calls.length;
        fireEvent.click(within(agent).getByRole('button', { name: 'Retry GAIA assistant' }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledTimes(calls + 1));
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
            problem: { kind: 'server_unreachable', message: "Your Lemonade Server at 127.0.0.1:9 isn't answering.", start: 'Start Lemonade Server from the Lemonade tray icon.', use_gaia: true },
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

    it('tests a model again without deleting it when its test failed for want of memory', async () => {
        const failed = runStatus([
            progress({ id: QWEN.id, state: 'failed', bytes_done: 23.3e9, bytes_total: 23.3e9, error_kind: 'memory', error: 'not enough memory', can_retry: true }),
        ], { state: 'failed', error: 'A model failed its test' });
        mockedApi.startSetup.mockResolvedValue(failed);
        mockedApi.downloadSetupModel.mockResolvedValue(failed);
        await startLocal();
        fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));
        expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
        await waitFor(() => expect(mockedApi.downloadSetupModel).toHaveBeenCalledWith(QWEN.id, { keepTrying: false }));
        expect(mockedApi.removeSetupModel).not.toHaveBeenCalled();
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

    it("says last time's download didn't finish, never resumes on its own, and the bar's start picks it up", async () => {
        mockedApi.getSetupPlan.mockResolvedValue(HALF());
        renderSetup();
        const banner = await screen.findByText(/Last time’s download didn’t finish/);
        // The banner only says so; its actions are the step's own, in the bar.
        expect(within(banner.closest('.setup-banner') as HTMLElement).queryByRole('button')).toBeNull();
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
        await planScreen();
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: /Download and start/ }));
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
        expect(await screen.findByRole('heading', { name: 'Choose your models' })).toBeInTheDocument();
    });

    it('deletes a broken model and downloads it again', async () => {
        mockedApi.startSetup.mockResolvedValue(failedRun('broken'));
        mockedApi.removeSetupModel.mockResolvedValue({ ...COLD_BIG(), freed_gb: 23.3 });
        mockedApi.downloadSetupModel.mockResolvedValue(failedRun('broken'));
        await startLocal();
        fireEvent.click(await screen.findByRole('button', { name: 'Download it again' }));
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
        expect(screen.getByLabelText('Fireworks AI API key')).toHaveAttribute('type', 'password');
    });

    it('labels the key field, mentions a saved key only when there is one, and can show the key', async () => {
        await open();
        await toKey();
        const field = screen.getByLabelText('Fireworks AI API key');
        expect(field).toHaveAttribute('placeholder', 'Paste your API key');
        fireEvent.change(field, { target: { value: 'fw-secret' } });
        const show = screen.getByRole('button', { name: 'Show key' });
        fireEvent.click(show);
        expect(field).toHaveAttribute('type', 'text');
        fireEvent.click(screen.getByRole('button', { name: 'Hide key' }));
        expect(field).toHaveAttribute('type', 'password');
    });

    it('offers to leave the key blank only when one is saved', async () => {
        mockedApi.listProviders.mockResolvedValue({
            providers: [{ id: 'fireworks', name: 'Fireworks AI', remote: true, privacy_notice: '', key_source: 'stored', key_hint: '1234' }],
            active: null,
            lemonade_error: null,
        } as unknown as Awaited<ReturnType<typeof api.listProviders>>);
        await open();
        await toKey();
        await waitFor(() => expect(screen.getByLabelText('Fireworks AI API key')).toHaveAttribute('placeholder', 'Paste your API key (leave blank to use one you saved before)'));
    });

    it('lists the models as a choice with the default picked, and connects the one picked', async () => {
        connects('fireworks');
        mockedApi.getCloudPrices.mockResolvedValue({
            provider: 'fireworks', source: 'Fireworks AI list price', note: null,
            models: [
                { model: 'fireworks.m1', name: 'm1', rank: 1, note: 'best overall', price: null },
                { model: 'fireworks.m2', name: 'm2', rank: 2, note: 'cheapest', price: null },
            ],
        });
        mockedApi.startSetup.mockResolvedValue({ state: 'running', skip_chat_model: true, steps: [], models: [] });
        await open();
        await toKey();
        const group = await screen.findByRole('radiogroup', { name: 'The model GAIA uses' });
        const first = within(group).getByRole('radio', { name: /^m1/ });
        expect(first).toBeChecked();
        expect(first.closest('label')).toHaveTextContent('m1 · Default · best overall');
        fireEvent.click(within(group).getByRole('radio', { name: /^m2/ }));
        fireEvent.change(screen.getByLabelText('Fireworks AI API key'), { target: { value: 'fw-key' } });
        fireEvent.click(screen.getByRole('button', { name: /Test and connect/ }));
        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('fireworks.m2'));
        // Moving on says the key worked and which model answers.
        expect(await screen.findByText('Key accepted. Answers will come from m2.')).toBeInTheDocument();
    });

    it('fails loudly when the picked model is not offered to the key', async () => {
        connects('fireworks');
        mockedApi.getCloudPrices.mockResolvedValue({
            provider: 'fireworks', source: 'Fireworks AI list price', note: null,
            models: [
                { model: 'fireworks.m1', name: 'm1', rank: 1, note: null, price: null },
                { model: 'fireworks.gone', name: 'gone', rank: 2, note: null, price: null },
            ],
        });
        await open();
        await toKey();
        fireEvent.click(await screen.findByRole('radio', { name: /^gone/ }));
        fireEvent.click(screen.getByRole('button', { name: /Test and connect/ }));
        expect(await screen.findByRole('alert')).toHaveTextContent("gone isn't offered to this key. Pick another model.");
        expect(mockedApi.selectModel).not.toHaveBeenCalled();
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
    });

    it('connects the row shown as Default when nothing is clicked, and fails loudly if the key lacks it', async () => {
        connects('fireworks');
        // The live list ranks another model first and lacks the Default row: setup must not switch to it.
        mockedApi.listProviderModels.mockResolvedValue({
            provider: 'fireworks',
            models: [{ id: 'fireworks.other', context_length: null, downloaded: true, labels: [], rank: 1, note: null, evidence: null }],
        });
        mockedApi.getCloudPrices.mockResolvedValue({
            provider: 'fireworks', source: 'Fireworks AI list price', note: null,
            models: [{ model: 'fireworks.m1', name: 'm1', rank: 1, note: null, price: null }],
        });
        await open();
        await toKey();
        expect(await screen.findByRole('radio', { name: /^m1/ })).toBeChecked();
        fireEvent.change(screen.getByLabelText('Fireworks AI API key'), { target: { value: 'fw-key' } });
        fireEvent.click(screen.getByRole('button', { name: /Test and connect/ }));
        expect(await screen.findByRole('alert')).toHaveTextContent("m1 isn't offered to this key. Pick another model.");
        expect(mockedApi.selectModel).not.toHaveBeenCalled();
    });

    it('connects the Default row by its id, not by the live list order', async () => {
        connects('fireworks');
        mockedApi.getCloudPrices.mockResolvedValue({
            provider: 'fireworks', source: 'Fireworks AI list price', note: null,
            models: [
                { model: 'fireworks.m2', name: 'm2', rank: 1, note: null, price: null },
                { model: 'fireworks.m1', name: 'm1', rank: 2, note: null, price: null },
            ],
        });
        await open();
        await toKey();
        expect(await screen.findByRole('radio', { name: /^m2/ })).toBeChecked();
        fireEvent.change(screen.getByLabelText('Fireworks AI API key'), { target: { value: 'fw-key' } });
        fireEvent.click(screen.getByRole('button', { name: /Test and connect/ }));
        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('fireworks.m2'));
    });

    it('blocks cloud setup choices under Local only without loading their prices', async () => {
        mockedApi.listProviders.mockResolvedValue({
            providers: [
                { id: 'fireworks', name: 'Fireworks AI', remote: true, privacy_notice: '', blocked: true },
                { id: 'amd', name: 'AMD LLM Gateway', remote: true, privacy_notice: '', blocked: true },
            ],
            active: null,
            lemonade_error: null,
        } as never);
        await open();
        await toKey();

        const fireworks = await screen.findByRole('radio', { name: /Fireworks AI/ });
        const gateway = screen.getByRole('radio', { name: /AMD LLM Gateway/ });
        expect(fireworks).toBeDisabled();
        expect(gateway).toBeDisabled();
        expect(fireworks).toHaveTextContent('Blocked by Local only. Turn it off in Settings to use cloud models.');
        expect(screen.getByRole('alert')).toHaveTextContent('Blocked by Local only. Turn it off in Settings to use cloud models.');
        expect(screen.getByRole('button', { name: /Test and connect/ })).toBeDisabled();
        expect(mockedApi.getCloudPrices).not.toHaveBeenCalled();
    });

    it('says a key was found in the environment, by its last characters only', async () => {
        mockedApi.listProviders.mockResolvedValue({
            providers: [{
                id: 'fireworks', name: 'Fireworks AI', remote: true, privacy_notice: '',
                key_source: 'environment', key_hint: '0000', env_var: 'FIREWORKS_API_KEY',
            }],
            active: null,
            lemonade_error: null,
        } as unknown as Awaited<ReturnType<typeof api.listProviders>>);
        await open();
        await toKey();
        expect(await screen.findByText(
            'Found a Fireworks AI key in your environment (ends …0000). Test and connect to use it.',
        )).toBeInTheDocument();
        fireEvent.change(screen.getByLabelText('Fireworks AI API key'), { target: { value: 'fw_test_000' } });
        expect(screen.queryByText(/Found a Fireworks AI key/)).not.toBeInTheDocument();
    });

    it('says nothing about the environment when no key is there', async () => {
        await open();
        await toKey();
        expect(screen.queryByText(/in your environment/)).not.toBeInTheDocument();
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
        expect(railLabels()).toEqual(['Check this PC', 'Your setup', 'Download', 'Test', 'Ready']);
        fireEvent.change(screen.getByLabelText('Fireworks AI API key'), { target: { value: 'fw-key' } });
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: /Test and connect/ }));
        await waitFor(() => expect(mockedApi.selectModel).toHaveBeenCalledWith('fireworks.m1'));
        expect(mockedApi.connectProviderWaitingForOs).toHaveBeenCalledWith('fireworks', { api_key: 'fw-key' }, expect.any(Function), expect.any(Function));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenCalledWith(true, false, expect.objectContaining({ extraModels: [] })));
        expect(mockedApi.chooseSetupChatModel).not.toHaveBeenCalled();
        expect(mockedApi.skipSetupModel).not.toHaveBeenCalled();
        expect(await startChatting()).toBeInTheDocument();
        expect(screen.getByText(/, in the cloud\./)).toBeInTheDocument();
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
        expect(screen.queryByLabelText(/API key/)).not.toBeInTheDocument();
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
        expect(await screen.findByLabelText('Fireworks AI API key').catch(() => startChatting())).toBeInTheDocument();
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
    const FORM = 'Which model server GAIA uses';
    const pickMine = () => fireEvent.click(screen.getByRole('radio', { name: /^Use the one I have/ }));
    const proceed = () => fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    const useMine = () => {
        pickMine();
        proceed();
    };
    async function offer(found: LemonadeFound | null) {
        mockedApi.getLemonadeTarget.mockResolvedValue(TARGET);
        mockedApi.detectLemonade.mockResolvedValue({ found });
        mockedApi.setLemonadeTarget.mockResolvedValue({ ...TARGET, daemon: null });
        renderSetup();
        await screen.findByRole('region', { name: 'What setup found' });
    }

    it('asks with two choices, each saying what it means, and keeps the address in the details', async () => {
        await offer(FOUND);
        const form = await screen.findByRole('form', { name: FORM });
        expect(within(form).getByRole('group', { name: 'This PC already runs a model server. Which one should GAIA use?' })).toBeInTheDocument();
        const own = screen.getByRole('radio', { name: /^Install GAIA's own/ });
        const mine = screen.getByRole('radio', { name: /^Use the one I have/ });
        expect(own).toBeChecked();
        expect(own.closest('label')).toHaveTextContent("Install GAIA's ownRecommendedThe version GAIA is tested with");
        expect(mine.closest('label')).toHaveTextContent('Keeps one copy of each model.');
        expect(mine.closest('label')).not.toHaveTextContent('Recommended');
        // One name in setup: no "AI engine", and the address waits behind the chevron.
        expect(document.body.textContent).not.toMatch(/AI engine/);
        expect(form).not.toHaveTextContent('http://localhost:13305');
        const plain = "GAIA downloads and runs its models on your server. It leaves your server's settings and your own models as they are.";
        const more = screen.getByRole('button', { name: 'About your model server' });
        expect(more).toHaveAttribute('aria-expanded', 'false');
        fireEvent.click(more);
        expect(screen.getByText(plain)).toBeInTheDocument();
        expect(form).toHaveTextContent('Lemonade 10.3.0 · http://localhost:13305/api/v1');
    });

    it('saves the URL when the user picks theirs', async () => {
        await offer(FOUND);
        await screen.findByRole('form', { name: FORM });
        useMine();
        await waitFor(() => expect(mockedApi.setLemonadeTarget).toHaveBeenCalledExactlyOnceWith(
            { mode: 'external', base_url: FOUND.url, api_key: undefined },
        ));
        expect(await screen.findByText(/GAIA will use your model server at http:\/\/localhost:13305\/api\/v1/)).toBeInTheDocument();
    });

    it('says the checks wait on the choice, and keeps one Continue in the bottom bar', async () => {
        await offer(FOUND);
        await screen.findByRole('form', { name: FORM });
        expect(await screen.findByText('Waiting for your choice below')).toBeInTheDocument();
        const bar = () => [...(document.querySelector('.setup-footer') as HTMLElement).querySelectorAll('button')].map((b) => b.textContent?.trim());
        await waitFor(() => expect(bar()).toEqual(['Continue']));
        expect(screen.getByRole('button', { name: 'Continue' })).toHaveClass('btn-primary');
        proceed();
        await waitFor(() => expect(mockedApi.setLemonadeTarget).toHaveBeenCalledExactlyOnceWith({ mode: 'gaia' }));
        await waitFor(() => expect(bar()).toEqual(['Continue']));
    });

    it('keeps the Checks log free of unknown values and of a server verdict before the choice', async () => {
        mockedApi.getOnboardingPreflight.mockResolvedValue(HARDWARE);
        await offer(FOUND);
        await screen.findByRole('form', { name: FORM });
        fireEvent.click(screen.getByRole('button', { name: 'Having trouble?' }));
        fireEvent.click(screen.getByRole('button', { name: 'Checks log' }));
        await screen.findByText(/Waiting for you to choose which Lemonade GAIA uses/);
        const text = document.querySelector('.setup-log-pre')?.textContent ?? '';
        expect(text).toMatch(/^This PC: AMD Radeon 8060S/);
        expect(text).not.toMatch(/\?/);
        expect(text).not.toMatch(/No model server answering yet/);
    });

    it('reads the device and the models again from their server', async () => {
        await offer(FOUND);
        const plans = mockedApi.getSetupPlan.mock.calls.length;
        await screen.findByRole('form', { name: FORM });
        useMine();
        await screen.findByText(/GAIA will use your model server at/);
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
        await screen.findByRole('form', { name: FORM });
        proceed();
        await waitFor(() => expect(mockedApi.setLemonadeTarget).toHaveBeenCalledExactlyOnceWith({ mode: 'gaia' }));
        expect(await screen.findByText('GAIA will install its own model server.')).toBeInTheDocument();
    });

    it('asks for the key a protected server needs, and never shows it back', async () => {
        await offer({ ...FOUND, status: 'needs_key' });
        await screen.findByRole('form', { name: FORM });
        // The key belongs to their server: asked for once it is the one picked.
        expect(screen.queryByLabelText('Model server API key')).toBeNull();
        pickMine();
        const key = screen.getByLabelText('Model server API key');
        expect(key).toHaveAttribute('type', 'password');
        expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled();
        fireEvent.change(key, { target: { value: 'sk-secret' } });
        proceed();
        await waitFor(() => expect(mockedApi.setLemonadeTarget).toHaveBeenCalledExactlyOnceWith(
            { mode: 'external', base_url: FOUND.url, api_key: 'sk-secret' },
        ));
        await screen.findByText(/GAIA will use your model server at/);
        expect(document.body.textContent).not.toContain('sk-secret');
    });

    it('says plainly that the key was rejected, without naming a setting, and reveals the key field', async () => {
        await offer(FOUND);
        const message = 'The Lemonade Server at http://localhost:13305/api/v1 rejected the API key. Check the key it was started with (its LEMONADE_API_KEY).';
        mockedApi.setLemonadeTarget.mockRejectedValue(Object.assign(new Error(message), { reason: 'bad_key' }));
        await screen.findByRole('form', { name: FORM });
        useMine();
        expect(await screen.findByRole('alert')).toHaveTextContent("That key wasn't accepted.");
        expect(screen.queryByText(/LEMONADE_API_KEY/)).toBeNull();
        expect(screen.getByLabelText('Model server API key')).toBeInTheDocument();
    });

    it("says it couldn't check a server that didn't answer in time, and checks again", async () => {
        mockedApi.getLemonadeTarget.mockResolvedValue(TARGET);
        mockedApi.detectLemonade
            .mockResolvedValueOnce({ found: null, unchecked: [FOUND.url] })
            .mockResolvedValueOnce({ found: FOUND, unchecked: [] });
        renderSetup();
        await screen.findByRole('region', { name: 'What setup found' });
        expect(await screen.findByText(/Couldn.t check whether your model server at http:\/\/localhost:13305\/api\/v1 is running/)).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: /Check again/ }));
        expect(await screen.findByRole('radio', { name: /^Use the one I have/ })).toBeInTheDocument();
    });

    it('says plainly that a too-old server is left alone and GAIA installs its own, with nothing to do', async () => {
        const message = 'The Lemonade Server at http://localhost:8000/api/v1 is v9.0.0; GAIA needs v10.0.0 or newer. Upgrade it from https://lemonade-server.ai.';
        await offer({ ...FOUND, status: 'too_old', version: '9.0.0', message });
        expect(await screen.findByText(/older than GAIA needs, so GAIA installs its own\. Yours stays as it is\./)).toBeInTheDocument();
        // The backend's English sentence, with its upgrade instruction, never reaches the screen.
        expect(screen.queryByText(/Upgrade it/)).not.toBeInTheDocument();
        expect(screen.queryByRole('radio', { name: /^Use the one I have/ })).not.toBeInTheDocument();
        await waitFor(() => expect(screen.getByRole('button', { name: 'Continue' })).toBeInTheDocument());
    });

    it('does not look for one once a choice is saved', async () => {
        renderSetup();
        await screen.findByRole('region', { name: 'What setup found' });
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
        },
        command: 'gaia init --profile gaia',
        log_tail: [`❌ ${RAW}`],
        logs: { check: ['$ gaia init --profile gaia --server-only', `❌ ${RAW}`] },
        models: [],
    };

    it('says so plainly, says how to start it, and keeps the exception in Details', async () => {
        mockedApi.startSetup.mockResolvedValue(UNREACHABLE);
        await startLocal();
        // A saved server's address isn't set outside the app: nothing to ignore.
        expect(await screen.findByRole('button', { name: /Use GAIA.s own instead/ })).toBeInTheDocument();
        expect(screen.queryByText(/set outside the app/)).not.toBeInTheDocument();
        expect(await screen.findByRole('alert')).toHaveTextContent("Your Lemonade Server at 127.0.0.1:9 isn't answering.");
        expect(screen.getByText('Start Lemonade Server from the Lemonade tray icon.')).toBeInTheDocument();
        expect(screen.queryByText(/HTTPConnectionPool/)).not.toBeInTheDocument();
        // A failed step opens "Having trouble?" by itself.
        expect(screen.getByRole('button', { name: 'Having trouble?' })).toHaveAttribute('aria-expanded', 'true');
        fireEvent.click(screen.getByRole('button', { name: 'Checks log' }));
        expect(screen.getByText(/HTTPConnectionPool/)).toBeInTheDocument();
    });

    it("switches to GAIA's own server and runs setup again", async () => {
        mockedApi.startSetup.mockResolvedValueOnce(UNREACHABLE).mockResolvedValueOnce(READY);
        await startLocal();
        fireEvent.click(await screen.findByRole('button', { name: /Use GAIA.s own instead/ }));
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenLastCalledWith(false, true, expect.any(Object)));
        expect(await startChatting()).toBeInTheDocument();
    });

    it("says plainly that a server the user chose is too old, and makes GAIA's own the way on", async () => {
        const message = 'Your Lemonade Server at 127.0.0.1:13305 is v9.0.0; GAIA needs v10.0.0 or newer.';
        mockedApi.startSetup.mockResolvedValueOnce({
            ...UNREACHABLE,
            error: message,
            problem: { kind: 'server_too_old', message, start: null, use_gaia: true },
        }).mockResolvedValueOnce(READY);
        await startLocal();
        expect(await screen.findByText(/older than GAIA needs\. Update it, or use GAIA.s own instead\./)).toBeInTheDocument();
        // No command to run, and the versions wait behind Details.
        expect(screen.queryByText(/gaia config set/)).not.toBeInTheDocument();
        expect(screen.getByText(message).closest('details')).not.toBeNull();
        const useGaia = screen.getByRole('button', { name: /Use GAIA.s own instead/ });
        expect(useGaia).toHaveClass('btn-primary');
        expect(screen.getByRole('button', { name: 'Try again' })).toHaveClass('btn-secondary');
        fireEvent.click(useGaia);
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenLastCalledWith(false, true, expect.any(Object)));
    });

    it("offers GAIA's own when an environment setting picked the server, and says that address will be ignored (#6671)", async () => {
        mockedApi.startSetup.mockResolvedValue({
            ...UNREACHABLE,
            problem: { ...UNREACHABLE.problem!, env_url: 'http://127.0.0.1:9' },
        });
        await startLocal();
        const useGaia = await screen.findByRole('button', { name: /Use GAIA.s own instead/ });
        expect(screen.getByText(
            "This server's address (http://127.0.0.1:9) is set outside the app. If you use GAIA's own, GAIA ignores that address until it changes.",
        )).toBeInTheDocument();
        expect(screen.queryByText(/LEMONADE_BASE_URL|unset|restart GAIA/i)).not.toBeInTheDocument();
        expect(screen.queryByText(/setting outside the app/i)).not.toBeInTheDocument();
        fireEvent.click(useGaia);
        await waitFor(() => expect(mockedApi.startSetup).toHaveBeenLastCalledWith(false, true, expect.any(Object)));
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

    it("hides the gateway from a PC that can't reach it, so only AMD's network sees it", async () => {
        mockedApi.listProviders.mockResolvedValue(gateway(false));
        await open();
        await toKey();
        await waitFor(() => expect(screen.queryByRole('radio', { name: /AMD LLM Gateway/ })).toBeNull());
        expect(screen.queryByRole('button', { name: 'Check the AMD gateway again' })).toBeNull();
        expect(screen.getByRole('radio', { name: /Fireworks AI/ })).toBeEnabled();
    });

    it("greys out a gateway the user chose before and says it needs AMD's network", async () => {
        localStorage.setItem('gaia-setup-choice', 'amd');
        mockedApi.listProviders.mockResolvedValue(gateway(false));
        await open();
        await toKey();
        await waitFor(() => expect(amd()).toBeDisabled());
        expect(amd()).toHaveTextContent("Unavailable: needs AMD's network or VPN.");
    });

    it('enables the gateway after Check again, once it answers', async () => {
        localStorage.setItem('gaia-setup-choice', 'amd');
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
        localStorage.setItem('gaia-setup-choice', 'amd');
        mockedApi.listProviders.mockResolvedValue(gateway(false));
        await open();
        await toKey();
        await waitFor(() => expect(amd()).toBeDisabled());
        fireEvent.click(screen.getByRole('button', { name: 'Check the AMD gateway again' }));
        await waitFor(() => expect(mockedApi.listProviders).toHaveBeenCalledTimes(2));
        expect(amd()).toBeDisabled();
    });
});
