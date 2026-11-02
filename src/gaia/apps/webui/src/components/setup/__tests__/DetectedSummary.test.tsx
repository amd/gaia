// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SetupPlan } from '../../../types';
import { DetectedSummary, specText } from '../DetectedSummary';
import { COLD_BIG } from './fixtures';

const PLAN = (): SetupPlan => ({
    ...COLD_BIG(),
    lemonade: { mode: 'gaia', url: 'http://127.0.0.1:13305/api/v1', reachable: true },
    capacity_gb: 103.1,
    memory_source: 'AMD iGPU',
    disk: { path: 'C:\\Users\\you\\.cache\\huggingface\\hub', free_gb: 653.2, source: 'lemonade' },
    backends: [
        { recipe: 'llamacpp', backend: 'vulkan', state: 'installed', version: 'b11393', default: true },
        { recipe: 'llamacpp', backend: 'rocm', state: 'installable', version: 'b11580', default: false },
        { recipe: 'flm', backend: 'npu', state: 'installed', version: 'v1.0.7', default: true },
        { recipe: 'kokoro', backend: 'cpu', state: 'update_required', version: 'b17', default: true },
        { recipe: 'ds4', backend: 'rocm', state: 'unsupported', version: null, default: true },
    ],
    system: {
        gaia_version: '0.25.4', agent_version: '0.3.3', lemonade_version: '2026.44.1',
        os: 'Windows 11 Pro 6.3 (Build 26300)', processor: 'AMD RYZEN AI MAX+ 395', physical_memory: '128.00 GB',
        devices: [
            { kind: 'cpu', name: 'AMD RYZEN AI MAX+ 395', family: 'x86_64', available: true, cores: 16, threads: 32 },
            { kind: 'amd_gpu', name: 'AMD Radeon(TM) 8060S Graphics', family: 'gfx1151', available: true },
            { kind: 'amd_npu', name: 'AMD RYZEN AI MAX+ 395', family: 'XDNA2', available: true },
            { kind: 'nvidia_gpu', name: 'Not here', family: null, available: false },
        ],
    },
});

describe('what setup found', () => {
    afterEach(() => vi.useRealTimers());

    it('names every package version and what it runs on', () => {
        render(<DetectedSummary plan={PLAN()} hardware={null} />);
        const found = screen.getByRole('region', { name: 'What setup found' });
        expect(found).toHaveTextContent('GAIA 0.25.4');
        expect(found).toHaveTextContent('0.3.3');
        expect(found).toHaveTextContent('Lemonade 2026.44.1');
        expect(found).toHaveTextContent('llama.cpp vulkan b11393');
        expect(found).toHaveTextContent('FastFlowLM npu v1.0.7');
        expect(found).toHaveTextContent('Kokoro cpu b17 · Update needed');
        expect(found).toHaveTextContent('AMD Radeon(TM) 8060S Graphics (gfx1151)');
        expect(found).toHaveTextContent('16 cores, 32 threads');
        expect(found).not.toHaveTextContent('Not here');
        expect(found).not.toHaveTextContent('ds4');
    });

    it('says what it could not find instead of leaving it blank', () => {
        render(<DetectedSummary plan={{ ...PLAN(), system: undefined, backends: [] }} hardware={null} />);
        expect(screen.getAllByText('Not reported').length).toBeGreaterThan(0);
        expect(screen.getAllByText('None found').length).toBe(2);
    });

    it('copies the whole spec as plain English text', async () => {
        const writeText = vi.fn().mockResolvedValue(undefined);
        Object.assign(navigator, { clipboard: { writeText } });
        render(<DetectedSummary plan={PLAN()} hardware={null} />);
        await act(async () => {
            fireEvent.click(screen.getByRole('button', { name: 'Copy these details' }));
        });
        expect(writeText).toHaveBeenCalledWith(specText(PLAN(), null));
        expect(screen.getByRole('status')).toHaveTextContent('Copied');
        const text = writeText.mock.calls[0][0] as string;
        expect(text).toContain("Model server: GAIA's own Lemonade 2026.44.1 at http://127.0.0.1:13305/api/v1");
        expect(text).toContain('llama.cpp: vulkan b11393 (installed)');
        expect(text).toContain('Memory: 128.00 GB, 103.1 GB for models (AMD iGPU)');
    });

    it('says when the copy did not work', async () => {
        Object.assign(navigator, { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } });
        render(<DetectedSummary plan={PLAN()} hardware={null} />);
        await act(async () => {
            fireEvent.click(screen.getByRole('button', { name: 'Copy these details' }));
        });
        expect(screen.getByRole('status')).toHaveTextContent("Couldn't copy");
    });
});
