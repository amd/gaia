// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A failed answer that names a saved problem report offers to save the bundle. */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MessageBubble } from '../MessageBubble';
import { namesFailureRecord } from '../TroubleshootingReport';
import * as api from '../../services/api';
import type { Message } from '../../types';
import { classifyChatError } from '../../utils/streamErrors';

vi.mock('../../services/api', async (importOriginal) => {
    const actual = await importOriginal<typeof import('../../services/api')>();
    return { ...actual, createSetupReport: vi.fn(), fetchSetupReport: vi.fn() };
});
const mocked = vi.mocked(api);

const FAILED =
    "The model call failed at step 1, so this request is unfinished. Couldn't reach the local LLM.\n\n"
    + 'A problem report for this was saved on this PC as GF-20261104-192233-3f2a; nothing was sent anywhere.';

const base: Message = {
    id: 1,
    session_id: 'sess-1',
    role: 'assistant',
    content: FAILED,
    created_at: '2026-11-04T19:22:33.000Z',
    rag_sources: null,
};

describe('failure report on a message', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        URL.createObjectURL = vi.fn(() => 'blob:report');
        URL.revokeObjectURL = vi.fn();
    });

    it('recognises only a real record id', () => {
        expect(namesFailureRecord(FAILED)).toBe(true);
        expect(namesFailureRecord('GF-2026-1')).toBe(false);
        expect(namesFailureRecord('xGF-20261104-192233-3f2az')).toBe(false);
    });

    it('offers no report on an ordinary answer', () => {
        render(<MessageBubble message={{ ...base, content: 'Ulaanbaatar.' }} />);
        expect(screen.queryByRole('button', { name: 'Save a troubleshooting report' })).toBeNull();
    });

    it('keeps the report on a live failure the chat put in its own words', () => {
        const err = new Error(`Lemonade did not answer.\n\n${FAILED.split('\n\n')[1]}`);
        const { text } = classifyChatError(err);
        expect(text).toContain('GF-20261104-192233-3f2a');
        render(<MessageBubble message={{ ...base, content: text, failed: true, failureText: err.message }} />);
        expect(screen.getByRole('button', { name: 'Save a troubleshooting report' })).toBeInTheDocument();
    });

    it('offers the report when only the raw failure names it', () => {
        render(<MessageBubble message={{ ...base, content: 'The model server did not answer.', failed: true, failureText: FAILED }} />);
        expect(screen.getByRole('button', { name: 'Save a troubleshooting report' })).toBeInTheDocument();
    });

    it('saves the bundle from the failed message', async () => {
        mocked.createSetupReport.mockResolvedValue({ name: 'diagnostics-20261104-192300.tgz', path: '/home/you/.gaia/diagnostics-20261104-192300.tgz', size_bytes: 1_200_000 });
        mocked.fetchSetupReport.mockResolvedValue(new Blob(['x']));
        const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
        render(<MessageBubble message={base} />);
        expect(screen.getByText(/nothing is sent unless you share it/)).toBeInTheDocument();
        await act(async () => {
            fireEvent.click(screen.getByRole('button', { name: 'Save a troubleshooting report' }));
        });
        expect(mocked.fetchSetupReport).toHaveBeenCalledWith('diagnostics-20261104-192300.tgz');
        expect(click).toHaveBeenCalled();
        expect(screen.getByRole('status')).toHaveTextContent('/home/you/.gaia/diagnostics-20261104-192300.tgz');
    });

    it('says why when the report could not be saved', async () => {
        mocked.createSetupReport.mockRejectedValue(new Error('disk full'));
        render(<MessageBubble message={base} />);
        await act(async () => {
            fireEvent.click(screen.getByRole('button', { name: 'Save a troubleshooting report' }));
        });
        expect(screen.getByRole('alert')).toHaveTextContent('disk full');
    });
});
