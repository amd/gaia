// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Plain-data helpers for the GPU memory step, shared by the web panel and the phone app. */

import type { Say } from '../../i18n/registry';
import type { GpuMemoryStatus } from '../../types';
import { formatSize } from './format';

/** "Windows" / "Linux", for "GAIA keeps at least 16 GB for Windows and your apps." */
export function osName(os: string): string {
    return os === 'windows' ? 'Windows' : os === 'linux' ? 'Linux' : os;
}

/** The literal a guided step asks the user to run, for its Copy button. */
export function copyableCommand(step: string): string | null {
    const m = /^Run:\s*(.+)$/.exec(step) ?? /^(sudo .+)$/.exec(step);
    return m ? m[1].trim() : null;
}

/**
 * The GPU's share once the knob is at `settingGb`, from the backend's presets,
 * so a message says the same number the split bar shows. Null when no preset
 * names it: the knob's own value is not what the GPU gets, so it is never shown.
 */
export function gpuGbAtSetting(status: GpuMemoryStatus, settingGb: number): number | null {
    if (status.setting && Math.abs(status.setting.current_gb - settingGb) < 0.05 && status.gpu_gb != null) return status.gpu_gb;
    return status.presets?.find((p) => Math.abs(p.setting_gb - settingGb) < 0.05)?.gpu_gb ?? null;
}

/** "Restart to use N GB", or without the size when the backend didn't name it. */
export function restartLine(say: Say, gpuGb: number | null, applied = false): string {
    if (gpuGb == null) return say(applied ? 'gpu.appliedAny' : 'gpu.pending.restartAny');
    return say(applied ? 'gpu.applied' : 'gpu.pending.restart', { gb: formatSize(gpuGb) });
}

export interface SplitFigures {
    /** Whole figures in GB, as the legend shows them; they add up to `total`. */
    total: number;
    gpu: number;
    /** What the picked size adds to the GPU; 0 when it adds nothing. */
    extra: number;
    cpu: number;
    /** The GPU's size after the change, or null when nothing is added. */
    target: number | null;
}

/** The split in tenths of a GB, rounded once so the legend's figures add up to the total shown. */
export function splitFigures(status: GpuMemoryStatus, targetGpuGb: number | null): SplitFigures {
    const tenths = (gb: number) => Math.round(gb * 10);
    const total = tenths(status.total_gb ?? 0);
    const gpu = tenths(status.gpu_gb ?? 0);
    const target = targetGpuGb != null && tenths(targetGpuGb) > gpu ? tenths(targetGpuGb) : null;
    const extra = target != null ? target - gpu : 0;
    return { total, gpu, extra, cpu: Math.max(0, total - gpu - extra), target };
}
