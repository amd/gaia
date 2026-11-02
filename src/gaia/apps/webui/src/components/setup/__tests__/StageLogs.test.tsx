// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../../services/api';
import { StageLogs } from '../StageLogs';
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

describe('setup logs', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        URL.createObjectURL = vi.fn(() => 'blob:report');
        URL.revokeObjectURL = vi.fn();
    });

    it('lists each stage that has a log, collapsed until asked for', () => {
        render(<StageLogs run={RUN} />);
        expect(screen.getByRole('button', { name: 'Checks log' })).toHaveAttribute('aria-expanded', 'false');
        expect(screen.getByRole('button', { name: 'Download log' })).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Test log' })).toBeNull();
        expect(screen.queryByText(/server: done/)).toBeNull();
        fireEvent.click(screen.getByRole('button', { name: 'Checks log' }));
        expect(screen.getByText(/server: done/)).toBeInTheDocument();
        expect(screen.queryByText(/downloaded/)).toBeNull();
    });

    it('offers the report even before anything ran', () => {
        render(<StageLogs run={null} />);
        expect(screen.getByRole('button', { name: 'Save a troubleshooting report' })).toBeEnabled();
    });

    it('saves the report and says where a copy is kept', async () => {
        mocked.createSetupReport.mockResolvedValue({ name: 'diagnostics-20261102-091500.tgz', path: 'C:\\Users\\you\\.gaia\\diagnostics-20261102-091500.tgz', size_bytes: 2_400_000 });
        mocked.fetchSetupReport.mockResolvedValue(new Blob(['x']));
        const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
        render(<StageLogs run={RUN} />);
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
        render(<StageLogs run={RUN} />);
        await act(async () => {
            fireEvent.click(screen.getByRole('button', { name: 'Save a troubleshooting report' }));
        });
        expect(screen.getByRole('alert')).toHaveTextContent("Couldn't save the report: disk full");
    });
});
