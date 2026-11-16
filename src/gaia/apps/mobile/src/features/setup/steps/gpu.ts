// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { GpuMemoryPreset, GpuMemoryStatus } from '@webui/types';

/** The size picked before anyone chooses: the recommended one, else keeping the split (or the first size when the GPU's share is unread). Chat in the cloud has no local model to size for, so nothing is recommended. */
export function defaultTarget(status: GpuMemoryStatus, chatInCloud = false): number | null {
    return (chatInCloud ? null : status.recommended_setting_gb) ?? (status.gpu_gb != null ? null : status.presets?.[0]?.setting_gb ?? null);
}

/** The top preset is the most GAIA allows: the CPU keeps only its floor there. */
export function maxSetting(status: GpuMemoryStatus): number | null {
    const presets = status.presets ?? [];
    const top = presets[presets.length - 1];
    return top && top.setting_gb === status.setting?.max_gb ? top.setting_gb : null;
}

/** Up to two model names a preset unlocks, by catalog name. */
export function unlockNames(preset: GpuMemoryPreset, names: Record<string, string>): string[] {
    return preset.unlocks.slice(0, 2).map((id) => names[id] ?? id);
}

/** Catalog id to model name. */
export function modelNames(catalog: readonly { id: string; name: string }[] | undefined): Record<string, string> {
    const out: Record<string, string> = {};
    for (const e of catalog ?? []) out[e.id] = e.name;
    return out;
}
