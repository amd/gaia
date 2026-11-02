// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../../services/api';
import { useChatStore } from '../../../stores/chatStore';
import { useModelStore } from '../../../stores/modelStore';
import { resetSetupDownloads, scopedStatus, useSetupDownloads } from '../../../stores/setupDownloadsStore';
import { NO_GPU_SPLIT } from '../../../test/setupApiMocks';
import { DownloadChip } from '../../setup/DownloadChip';
import { EMBED, GEMMA, GPT_OSS, QWEN, gpuStatus, plan, progress, runStatus } from '../../setup/__tests__/fixtures';
import { LocalModelsSections } from '../LocalModelsSections';

vi.mock('../../../services/api', async (importOriginal) => {
    const real = await importOriginal<typeof api>();
    const mocked = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]));
    return { ...mocked, ApiError: real.ApiError, setupConfirmFrom: real.setupConfirmFrom };
});
const mocked = vi.mocked(api);

const on = <T extends typeof QWEN>(e: T) => ({ ...e, downloaded: true, size_gb: 0 });
/** After setup: Qwen and the embedder on disk, Gemma skipped. */
const AFTER_SETUP = () => plan([on(QWEN), { ...GEMMA, skipped: true, selected: false }, on(EMBED), GPT_OSS]);

beforeEach(() => {
    vi.clearAllMocks();
    resetSetupDownloads();
    mocked.getSetupPlan.mockResolvedValue(AFTER_SETUP());
    mocked.getGpuMemory.mockResolvedValue(NO_GPU_SPLIT);
    mocked.getSetupStatus.mockResolvedValue({ state: 'idle' });
});

const row = (name: string) => screen.getByRole('checkbox', { name: new RegExp(name) });

describe('Settings > Models on this PC', () => {
    it('shows installed and skipped models with the same checklist as setup', async () => {
        render(<LocalModelsSections />);
        expect(await screen.findByRole('group', { name: 'Models on this PC' })).toBeInTheDocument();
        expect(row('Qwen3.6-35B-A3B')).toBeChecked();
        expect(row('Qwen3.6-35B-A3B')).toHaveAccessibleName(/On this PC/);
        expect(row('Gemma-4-E4B-it')).not.toBeChecked();
        expect(row('Gemma-4-E4B-it')).toHaveAccessibleName(/Download 6\.0 GB/);
        expect(screen.getByRole('note')).toHaveTextContent('Without Gemma-4-E4B-it, GAIA can’t read images or screenshots.');
        expect(screen.getByRole('button', { name: 'Apply changes' })).toBeDisabled();
    });

    it('stages a skipped model, shows the download in the Apply label, and downloads it after Apply', async () => {
        mocked.skipSetupModel.mockResolvedValue(plan([on(QWEN), GEMMA, on(EMBED), GPT_OSS]));
        mocked.downloadSetupModel.mockResolvedValue({ state: 'idle', models: [progress({ id: GEMMA.id, roles: ['vision'], state: 'downloading', bytes_total: 5.97e9 })] });
        render(<LocalModelsSections />);
        fireEvent.click(await screen.findByRole('checkbox', { name: /Gemma-4-E4B-it/ }));
        expect(mocked.skipSetupModel).not.toHaveBeenCalled();
        const apply = screen.getByRole('button', { name: 'Apply: download 6.0 GB' });
        fireEvent.click(apply);
        await waitFor(() => expect(mocked.skipSetupModel).toHaveBeenCalledExactlyOnceWith(GEMMA.id, false));
        await waitFor(() => expect(mocked.downloadSetupModel).toHaveBeenCalledWith(GEMMA.id, expect.objectContaining({ acknowledgeLowDisk: true })));
        expect(await screen.findByText('Saved.')).toBeInTheDocument();
    });

    it('asks before downloading a model too big for memory, as setup does', async () => {
        const big = { ...GPT_OSS, fits: false as const, need_gb: 70, capacity_gb: 16 };
        mocked.getSetupPlan.mockResolvedValue(plan([on(QWEN), on(GEMMA), on(EMBED), big]));
        render(<LocalModelsSections />);
        await screen.findByRole('group', { name: 'Models on this PC' });
        fireEvent.click(screen.getByText('More models GAIA can download (1)'));
        fireEvent.click(row('gpt-oss 20B'));
        fireEvent.click(screen.getByRole('button', { name: 'Apply: download 12.1 GB' }));
        const sheet = await screen.findByRole('alertdialog', { name: 'gpt-oss 20B may not fit this PC. Download it anyway?' });
        expect(sheet).toHaveTextContent('Needs about 70.0 GB; this PC has 16.0 GB.');
        expect(mocked.downloadSetupModel).not.toHaveBeenCalled();
    });

    it('asks before downloading past the free disk space', async () => {
        mocked.getSetupPlan.mockResolvedValue(plan([on(QWEN), on(GEMMA), on(EMBED), GPT_OSS], { disk: { path: 'C:\\hub', free_gb: 5, source: 'lemonade' } }));
        render(<LocalModelsSections />);
        await screen.findByRole('group', { name: 'Models on this PC' });
        fireEvent.click(screen.getByText('More models GAIA can download (1)'));
        fireEvent.click(row('gpt-oss 20B'));
        fireEvent.click(screen.getByRole('button', { name: 'Apply: download 12.1 GB' }));
        const sheet = await screen.findByRole('alertdialog', { name: 'Not enough free disk space' });
        expect(sheet).toHaveTextContent('Needs 12.1 GB; only 5.0 GB free on C:. Free 7.1 GB or untick gpt-oss 20B (saves 12.1 GB).');
    });

    it('deletes a model only after an explicit Remove and confirm, and never the chat model in use', async () => {
        mocked.removeSetupModel.mockResolvedValue({ ...plan([on(QWEN), { ...GEMMA, skipped: true }, on(EMBED)]), freed_gb: 6 });
        mocked.getSetupPlan.mockResolvedValue(plan([on(QWEN), on(GEMMA), on(EMBED)]));
        render(<LocalModelsSections />);
        await screen.findByRole('group', { name: 'Models on this PC' });
        expect(screen.queryByRole('button', { name: 'Remove Qwen3.6-35B-A3B' })).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('checkbox', { name: /Gemma-4-E4B-it/ }));
        expect(mocked.removeSetupModel).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: 'Remove Gemma-4-E4B-it' }));
        const sheet = screen.getByRole('alertdialog', { name: 'Remove Gemma-4-E4B-it?' });
        expect(sheet).toHaveTextContent('This frees 6.0 GB and turns off reading images. You can download it again.');
        expect(within(sheet).getByRole('button', { name: 'Keep' })).toHaveFocus();
        fireEvent.click(within(sheet).getByRole('button', { name: 'Remove' }));
        await waitFor(() => expect(mocked.removeSetupModel).toHaveBeenCalledExactlyOnceWith(GEMMA.id));
        expect(await screen.findByText('Removed Gemma-4-E4B-it; 6.0 GB freed.')).toBeInTheDocument();
    });

    it("says it can't list models while the model server is down", async () => {
        mocked.getSetupPlan.mockResolvedValue({ ...AFTER_SETUP(), lemonade: { mode: 'gaia', url: null, reachable: false } });
        render(<LocalModelsSections />);
        expect(await screen.findByText(/The local model server isn’t answering/)).toBeInTheDocument();
        expect(screen.queryByRole('group', { name: 'Models on this PC' })).not.toBeInTheDocument();
    });

    it('shows the GPU memory section on an integrated GPU, with the restart still pending', async () => {
        mocked.getGpuMemory.mockResolvedValue(gpuStatus({
            pending: { setting_gb: 64, previous_setting_gb: 32, source: 'apply', recorded_at: null, state: 'restart_required' },
            revert: { available: true, previous_setting_gb: 32 },
        }));
        render(<LocalModelsSections />);
        const section = await screen.findByRole('region', { name: 'GPU memory' });
        expect(within(section).getByText(/Restart your PC to use 64\.0 GB\./)).toBeInTheDocument();
        expect(within(section).getByRole('button', { name: 'Undo' })).toBeInTheDocument();
    });

    it('opens the closed Manage section when its error needs reading', async () => {
        mocked.getSetupPlan.mockRejectedValue(new Error('plan unreadable'));
        render(<LocalModelsSections />);
        const error = await screen.findByText('plan unreadable');
        expect((error.closest('details') as HTMLDetailsElement).open).toBe(true);
    });

    it('hides the GPU memory section on a discrete GPU', async () => {
        render(<LocalModelsSections />);
        await screen.findByRole('group', { name: 'Models on this PC' });
        expect(screen.queryByRole('region', { name: 'GPU memory' })).not.toBeInTheDocument();
    });

    it('shows a running download with Cancel, and its row in the checklist', async () => {
        const st = runStatus([progress({ id: GEMMA.id, roles: ['vision'], state: 'downloading', bytes_done: 2e9, bytes_total: 5.97e9 })], { state: 'idle' });
        mocked.getSetupStatus.mockResolvedValue(st);
        mocked.cancelSetup.mockResolvedValue({ cancelled: true });
        render(<LocalModelsSections />);
        const section = await screen.findByRole('region', { name: 'Downloads' });
        expect(within(section).getByRole('progressbar', { name: 'Gemma-4-E4B-it, Downloading' })).toBeInTheDocument();
        expect(row('Gemma-4-E4B-it')).toHaveAccessibleName(/Downloading 2\.0 GB of 6\.0 GB/);
        fireEvent.click(within(section).getByRole('button', { name: 'Cancel' }));
        fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Stop' }));
        await waitFor(() => expect(mocked.cancelSetup).toHaveBeenCalled());
    });
});

describe('downloads outside setup', () => {
    it("counts only this session's downloads, not the rows a finished setup left", () => {
        const after = runStatus([
            progress({ id: EMBED.id, roles: ['embedding'], state: 'ready', bytes_done: 0.3e9, bytes_total: 0.3e9 }),
            progress({ id: GEMMA.id, roles: ['vision'], state: 'ready', bytes_done: 6e9, bytes_total: 6e9 }),
            progress({ id: QWEN.id, state: 'downloading', bytes_done: 2.33e9, bytes_total: 23.3e9 }),
        ], { state: 'ready' });
        const scoped = scopedStatus(after, [QWEN.id])!;
        expect(scoped.models!.map((m) => m.id)).toEqual([QWEN.id]);
        expect(scoped.overall!.percent).toBe(10);
        // A run still going shows all of its models.
        expect(scopedStatus({ ...after, state: 'running' }, [])!.models).toHaveLength(3);
        // A failed row from the run still needs an answer, so it stays.
        const failed = runStatus([progress({ id: GEMMA.id, state: 'failed', error_kind: 'network' })], { state: 'failed' });
        expect(scopedStatus(failed, [])!.models).toHaveLength(1);
    });
});

describe('the download chip in the app chrome', () => {
    it('shows progress while a download runs and opens Settings > Model', async () => {
        mocked.getSetupStatus.mockResolvedValue(runStatus([
            progress({ id: EMBED.id, roles: ['embedding'], state: 'ready', bytes_done: 1e9, bytes_total: 1e9 }),
            progress({ id: QWEN.id, state: 'downloading', bytes_done: 1e9, bytes_total: 3e9 }),
            progress({ id: GEMMA.id, roles: ['vision'], state: 'queued', bytes_total: 1e9 }),
        ]));
        const openSettings = vi.fn();
        useChatStore.setState({ openSettings });
        render(<DownloadChip />);
        const chip = await screen.findByRole('button', { name: 'Model downloads. Open Settings to see them.' });
        expect(chip).toHaveTextContent('Downloading 2 of 3 · 40%');
        fireEvent.click(chip);
        expect(openSettings).toHaveBeenCalledWith('model');
        act(() => resetSetupDownloads());
    });

    it('stays hidden when nothing downloads', async () => {
        render(<DownloadChip />);
        await waitFor(() => expect(mocked.getSetupStatus).toHaveBeenCalled());
        expect(screen.queryByRole('button', { name: /Model downloads/ })).not.toBeInTheDocument();
        expect(useSetupDownloads.getState().status).toEqual({ state: 'idle' });
    });
});

describe('Settings > default chat model', () => {
    it('refreshes "In use" and the sidebar right after the change, not on reload', async () => {
        const refresh = vi.fn().mockResolvedValue(undefined);
        useModelStore.setState({ refresh });
        mocked.chooseSetupChatModel.mockResolvedValue(plan([on(QWEN), { ...GEMMA, skipped: true, selected: false }, on(EMBED), GPT_OSS], {
            chat_model: { id: QWEN.id, user_set: true, size_gb: 0, downloaded: true },
        }));
        mocked.getSetupPlan.mockResolvedValue(plan([on(QWEN), { ...GEMMA, skipped: true, selected: false }, on(EMBED), on(GPT_OSS)], {
            chat_model: { id: GPT_OSS.id, user_set: true, size_gb: 0, downloaded: true },
        }));
        render(<LocalModelsSections />);
        fireEvent.change(await screen.findByRole('combobox', { name: 'Default chat model' }), { target: { value: QWEN.id } });
        fireEvent.click(screen.getByRole('button', { name: /^Apply/ }));
        await waitFor(() => expect(mocked.chooseSetupChatModel).toHaveBeenCalledWith(QWEN.id, false));
        await waitFor(() => expect(refresh).toHaveBeenCalled());
    });
});

describe('the download chip counts only what is downloading', () => {
    it('leaves a failed row out of "N of M"', async () => {
        mocked.getSetupStatus.mockResolvedValue(runStatus([
            progress({ id: QWEN.id, state: 'downloading', bytes_done: 1e9, bytes_total: 3e9 }),
            progress({ id: GEMMA.id, roles: ['vision'], state: 'failed', bytes_done: 0, bytes_total: 1e9, error_kind: 'network' }),
        ], { state: 'idle' }));
        useSetupDownloads.setState({ started: [QWEN.id, GEMMA.id] });
        render(<DownloadChip />);
        const chip = await screen.findByRole('button', { name: 'Model downloads. Open Settings to see them.' });
        expect(chip).toHaveTextContent(/^Downloading 1 of 1 · /);
        act(() => resetSetupDownloads());
    });
});

describe('Settings words an unticked model for Settings', () => {
    it('says a model on this PC stays there, instead of "add it later in Settings"', async () => {
        mocked.getSetupPlan.mockResolvedValue(plan([on(QWEN), on(GEMMA), on(EMBED), GPT_OSS]));
        render(<LocalModelsSections />);
        fireEvent.click(await screen.findByRole('checkbox', { name: /Gemma-4-E4B-it/ }));
        const note = screen.getByText(/Gemma-4-E4B-it stays on this PC/);
        expect(note).toHaveTextContent('Unticking it only stops setup from downloading it again; Remove deletes it.');
        expect(screen.queryByText(/later in Settings/)).not.toBeInTheDocument();
    });

    it('offers to download a missing one by ticking it', async () => {
        render(<LocalModelsSections />);
        expect(await screen.findByText('Without Gemma-4-E4B-it, GAIA can’t read images or screenshots. Tick it to download it.')).toBeInTheDocument();
    });
});
