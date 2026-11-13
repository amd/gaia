// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../../services/api';
import type { PreflightReport, SetupPlan } from '../../../types';
import { DetectedSummary, specText } from '../DetectedSummary';
import { COLD_BIG } from './fixtures';

vi.mock('../../../services/api', async (importOriginal) => {
    const actual = await importOriginal<typeof import('../../../services/api')>();
    return { ...actual, openFileOrFolder: vi.fn() };
});

const PLAN = (): SetupPlan => ({
    ...COLD_BIG(),
    // As the real catalog has it: gpt-oss is a chat model GAIA hasn't evaluated.
    catalog: COLD_BIG().catalog!.map((e) => (e.id.startsWith('gpt-oss') ? { ...e, support: 'untested' as const } : e)),
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
        gaia_version: '0.25.4', gaia_home: 'C:\\Users\\you\\.gaia', agent_version: '0.3.3', lemonade_version: '2026.44.1',
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

    it('leads with what decides setup, and where things are kept', () => {
        render(<DetectedSummary plan={PLAN()} hardware={null} />);
        const found = screen.getByRole('region', { name: 'What setup found' });
        expect(found).toHaveTextContent('AMD Radeon(TM) 8060S Graphics');
        expect(found).toHaveTextContent('NPU');
        expect(found).toHaveTextContent('103.1 GB of 137.4 GB');
        expect(found).toHaveTextContent('653.2 GB');
        expect(found).toHaveTextContent('GAIA 0.25.4 · Lemonade 2026.44.1 · llama.cpp b11393 · FastFlowLM v1.0.7');
        // Folders are links that open them; the long path is only in the tooltip.
        expect(found).not.toHaveTextContent('huggingface');
        expect(screen.getByRole('button', { name: 'Open the models folder' })).toHaveAttribute('title', 'C:\\Users\\you\\.cache\\huggingface\\hub');
        expect(screen.getByRole('button', { name: "Open GAIA's data and logs" })).toHaveAttribute('title', 'C:\\Users\\you\\.gaia');
        // The rest waits behind All details.
        expect(found).not.toHaveTextContent('Kokoro');
        expect(found).not.toHaveTextContent('16 cores');
    });

    it('All details names every runtime, the server and the devices', () => {
        render(<DetectedSummary plan={PLAN()} hardware={null} />);
        const more = screen.getByRole('button', { name: 'All details' });
        expect(more).toHaveAttribute('aria-expanded', 'false');
        fireEvent.click(more);
        const found = screen.getByRole('region', { name: 'What setup found' });
        // Runtimes are a table: one row each, its device, version and state in their own cells.
        const kokoro = within(found).getByRole('rowheader', { name: 'Kokoro' }).closest('tr') as HTMLElement;
        // Lemonade still calls it out of date after updating it on first use: it reads as installed.
        expect([...kokoro.querySelectorAll('td')].map((c) => c.textContent)).toEqual(['cpu', 'b17', 'Installed']);
        expect(within(found).getAllByRole('columnheader', { name: 'Status' })).toHaveLength(2);
        expect(found).toHaveTextContent('http://127.0.0.1:13305/api/v1');
        expect(found).toHaveTextContent('0.3.3');
        expect(found).toHaveTextContent('AMD Radeon(TM) 8060S Graphics (gfx1151)');
        expect(found).toHaveTextContent('16 cores, 32 threads');
        expect(within(found).getAllByRole('heading').map((h) => h.textContent)).toEqual(['What setup found', 'Software', 'Runs models with', 'Models setup can download here', 'Hardware']);
        // Every model setup knows for this PC: all its jobs, whether GAIA tested it, whether it is here yet.
        const qwen = within(found).getByRole('rowheader', { name: 'Qwen3.6-35B-A3B' }).closest('tr') as HTMLElement;
        expect(qwen).toHaveTextContent('Chat');
        expect(qwen).toHaveTextContent('Verified');
        expect(qwen).toHaveTextContent('Not downloaded');
        const gptOss = within(found).getByRole('rowheader', { name: 'gpt-oss 20B' }).closest('tr') as HTMLElement;
        expect(gptOss).toHaveTextContent('Untested');
        expect(gptOss).not.toHaveTextContent('Verified');
        expect(found).not.toHaveTextContent('Not here');
        expect(found).not.toHaveTextContent('ds4');
    });

    it('says what it could not find instead of leaving it blank', () => {
        render(<DetectedSummary plan={{ ...PLAN(), system: undefined, backends: [] }} hardware={null} />);
        expect(screen.getAllByText('Not reported').length).toBeGreaterThan(0);
        fireEvent.click(screen.getByRole('button', { name: 'All details' }));
        expect(screen.getAllByText('None found').length).toBe(2);
    });

    it('names the chip as the graphics on a Mac, where no separate GPU is listed', () => {
        const mac = { ...PLAN(), system: { ...PLAN().system!, devices: PLAN().system!.devices.filter((d) => !d.kind.endsWith('gpu')) } };
        render(<DetectedSummary plan={mac} hardware={{ gpu_name: 'Apple M5 Pro' } as PreflightReport} />);
        fireEvent.click(screen.getByRole('button', { name: 'All details' }));
        const graphics = screen.getByText('Graphics').closest('div') as HTMLElement;
        expect(graphics).toHaveTextContent('Apple M5 Pro');
        expect(graphics).not.toHaveTextContent('None found');
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
        expect(text).toContain('Memory: 137.4 GB, 103.1 GB for models (AMD iGPU)');
        expect(text).toContain('Models folder: C:\\Users\\you\\.cache\\huggingface\\hub (653.2 GB free)');
        expect(text).toContain('GAIA data and logs: C:\\Users\\you\\.gaia');
    });

    it('keeps Copied up for two seconds after each copy finishes', async () => {
        vi.useFakeTimers();
        Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
        render(<DetectedSummary plan={PLAN()} hardware={null} />);
        const copy = screen.getByRole('button', { name: 'Copy these details' });
        await act(async () => { fireEvent.click(copy); });
        await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
        // A second copy starts the two seconds again.
        await act(async () => { fireEvent.click(copy); });
        await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
        expect(screen.getByRole('status')).toHaveTextContent('Copied');
        await act(async () => { await vi.advanceTimersByTimeAsync(600); });
        expect(screen.queryByRole('status')).toBeNull();
    });

    it('says which GAIA it is: a release candidate, a local build, or from source', () => {
        const withBuild = (gaia_build: NonNullable<NonNullable<SetupPlan['system']>['gaia_build']>): SetupPlan => ({
            ...PLAN(), system: { ...PLAN().system!, gaia_version: gaia_build.version, gaia_build },
        });
        const { rerender } = render(<DetectedSummary plan={withBuild({ version: '0.25.5rc2', kind: 'release_candidate', rc: '2', commit: null })} hardware={null} />);
        const found = () => screen.getByRole('region', { name: 'What setup found' });
        expect(found()).toHaveTextContent('GAIA 0.25.5 RC 2');
        expect(found()).toHaveTextContent('Release candidate');
        rerender(<DetectedSummary plan={withBuild({ version: '0.25.4', kind: 'local', rc: null, commit: null })} hardware={null} />);
        expect(found()).toHaveTextContent('Local build');
        rerender(<DetectedSummary plan={withBuild({ version: '0.25.4', kind: 'source', rc: null, commit: '57d72c9a' })} hardware={null} />);
        expect(found()).toHaveTextContent('GAIA 0.25.4 (57d72c9a)');
        expect(found()).toHaveTextContent('From source');
        expect(specText(withBuild({ version: '0.25.4', kind: 'local', rc: null, commit: null }), null)).toContain('GAIA: 0.25.4, local build');
        rerender(<DetectedSummary plan={withBuild({ version: '0.25.4', kind: 'release', rc: null, commit: null })} hardware={null} />);
        expect(found()).not.toHaveTextContent('Local build');
    });

    it('opens a folder in the file browser, and says why when it cannot', async () => {
        const opener = vi.mocked(api.openFileOrFolder);
        opener.mockResolvedValueOnce({ status: 'ok', path: '' });
        render(<DetectedSummary plan={PLAN()} hardware={null} />);
        await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Open the models folder' })); });
        expect(opener).toHaveBeenCalledWith('C:\\Users\\you\\.cache\\huggingface\\hub', false);
        opener.mockRejectedValueOnce(new Error('Path not found'));
        await act(async () => { fireEvent.click(screen.getByRole('button', { name: "Open GAIA's data and logs" })); });
        expect(screen.getByRole('alert')).toHaveTextContent("Couldn't open the folder: Path not found");
    });

    it('links to the memory split when this PC has one to change', () => {
        const change = vi.fn();
        render(<DetectedSummary plan={PLAN()} hardware={null} onChangeMemory={change} />);
        fireEvent.click(screen.getByRole('button', { name: 'Change how memory is split' }));
        expect(change).toHaveBeenCalled();
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
