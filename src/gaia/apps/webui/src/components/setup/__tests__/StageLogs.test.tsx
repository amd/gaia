// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ReactElement } from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../../services/api';
import { StageLogs, collapseProgress } from '../StageLogs';
import { runStatus } from './fixtures';

vi.mock('../../../services/api', async (importOriginal) => {
    const actual = await importOriginal<typeof import('../../../services/api')>();
    return { ...actual, createSetupReport: vi.fn(), fetchSetupReport: vi.fn() };
});
const mocked = vi.mocked(api);

const RUN = runStatus([], {
    logs: {
        check: ['$ gaia init --profile gaia --server-only', 'server: done'],
        download: ['Plan: Gemma-4-E4B-it-GGUF', 'Gemma-4-E4B-it-GGUF: downloaded'],
    },
});

/** Renders the logs with "Having trouble?" opened, as a user looking for them would. */
function renderOpen(ui: ReactElement) {
    render(ui);
    fireEvent.click(screen.getByRole('button', { name: 'Having trouble?' }));
}

describe('setup logs', () => {
    it('keeps the logs and the report behind one quiet Having trouble?', () => {
        render(<StageLogs run={RUN} />);
        expect(screen.getByRole('button', { name: 'Having trouble?' })).toHaveAttribute('aria-expanded', 'false');
        expect(screen.queryByRole('button', { name: 'Checks log' })).toBeNull();
        expect(screen.queryByRole('button', { name: 'Save a troubleshooting report' })).toBeNull();
    });

    it('opens Having trouble? by itself when a step fails', () => {
        const { rerender } = render(<StageLogs run={RUN} />);
        rerender(<StageLogs run={RUN} failed />);
        expect(screen.getByRole('button', { name: 'Having trouble?' })).toHaveAttribute('aria-expanded', 'true');
        expect(screen.getByRole('button', { name: 'Save a troubleshooting report' })).toBeEnabled();
    });

    it('keeps each file of a split download, even when only digits tell them apart', () => {
        const lines = [
            'Downloading model-00001-of-00002.gguf... 60%',
            'Downloading model-00001-of-00002.gguf... 100%',
            'Downloading model-00002-of-00002.gguf... 5%',
            'Downloading model-00002-of-00002.gguf... 30%',
        ];
        expect(collapseProgress(lines)).toEqual([lines[1], lines[3]]);
    });

    it('leaves lines without a percent, and bars split by other lines, alone', () => {
        const lines = ['Step 1 of 3', 'Step 1 of 3', 'Fetching... 10%', 'retrying', 'Fetching... 20%'];
        expect(collapseProgress(lines)).toEqual(lines);
    });

    it('keeps only the last line of a redrawn progress bar', () => {
        expect(collapseProgress([
            '$ gaia init --profile gaia',
            'Downloading Lemonade Server v2026.44.1... 10% (0.5 MB/5.2 MB)',
            'Downloading Lemonade Server v2026.44.1... 55% (2.9 MB/5.2 MB)',
            'Downloading Lemonade Server v2026.44.1... 100% (5.2 MB/5.2 MB)',
            'Starting Lemonade Server',
            'Downloading llama.cpp... 40% (56 MB/141 MB)',
        ])).toEqual([
            '$ gaia init --profile gaia',
            'Downloading Lemonade Server v2026.44.1... 100% (5.2 MB/5.2 MB)',
            'Starting Lemonade Server',
            'Downloading llama.cpp... 40% (56 MB/141 MB)',
        ]);
    });

    beforeEach(() => {
        vi.clearAllMocks();
        URL.createObjectURL = vi.fn(() => 'blob:report');
        URL.revokeObjectURL = vi.fn();
    });

    it('lists each stage that has a log, collapsed until asked for', () => {
        renderOpen(<StageLogs run={RUN} />);
        expect(screen.getByRole('button', { name: 'Checks log' })).toHaveAttribute('aria-expanded', 'false');
        expect(screen.getByRole('button', { name: 'Download log' })).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Test log' })).toBeNull();
        expect(screen.queryByText(/server: done/)).toBeNull();
        fireEvent.click(screen.getByRole('button', { name: 'Checks log' }));
        expect(screen.getByText(/server: done/)).toBeInTheDocument();
        expect(screen.queryByText(/downloaded/)).toBeNull();
    });

    it('shows what the checks saw before any run printed a line', () => {
        renderOpen(<StageLogs run={null} before={{ check: ['Found your Lemonade v2026.44.1 at http://localhost:13305/api/v1', 'Waiting for you to choose which Lemonade GAIA uses'] }} />);
        fireEvent.click(screen.getByRole('button', { name: 'Checks log' }));
        expect(screen.getByText(/Waiting for you to choose/)).toBeInTheDocument();
    });

    it('offers the report even before anything ran', () => {
        renderOpen(<StageLogs run={null} />);
        expect(screen.getByRole('button', { name: 'Save a troubleshooting report' })).toBeEnabled();
    });

    it('saves the report and says where a copy is kept', async () => {
        mocked.createSetupReport.mockResolvedValue({ name: 'diagnostics-20261102-091500.tgz', path: 'C:\\Users\\you\\.gaia\\diagnostics-20261102-091500.tgz', size_bytes: 2_400_000 });
        mocked.fetchSetupReport.mockResolvedValue(new Blob(['x']));
        const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
        renderOpen(<StageLogs run={RUN} />);
        await act(async () => {
            fireEvent.click(screen.getByRole('button', { name: 'Save a troubleshooting report' }));
        });
        expect(mocked.fetchSetupReport).toHaveBeenCalledWith('diagnostics-20261102-091500.tgz');
        expect(click).toHaveBeenCalled();
        expect(screen.getByRole('status')).toHaveTextContent('Saved (2 MB)');
        expect(screen.getByRole('status')).toHaveTextContent('.gaia\\diagnostics-20261102-091500.tgz');
        click.mockRestore();
    });

    it('says why the report could not be saved', async () => {
        mocked.createSetupReport.mockRejectedValue(new Error('disk full'));
        renderOpen(<StageLogs run={RUN} />);
        await act(async () => {
            fireEvent.click(screen.getByRole('button', { name: 'Save a troubleshooting report' }));
        });
        expect(screen.getByRole('alert')).toHaveTextContent("Couldn't save the report: disk full");
    });
});
