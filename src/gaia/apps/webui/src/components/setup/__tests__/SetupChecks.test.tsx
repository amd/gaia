// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { PreflightReport, SetupPlan, SetupRunStatus } from '../../../types';
import { SetupChecks } from '../SetupChecks';
import { COLD_BIG } from './fixtures';

const HARDWARE = {
    compatible: true, blockers: [], warnings: [], ram_gb: 96, gpu_name: 'AMD Radeon 8060S',
    npu_detected: true, disk_free_gb: 399.6, lemonade_running: true,
} as unknown as PreflightReport;

const DOWN: SetupPlan = { ...COLD_BIG(), lemonade: { mode: 'gaia', url: null, reachable: false } };
const row = (name: string) => screen.getByText(name).closest('li') as HTMLElement;

function show(over: { hardware?: PreflightReport | null; hardwareError?: string | null; plan?: SetupPlan | null; serverRun?: SetupRunStatus | null; awaitingChoice?: boolean } = {}) {
    render(<SetupChecks hardware={null} hardwareError={null} plan={null} serverRun={null} {...over} />);
}

describe('Checking this PC', () => {
    it('shows the server check as a question, not work, while it waits on the user', () => {
        show({ hardware: HARDWARE, plan: DOWN, awaitingChoice: true });
        const server = row('AI engine');
        expect(server).toHaveClass('is-question');
        expect(server).toHaveTextContent('Choose below');
        expect(server.querySelector('.spin')).toBeNull();
    });

    it('starts with the hardware being read and the server waiting its turn', () => {
        show();
        expect(row('Processor, GPU and NPU')).toHaveClass('is-active');
        expect(row('Memory for models')).toHaveClass('is-active');
        expect(row('AI engine')).toHaveClass('is-waiting');
        expect(screen.getByText('Checks done: 0 of 5')).toBeInTheDocument();
    });

    it('fills in what it found and counts the finished checks', () => {
        show({ hardware: HARDWARE, plan: DOWN });
        expect(row('Processor, GPU and NPU')).toHaveTextContent('AMD Radeon 8060S · Ryzen AI NPU');
        expect(row('Memory for models')).toHaveTextContent('96.0 GB');
        expect(row('Disk space')).toHaveTextContent('400 GB free');
        expect(row('AI engine')).toHaveClass('is-active');
        expect(screen.getByText('Checks done: 3 of 5')).toBeInTheDocument();
        expect(screen.getByRole('progressbar', { name: 'Check this PC' })).toHaveAttribute('value', '60');
    });

    it('folds into one line once every check passed, which opens to each check', () => {
        show({ hardware: HARDWARE, plan: COLD_BIG() });
        expect(screen.getByRole('status')).toHaveTextContent('All 5 checks passed');
        expect(screen.queryByText('Memory')).toBeNull();
        const toggle = screen.getByRole('button', { name: 'All 5 checks passed' });
        expect(toggle).toHaveAttribute('aria-expanded', 'false');
        fireEvent.click(toggle);
        expect(toggle).toHaveAttribute('aria-expanded', 'true');
        expect(row('Disk space')).toHaveTextContent('400 GB free');
        expect(row('AI engine')).not.toHaveTextContent('Running');
        expect(row('AI engine')).toHaveTextContent("GAIA's own");
        expect(row('Models for this PC')).toHaveTextContent(/\d+ available, \d+ already here/);
    });

    it('says which Lemonade answered and whose it is, not that it is running', () => {
        const plan = (mode: 'gaia' | 'external') => ({
            ...COLD_BIG(), lemonade: { mode, url: 'http://localhost:13305/api/v1', reachable: true }, system: { lemonade_version: '2026.44.1' },
        }) as unknown as SetupPlan;
        const { unmount } = render(<SetupChecks hardware={HARDWARE} hardwareError={null} plan={plan('gaia')} serverRun={null} />);
        fireEvent.click(screen.getByRole('button', { name: 'All 5 checks passed' }));
        expect(row('AI engine')).toHaveTextContent("Lemonade 2026.44.1 · GAIA's own");
        unmount();
        render(<SetupChecks hardware={HARDWARE} hardwareError={null} plan={plan('external')} serverRun={null} />);
        fireEvent.click(screen.getByRole('button', { name: 'All 5 checks passed' }));
        expect(row('AI engine')).toHaveTextContent('Lemonade 2026.44.1 · Yours');
    });

    it('shows the same model capacity as the next setup screen, not the OS-visible RAM', () => {
        // 128 GB installed with 64 GB set aside for the GPU: Windows reports 63.6 GB,
        // while setup chooses models from its 103.1 GB model-memory capacity.
        const plan = { ...DOWN, capacity_gb: 103.1 } as SetupPlan;
        show({ hardware: { ...HARDWARE, ram_gb: 63.6 }, plan });
        expect(row('Memory for models')).toHaveTextContent('103.1 GB');
    });

    it('marks the hardware rows failed when they could not be read, inventing no value', () => {
        show({ hardwareError: 'timed out', plan: DOWN });
        expect(row('Memory for models')).toHaveClass('is-failed');
        expect(row('Memory for models')).not.toHaveTextContent('GB');
    });

    it('marks the server failed when the server run did', () => {
        show({ hardware: HARDWARE, plan: DOWN, serverRun: { state: 'failed', server_only: true, steps: [] } });
        expect(row('AI engine')).toHaveClass('is-failed');
        expect(row('Models for this PC')).toHaveClass('is-waiting');
    });
});
