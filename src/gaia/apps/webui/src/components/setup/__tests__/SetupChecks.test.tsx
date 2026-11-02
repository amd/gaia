// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react';
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
        const server = row("GAIA's model server");
        expect(server).toHaveClass('is-question');
        expect(server).toHaveTextContent('Choose below');
        expect(server.querySelector('.spin')).toBeNull();
    });

    it('starts with the hardware being read and the server waiting its turn', () => {
        show();
        expect(row('Processor, GPU and NPU')).toHaveClass('is-active');
        expect(row('Memory')).toHaveClass('is-active');
        expect(row("GAIA's model server")).toHaveClass('is-waiting');
        expect(screen.getByText('Checks done: 0 of 5')).toBeInTheDocument();
    });

    it('fills in what it found and counts the finished checks', () => {
        show({ hardware: HARDWARE, plan: DOWN });
        expect(row('Processor, GPU and NPU')).toHaveTextContent('AMD Radeon 8060S · Ryzen AI NPU');
        expect(row('Memory')).toHaveTextContent('96 GB');
        expect(row('Disk space')).toHaveTextContent('400 GB free');
        expect(row("GAIA's model server")).toHaveClass('is-active');
        expect(screen.getByText('Checks done: 3 of 5')).toBeInTheDocument();
        expect(screen.getByRole('progressbar', { name: 'Check this PC' })).toHaveAttribute('value', '60');
    });

    it('folds into one line once every check passed', () => {
        show({ hardware: HARDWARE, plan: COLD_BIG() });
        expect(screen.getByRole('status')).toHaveTextContent('All 5 checks passed');
        expect(screen.queryByText('Memory')).toBeNull();
    });

    it('marks the hardware rows failed when they could not be read, inventing no value', () => {
        show({ hardwareError: 'timed out', plan: DOWN });
        expect(row('Memory')).toHaveClass('is-failed');
        expect(row('Memory')).not.toHaveTextContent('GB');
    });

    it('marks the server failed when the server run did', () => {
        show({ hardware: HARDWARE, plan: DOWN, serverRun: { state: 'failed', server_only: true, steps: [] } });
        expect(row("GAIA's model server")).toHaveClass('is-failed');
        expect(row('Supported backends and models')).toHaveClass('is-waiting');
    });
});
