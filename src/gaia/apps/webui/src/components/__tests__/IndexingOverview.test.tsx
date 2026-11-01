// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatIndexingNotice, FINISHED_NOTICE_MS, IndexingIndicator, IndexingSummary } from '../IndexingOverview';
import * as api from '../../services/api';
import { refreshIndexing } from '../../services/indexingPoll';
import { useChatStore } from '../../stores/chatStore';
import { timeLeftLabel, statusEventLabel } from '../../utils/indexingLabels';
import { translator } from '../../i18n/useT';
import { agentEventToStep } from '../../utils/streamSteps';
import type { Document } from '../../types';

vi.mock('../../services/api', () => ({
    dismissIndexingBatch: vi.fn(() => Promise.resolve()),
    getIndexingOverview: vi.fn(),
    listDocuments: vi.fn(),
}));

const mockedApi = vi.mocked(api);
const en = translator('en');
const ar = translator('ar');

const RUNNING: api.IndexingOverview = {
    batch_id: 'b1',
    active: true,
    total: 7,
    done: 3,
    failed: 0,
    cancelled: 0,
    current_id: 'd4',
    current: 'annual-report.pdf',
    percent: 41,
    seconds_left: 720,
    estimate: 'ready',
    documents: [
        { id: 'd4', filename: 'annual-report.pdf', state: 'running', progress: { phase: 'reading', done: 37, total: 229, estimate: 'ready', seconds_left: 300 } },
    ],
};

const doc = (over: Partial<Document>): Document => ({
    id: 'd1', filename: 'report.pdf', filepath: 'C:/report.pdf', file_size: 1, chunk_count: 0,
    indexed_at: '', last_accessed_at: null, sessions_using: 1, indexing_status: 'complete', ...over,
});

const setOverview = (indexing: api.IndexingOverview | null) => act(() => useChatStore.setState({ indexing }));

beforeEach(() => {
    useChatStore.setState({ indexing: null, showDocLibrary: false, locale: 'en', documents: [] });
    vi.clearAllMocks();
});

afterEach(() => {
    vi.useRealTimers();
});

describe('timeLeftLabel', () => {
    it('shows no number until there is one to show', () => {
        expect(timeLeftLabel(null, 'estimating', en)).toBe('Estimating…');
        expect(timeLeftLabel(undefined, undefined, en)).toBe('Estimating…');
        expect(timeLeftLabel(700, 'estimating', en)).toBe('Estimating…');
    });

    it('says a stall in words and never freezes a number', () => {
        expect(timeLeftLabel(700, 'stalled', en)).toBe('Taking longer than expected');
        expect(timeLeftLabel(null, 'waiting', en)).toBe('Waiting for the model to be free');
    });

    it.each([
        [20, 'Less than a minute left'],
        [59, 'Less than a minute left'],
        [60, 'About a minute left'],
        [720, 'About 12 minutes left'],
        [1190, 'About 20 minutes left'],
        [1600, 'About 25 minutes left'],
        [3600, 'About an hour left'],
        [4800, 'About 1 h 20 min left'],
        [7000, 'About 2 hours left'],
        [20000, 'About 6 hours left'],
    ])('rounds %d seconds away', (seconds, words) => {
        expect(timeLeftLabel(seconds, 'ready', en)).toBe(words);
    });

    it('marks a lower bound as one', () => {
        expect(timeLeftLabel(720, 'at_least', en)).toBe('At least 12 minutes left');
        expect(timeLeftLabel(30, 'at_least', en)).toBe('Estimating…');
    });

    it('keeps Latin digits in Arabic', () => {
        expect(timeLeftLabel(720, 'ready', ar)).toContain('12');
    });
});

describe('statusEventLabel', () => {
    it('reads the backend notices in the chosen language', () => {
        const event = { type: 'status' as const, message: 'English', notice: { kind: 'still_indexing' as const, names: ['a.pdf'] } };
        expect(statusEventLabel(event, en)).toBe("Still indexing, so this reply can't search it yet: a.pdf");
        expect(statusEventLabel(event, ar)).toContain('a.pdf');
        expect(statusEventLabel(event, ar)).toContain('الفهرسة');
    });

    it('turns an index inside a turn into one line', () => {
        const event = {
            type: 'status' as const,
            index: { name: 'a.pdf', phase: 'reading', done: 37, total: 229, estimate: 'ready', seconds_left: 720 },
        };
        expect(statusEventLabel(event, en)).toBe('a.pdf: Reading page 37 of 229 · About 12 minutes left');
    });

    it('leaves other status lines alone', () => {
        expect(statusEventLabel({ type: 'status', message: 'Sending to model...' }, en)).toBe('Sending to model...');
    });

    it('labels the timeline step the same way', () => {
        const step = agentEventToStep(
            { type: 'status', status: 'warning', notice: { kind: 'not_indexed', names: ['c.pdf'] } },
            { current: 0 },
        );
        expect(step?.label).toBe("Not indexed, so this reply can't search it: c.pdf");
    });
});

describe('IndexingIndicator', () => {
    it('keeps saying images are being read while a finished batch is on screen', () => {
        useChatStore.setState({
            indexing: {
                ...RUNNING,
                active: false,
                done: 7,
                images: { documents: 2, names: ['a.pdf', 'b.pdf'], pages_done: 1, pages_total: 9, seconds_left: null, blocked: null },
            } as api.IndexingOverview,
            showDocLibrary: false,
        });
        render(<IndexingIndicator />);
        expect(screen.getByText(en.plural('docs.overall.done', 7, { n: 7 }))).toBeTruthy();
        expect(screen.getByText(en.plural('docs.images.overall', 2, { name: 'a.pdf' }))).toBeTruthy();
    });

    it('stays out of the way from the cold, empty state', () => {
        const { container } = render(<IndexingIndicator />);
        expect(container).toBeEmptyDOMElement();
        setOverview({ active: false, total: 0, documents: [] });
        expect(container).toBeEmptyDOMElement();
    });

    it('shows the batch while the library is closed and opens it', () => {
        render(<IndexingIndicator />);
        setOverview(RUNNING);
        expect(screen.getByText('3 of 7 documents indexed')).toBeInTheDocument();
        expect(screen.getByText('About 12 minutes left')).toBeInTheDocument();
        expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '41');
        fireEvent.click(screen.getByRole('button', { name: /Open Document Library/ }));
        expect(useChatStore.getState().showDocLibrary).toBe(true);
    });

    it('steps aside while the library shows the same thing', () => {
        const { container } = render(<IndexingIndicator />);
        act(() => useChatStore.setState({ indexing: RUNNING, showDocLibrary: true }));
        expect(container).toBeEmptyDOMElement();
    });

    it('keeps a failure until dismissed', async () => {
        vi.useFakeTimers();
        render(<IndexingIndicator />);
        setOverview({ ...RUNNING, active: false, done: 5, failed: 2, seconds_left: null });
        expect(screen.getByText("5 documents indexed · 2 couldn't be indexed")).toBeInTheDocument();
        await act(async () => { vi.advanceTimersByTime(FINISHED_NOTICE_MS * 5); });
        expect(mockedApi.dismissIndexingBatch).not.toHaveBeenCalled();
        await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Dismiss' })); });
        expect(mockedApi.dismissIndexingBatch).toHaveBeenCalledWith('b1');
    });

    it('clears a batch that went well by itself', async () => {
        vi.useFakeTimers();
        render(<IndexingIndicator />);
        setOverview({ ...RUNNING, active: false, done: 7, seconds_left: null });
        await act(async () => { vi.advanceTimersByTime(FINISHED_NOTICE_MS); });
        expect(mockedApi.dismissIndexingBatch).toHaveBeenCalledWith('b1');
    });
});

describe('IndexingSummary', () => {
    it('shows counts, the combined bar, the file in hand and the time left', () => {
        render(<IndexingSummary />);
        setOverview(RUNNING);
        expect(screen.getByText('3 of 7 documents indexed')).toBeInTheDocument();
        expect(screen.getByText('annual-report.pdf')).toBeInTheDocument();
        expect(screen.getByText(/Reading page 37 of 229/)).toBeInTheDocument();
        expect(screen.getByText('About 12 minutes left')).toBeInTheDocument();
        expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '41');
    });

    it('shows a failure while the rest keep going', () => {
        render(<IndexingSummary />);
        setOverview({ ...RUNNING, failed: 1, cancelled: 1 });
        expect(screen.getByText("1 couldn't be indexed · 1 cancelled")).toBeInTheDocument();
    });

    it('names what failed once the batch ends', () => {
        render(<IndexingSummary />);
        setOverview({
            ...RUNNING, active: false, done: 0, total: 1, failed: 1,
            documents: [{ id: 'd1', filename: 'broken.pdf', state: 'failed', error: 'damaged' }],
        });
        expect(screen.getByText('broken.pdf')).toBeInTheDocument();
    });
});

describe('ChatIndexingNotice', () => {
    it('says nothing when every document is ready', () => {
        const { container } = render(<ChatIndexingNotice documents={[doc({})]} />);
        expect(container).toBeEmptyDOMElement();
    });

    it('says a document is still indexing', () => {
        render(<ChatIndexingNotice documents={[doc({ indexing_status: 'indexing' })]} />);
        expect(screen.getByRole('status')).toHaveTextContent('report.pdf is still indexing');
    });

    it('counts several', () => {
        render(<ChatIndexingNotice documents={[doc({ indexing_status: 'indexing' }), doc({ id: 'd2', indexing_status: 'pending' })]} />);
        expect(screen.getByRole('status')).toHaveTextContent("2 of this chat's documents are still indexing");
    });

    it('offers the library for a document that was not indexed', () => {
        render(<ChatIndexingNotice documents={[doc({ indexing_status: 'cancelled' })]} />);
        expect(screen.getByRole('status')).toHaveTextContent("report.pdf wasn't indexed");
        fireEvent.click(screen.getByRole('button', { name: 'Open Document Library' }));
        expect(useChatStore.getState().showDocLibrary).toBe(true);
    });
});

describe('refreshIndexing', () => {
    it('re-reads the library when a document finishes, with the library closed', async () => {
        mockedApi.listDocuments.mockResolvedValue({ documents: [doc({})], total: 1, total_size_bytes: 1, total_chunks: 0 });
        mockedApi.getIndexingOverview.mockResolvedValue({ ...RUNNING, done: 1, batch_id: 'poll-test' });
        await refreshIndexing();
        mockedApi.listDocuments.mockClear();
        await refreshIndexing();
        expect(mockedApi.listDocuments).not.toHaveBeenCalled();

        mockedApi.getIndexingOverview.mockResolvedValue({ ...RUNNING, done: 2, batch_id: 'poll-test' });
        await refreshIndexing();
        expect(mockedApi.listDocuments).toHaveBeenCalledTimes(1);
        expect(useChatStore.getState().documents).toHaveLength(1);
    });

    it('keeps polling through a failed read', async () => {
        mockedApi.getIndexingOverview.mockRejectedValue(new Error('down'));
        await expect(refreshIndexing()).resolves.toBeUndefined();
    });
});
