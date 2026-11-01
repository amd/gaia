// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../../services/api';
import type { GpuMemoryStatus } from '../../../types';
import { GpuMemoryPanel, gpuGbAtSetting } from '../GpuMemoryPanel';
import { gpuStatus } from './fixtures';

vi.mock('../../../services/api', async (importOriginal) => {
    const real = await importOriginal<typeof api>();
    return {
        ...real,
        getGpuMemory: vi.fn(),
        applyGpuMemory: vi.fn(),
        revertGpuMemory: vi.fn(),
        recordGpuMemoryPending: vi.fn(),
        dismissGpuMemoryPending: vi.fn(),
    };
});
const mocked = vi.mocked(api);

const NAMES = { 'Qwen3.6-35B-A3B-GGUF': 'Qwen3.6-35B-A3B', 'gpt-oss-120b-mxfp4-GGUF': 'gpt-oss 120B' };

function Harness({ initial, mode = 'setup', onSkip = vi.fn(), onContinue = vi.fn() }: { initial: GpuMemoryStatus; mode?: 'setup' | 'settings'; onSkip?: () => void; onContinue?: () => void }) {
    const [status, setStatus] = useState(initial);
    return <GpuMemoryPanel status={status} onStatus={setStatus} mode={mode} names={NAMES} onSkip={onSkip} onContinue={onContinue} onBack={vi.fn()} />;
}

const restartPending = (over: Partial<GpuMemoryStatus> = {}) => gpuStatus({
    pending: { setting_gb: 64, previous_setting_gb: 32, source: 'apply', recorded_at: '2026-10-31T10:00:00Z', state: 'restart_required' },
    revert: { available: true, previous_setting_gb: 32 },
    ...over,
});

beforeEach(() => {
    vi.clearAllMocks();
});

describe('GPU memory: what a bigger share unlocks', () => {
    it('shows the split, the target and the model it unlocks, preselecting the smallest that does', () => {
        render(<Harness initial={gpuStatus()} />);
        expect(screen.getByText(/This PC shares 128\.0 GB between the CPU and the GPU\. The GPU has 32\.0 GB now\./)).toBeInTheDocument();
        expect(screen.getByRole('img', { name: 'Memory split: CPU 96.0 GB, GPU 32.0 GB' })).toBeInTheDocument();
        expect(screen.getByRole('radio', { name: 'GPU 64.0 GB' })).toHaveAttribute('aria-checked', 'true');
        expect(screen.getByText('With 64.0 GB for the GPU, Qwen3.6-35B-A3B fits.')).toBeInTheDocument();
        expect(screen.getByText(/GAIA keeps at least 16\.0 GB for Linux and your apps\. Changing it needs administrator permission and a restart\./)).toBeInTheDocument();
    });

    it('names at most two unlocked models, and moves between sizes with the arrow keys', () => {
        render(<Harness initial={gpuStatus()} />);
        const group = screen.getByRole('radiogroup', { name: 'GPU memory' });
        fireEvent.keyDown(group, { key: 'ArrowRight' });
        expect(screen.getByRole('radio', { name: 'GPU 96.0 GB' })).toHaveAttribute('aria-checked', 'true');
        expect(screen.getByRole('radio', { name: 'GPU 96.0 GB' })).toHaveFocus();
        expect(screen.getByText('With 96.0 GB for the GPU, Qwen3.6-35B-A3B and gpt-oss 120B fit.')).toBeInTheDocument();
    });

    it('lets setup skip the step', () => {
        const onSkip = vi.fn();
        render(<Harness initial={gpuStatus()} onSkip={onSkip} />);
        fireEvent.click(screen.getByRole('button', { name: 'Skip for now' }));
        expect(onSkip).toHaveBeenCalled();
    });
});

describe('GPU memory: apply', () => {
    it('asks first, naming admin rights, the restart and the saved value, with Cancel focused', () => {
        render(<Harness initial={gpuStatus()} />);
        fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
        const sheet = screen.getByRole('alertdialog', { name: 'Give the GPU 64.0 GB?' });
        expect(sheet).toHaveTextContent('needs administrator permission and a restart');
        expect(sheet).toHaveTextContent('Current setting: 32.0 GB, saved for undo.');
        expect(within(sheet).getByRole('button', { name: 'Cancel' })).toHaveFocus();
        fireEvent.click(within(sheet).getByRole('button', { name: 'Cancel' }));
        expect(mocked.applyGpuMemory).not.toHaveBeenCalled();
    });

    it('applies after consent and says to restart, never restarting itself', async () => {
        mocked.applyGpuMemory.mockResolvedValue(restartPending());
        const onContinue = vi.fn();
        render(<Harness initial={gpuStatus()} onContinue={onContinue} />);
        fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
        fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Continue' }));
        expect(mocked.applyGpuMemory).toHaveBeenCalledWith(64);
        expect(await screen.findByText(/^Done\. Restart your PC to use 64\.0 GB\./)).toBeInTheDocument();
        expect(screen.getByText(/GAIA never restarts it for you; save your work first\. Downloads can start now; restart when they finish\./)).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
        expect(onContinue).toHaveBeenCalled();
    });

    it('says nothing changed when admin permission is refused, and offers three ways on', async () => {
        mocked.applyGpuMemory.mockRejectedValue(new api.ApiError('denied', 403, 'Permission was not given', null));
        render(<Harness initial={gpuStatus()} />);
        fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
        fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Continue' }));
        const alert = await screen.findByRole('alert');
        expect(alert).toHaveTextContent('Permission wasn’t given, so nothing changed.');
        expect(within(alert).getByRole('button', { name: 'Try again' })).toBeInTheDocument();
        expect(within(alert).getByRole('button', { name: 'Show me how' })).toBeInTheDocument();
        expect(within(alert).getByRole('button', { name: 'Skip for now' })).toBeInTheDocument();
    });

    it("shows the backend's reason when the write fails", async () => {
        mocked.applyGpuMemory.mockRejectedValue(new api.ApiError('The administrator step failed (exit 2): no space', 500, '', null));
        render(<Harness initial={gpuStatus()} />);
        fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
        fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Continue' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('The administrator step failed (exit 2): no space');
    });
});

describe('GPU memory: guided', () => {
    const windows = () => gpuStatus({
        os: 'windows', method: 'guided', method_reason: 'On Windows this is set in AMD Software.',
        setting: { name: 'Variable Graphics Memory', current_gb: 32, min_gb: 4, max_gb: 96 },
    });

    it('has no Apply where GAIA cannot set it, and walks through the exact steps', async () => {
        mocked.getGpuMemory.mockResolvedValue({ ...windows(), guided_steps: ['Open AMD Software.', 'Set Variable Graphics Memory to 60 GiB.', 'Run: amd-ttm --set 60'] });
        render(<Harness initial={windows()} />);
        expect(screen.queryByRole('button', { name: 'Apply' })).not.toBeInTheDocument();
        expect(screen.getByText('On Windows this is set in AMD Software.')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Show me how' }));
        expect(await screen.findByText('Set Variable Graphics Memory to 60 GiB.')).toBeInTheDocument();
        expect(mocked.getGpuMemory).toHaveBeenCalledWith(64);
        expect(screen.getByRole('button', { name: 'Copy amd-ttm --set 60' })).toBeInTheDocument();
    });

    it("re-reads the split after I've done this instead of trusting it", async () => {
        mocked.getGpuMemory.mockResolvedValue(windows());
        mocked.recordGpuMemoryPending.mockResolvedValue({
            ...windows(),
            pending: { setting_gb: 64, previous_setting_gb: 32, source: 'guided', recorded_at: null, state: 'restart_required' },
        });
        render(<Harness initial={windows()} />);
        fireEvent.click(screen.getByRole('button', { name: 'Show me how' }));
        fireEvent.click(await screen.findByRole('button', { name: 'I’ve done this' }));
        expect(mocked.recordGpuMemoryPending).toHaveBeenCalledWith(64);
        expect(await screen.findByText(/Restart your PC to use 64\.0 GB\./)).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Keep 32.0 GB' })).toBeInTheDocument();
    });
});

describe('GPU memory: after the restart', () => {
    it('confirms the new split once it took effect', async () => {
        mocked.dismissGpuMemoryPending.mockResolvedValue(gpuStatus({ gpu_gb: 64, cpu_gb: 64 }));
        render(<Harness initial={gpuStatus({
            gpu_gb: 64, cpu_gb: 64,
            pending: { setting_gb: 64, previous_setting_gb: 32, source: 'apply', recorded_at: null, state: 'took_effect' },
        })} />);
        expect(screen.getByRole('status')).toHaveTextContent('GPU memory is now 64.0 GB.');
        fireEvent.click(screen.getByRole('button', { name: 'OK' }));
        await waitFor(() => expect(mocked.dismissGpuMemoryPending).toHaveBeenCalled());
    });

    it('says honestly when the PC still reports the old split, with Try again, Show me how and Keep', async () => {
        mocked.dismissGpuMemoryPending.mockResolvedValue(gpuStatus());
        render(<Harness initial={gpuStatus({
            pending: { setting_gb: 64, previous_setting_gb: 32, source: 'apply', recorded_at: null, state: 'not_applied' },
        })} />);
        expect(screen.getByText('The PC still reports 32.0 GB. The change may not have applied. Try again, or follow the steps to set it yourself.')).toBeInTheDocument();
        expect(screen.getAllByRole('button', { name: 'Try again' }).length).toBeGreaterThan(0);
        expect(screen.getAllByRole('button', { name: 'Show me how' }).length).toBeGreaterThan(0);
        fireEvent.click(screen.getByRole('button', { name: 'Keep 32.0 GB' }));
        await waitFor(() => expect(mocked.dismissGpuMemoryPending).toHaveBeenCalled());
    });

    it('keeps a restart-pending notice in Settings, with Undo through the same consent', async () => {
        mocked.revertGpuMemory.mockResolvedValue(restartPending({ pending: { setting_gb: 32, previous_setting_gb: 64, source: 'revert', recorded_at: null, state: 'restart_required' } }));
        render(<Harness initial={restartPending()} mode="settings" />);
        expect(screen.getByText(/Restart your PC to use 64\.0 GB\./)).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
        const sheet = screen.getByRole('alertdialog', { name: 'Undo the GPU memory change?' });
        expect(sheet).toHaveTextContent('GAIA will put the setting back to 32.0 GB. This needs administrator permission and a restart.');
        expect(within(sheet).getByRole('button', { name: 'Cancel' })).toHaveFocus();
        fireEvent.click(within(sheet).getByRole('button', { name: 'Continue' }));
        await waitFor(() => expect(mocked.revertGpuMemory).toHaveBeenCalled());
        expect(await screen.findByText(/Restart your PC to use 32\.0 GB\./)).toBeInTheDocument();
    });
});

describe('GPU memory: one set of numbers', () => {
    // Linux: the knob is the GTT limit; the GPU's share adds the small BIOS carve-out.
    const linux = gpuStatus({
        gpu_gb: 16, cpu_gb: 112, setting: { name: 'GPU memory limit (GTT)', current_gb: 15.5, min_gb: 4, max_gb: 111.5 },
        presets: [{ setting_gb: 32, gpu_gb: 32.5, unlocks: ['Qwen3.6-35B-A3B-GGUF'] }],
    });

    it('words a setting as the GPU share the bar shows', () => {
        expect(gpuGbAtSetting(linux, 15.5)).toBe(16);
        expect(gpuGbAtSetting(linux, 32)).toBe(32.5);
        expect(gpuGbAtSetting(linux, 70)).toBe(70);
    });

    it('says "still reports" and "Keep" in the same numbers as the split', () => {
        render(<Harness initial={{ ...linux, pending: { setting_gb: 32, previous_setting_gb: 15.5, source: 'apply', recorded_at: null, state: 'not_applied' } }} />);
        expect(screen.getByText(/^The PC still reports 16\.0 GB\./)).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Keep 16.0 GB' })).toBeInTheDocument();
        expect(screen.getByRole('img', { name: 'Memory split: CPU 112.0 GB, GPU 16.0 GB' })).toBeInTheDocument();
    });
});
