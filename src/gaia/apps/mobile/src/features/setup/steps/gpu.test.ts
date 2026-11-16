// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { splitFigures } from '@webui/components/setup/gpuMemory';
import type { GpuMemoryStatus } from '@webui/types';

import { defaultTarget, maxSetting, unlockNames } from './gpu';

const status = (s: Partial<GpuMemoryStatus>): GpuMemoryStatus => ({
    applies: true, show_in_setup: true, hidden_reason: null, os: 'windows', method: 'apply', method_reason: '',
    total_gb: 64, gpu_gb: 16, setting: { name: 'Variable Graphics Memory', current_gb: 16, min_gb: 0, max_gb: 48 },
    presets: [{ setting_gb: 32, gpu_gb: 32, unlocks: ['a', 'b', 'c'] }, { setting_gb: 48, gpu_gb: 48, unlocks: [] }],
    recommended_setting_gb: null, ...s,
});

describe('GPU memory presets', () => {
    it('picks the recommended size, else keeps the split', () => {
        expect(defaultTarget(status({ recommended_setting_gb: 32 }))).toBe(32);
        expect(defaultTarget(status({}))).toBeNull();
        expect(defaultTarget(status({ gpu_gb: undefined }))).toBe(32);
    });

    it('keeps the split when chat runs in the cloud, whatever is recommended', () => {
        expect(defaultTarget(status({ recommended_setting_gb: 32 }), true)).toBeNull();
        expect(defaultTarget(status({ gpu_gb: undefined }), true)).toBe(32);
    });

    it('marks only a top preset that reaches the setting maximum as Max', () => {
        expect(maxSetting(status({}))).toBe(48);
        expect(maxSetting(status({ setting: { name: 'x', current_gb: 16, min_gb: 0, max_gb: 64 } }))).toBeNull();
    });

    it('names at most two unlocked models, falling back to the id', () => {
        const p = status({}).presets![0]!;
        expect(unlockNames(p, { a: 'Model A' })).toEqual(['Model A', 'b']);
    });
});

describe('split figures', () => {
    it('adds the picked size to the GPU and leaves the rest to the CPU', () => {
        expect(splitFigures(status({}), 32)).toEqual({ total: 640, gpu: 160, extra: 160, cpu: 320, target: 320 });
    });

    it('adds nothing when the pick is not bigger than now', () => {
        expect(splitFigures(status({}), 16)).toMatchObject({ extra: 0, cpu: 480, target: null });
    });
});
