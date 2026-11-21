// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A document that waited on a down model server is indexed once it is back (#8344). */

import { act, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useRetryServerWaitingDocs } from '../useRetryServerWaitingDocs';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import type { Document, SystemStatus } from '../../types';

vi.mock('../../services/api');
const mockedApi = vi.mocked(api);

function Harness() {
    useRetryServerWaitingDocs();
    return null;
}

const base: Document = {
    id: 'doc-down', filename: 'notes.md', filepath: '/tmp/notes.md', file_hash: 'h', file_size: 10,
    chunk_count: 0, indexed_at: '', last_accessed_at: null, sessions_using: 0,
    indexing_status: 'failed', error_kind: 'model_server_down', last_error: 'connection refused',
} as Document;
const unreadable: Document = { ...base, id: 'doc-bad', filename: 'bad.pdf', error_kind: 'corrupt' } as Document;
const busy: Document = { ...base, id: 'doc-busy', filename: 'busy.md', error_kind: 'model_server_busy' } as Document;
const alsoDown: Document = { ...base, id: 'doc-down-2', filename: 'more.md' } as Document;

function status(up: boolean): SystemStatus {
    return { lemonade_running: up } as SystemStatus;
}

function setServer(up: boolean) {
    act(() => { useChatStore.setState({ systemStatus: status(up) }); });
}

/** A stand-in for the documents table: list reads it, reindex writes it. */
let rows: Document[];
let inFlight: number;
let maxInFlight: number;
let reindexOutcome: (id: string) => Promise<'complete' | 'model_server_down'>;

function listing() {
    return { documents: rows.map((d) => ({ ...d })), total: rows.length, total_size_bytes: 0, total_chunks: 0 };
}

beforeEach(() => {
    vi.clearAllMocks();
    rows = [];
    inFlight = 0;
    maxInFlight = 0;
    reindexOutcome = async () => 'complete';
    useChatStore.setState({ systemStatus: status(false), documents: [] });
    mockedApi.listDocuments.mockImplementation(async () => listing());
    mockedApi.reindexDocument.mockImplementation(async (id: string) => {
        inFlight += 1;
        maxInFlight = Math.max(maxInFlight, inFlight);
        rows = rows.map((d) => (d.id === id ? { ...d, indexing_status: 'indexing' } : d));
        try {
            const outcome = await reindexOutcome(id);
            rows = rows.map((d) => (d.id === id
                ? (outcome === 'complete'
                    ? { ...d, indexing_status: 'complete', error_kind: null, chunk_count: 3 }
                    : { ...d, indexing_status: 'failed', error_kind: outcome })
                : d));
            if (outcome !== 'complete') throw new Error('Reindex failed');
            return rows.find((d) => d.id === id)!;
        } finally {
            inFlight -= 1;
        }
    });
});

afterEach(() => {
    useChatStore.setState({ systemStatus: null, documents: [] });
});

function deferred() {
    let resolve!: () => void;
    const promise = new Promise<void>((r) => { resolve = r; });
    return { promise, resolve };
}

describe('useRetryServerWaitingDocs', () => {
    it('does nothing while the model server is down', async () => {
        rows = [base];
        render(<Harness />);
        await act(async () => {});
        expect(mockedApi.listDocuments).not.toHaveBeenCalled();
        expect(mockedApi.reindexDocument).not.toHaveBeenCalled();
    });

    it('re-indexes only the rows waiting for the server when it comes back', async () => {
        rows = [base, unreadable, busy];
        render(<Harness />);
        setServer(true);

        await waitFor(() => expect(useChatStore.getState().documents.find((d) => d.id === 'doc-down')?.indexing_status).toBe('complete'));
        expect(mockedApi.reindexDocument.mock.calls.map((c) => c[0])).toEqual(['doc-down']);
        // The busy row says "Retry in a moment", so it waits for the user's click (#8636).
        expect(useChatStore.getState().documents.find((d) => d.id === 'doc-busy')?.indexing_status).toBe('failed');
        expect(useChatStore.getState().documents.find((d) => d.id === 'doc-bad')?.indexing_status).toBe('failed');
    });

    it('never retries an unreadable file, however many times the server comes back', async () => {
        rows = [unreadable];
        render(<Harness />);
        for (let i = 0; i < 5; i++) {
            setServer(true);
            await act(async () => {});
            setServer(false);
        }
        await waitFor(() => expect(mockedApi.listDocuments).toHaveBeenCalled());
        expect(mockedApi.reindexDocument).not.toHaveBeenCalled();
    });

    it('does not retry again while the server stays up, but does after the next outage', async () => {
        rows = [base];
        reindexOutcome = async () => 'model_server_down';
        render(<Harness />);

        setServer(true);
        await waitFor(() => expect(mockedApi.reindexDocument).toHaveBeenCalledTimes(1));
        await act(async () => {});

        act(() => { useChatStore.setState({ systemStatus: { ...status(true), model_loaded: 'x' } as SystemStatus }); });
        await act(async () => {});
        expect(mockedApi.reindexDocument).toHaveBeenCalledTimes(1);

        setServer(false);
        setServer(true);
        await waitFor(() => expect(mockedApi.reindexDocument).toHaveBeenCalledTimes(2));
    });

    it('skips a document a manual Retry already picked up', async () => {
        rows = [base, { ...alsoDown }];
        const gate = deferred();
        reindexOutcome = async (id) => { if (id === 'doc-down') await gate.promise; return 'complete'; };
        render(<Harness />);
        setServer(true);
        await waitFor(() => expect(mockedApi.reindexDocument).toHaveBeenCalledWith('doc-down'));

        // The user clicks Retry on the second row while the hook is still on the first.
        rows = rows.map((d) => (d.id === 'doc-down-2' ? { ...d, indexing_status: 'indexing' } : d));
        await act(async () => { gate.resolve(); });

        await waitFor(() => expect(useChatStore.getState().documents.find((d) => d.id === 'doc-down')?.indexing_status).toBe('complete'));
        expect(mockedApi.reindexDocument).toHaveBeenCalledTimes(1);
    });

    it('skips a document deleted while it waited its turn', async () => {
        rows = [base, { ...alsoDown }];
        const gate = deferred();
        reindexOutcome = async (id) => { if (id === 'doc-down') await gate.promise; return 'complete'; };
        render(<Harness />);
        setServer(true);
        await waitFor(() => expect(mockedApi.reindexDocument).toHaveBeenCalledTimes(1));

        rows = rows.filter((d) => d.id !== 'doc-down-2');
        await act(async () => { gate.resolve(); });

        await waitFor(() => expect(useChatStore.getState().documents.map((d) => d.id)).toEqual(['doc-down']));
        expect(mockedApi.reindexDocument).toHaveBeenCalledTimes(1);
    });

    it('shows the row as indexing while the hook works on it', async () => {
        rows = [base];
        const gate = deferred();
        reindexOutcome = async () => { await gate.promise; return 'complete'; };
        render(<Harness />);
        setServer(true);

        await waitFor(() => expect(useChatStore.getState().documents[0]?.indexing_status).toBe('indexing'));
        await act(async () => { gate.resolve(); });
        await waitFor(() => expect(useChatStore.getState().documents[0]?.indexing_status).toBe('complete'));
    });

    it('a flapping server never indexes one document twice at once, and the blip is not lost', async () => {
        rows = [base];
        const gate = deferred();
        let first = true;
        reindexOutcome = async () => {
            if (first) { first = false; await gate.promise; return 'model_server_down'; }
            return 'complete';
        };
        render(<Harness />);
        setServer(true);
        await waitFor(() => expect(mockedApi.reindexDocument).toHaveBeenCalledTimes(1));

        // The server drops and returns several times while the first attempt is in flight.
        for (let i = 0; i < 4; i++) {
            setServer(false);
            setServer(true);
        }
        await act(async () => { gate.resolve(); });

        await waitFor(() => expect(useChatStore.getState().documents[0]?.indexing_status).toBe('complete'));
        expect(maxInFlight).toBe(1);
        // One follow-up pass for all the blips, not one per blip.
        expect(mockedApi.reindexDocument).toHaveBeenCalledTimes(2);
    });

    it('drops the follow-up pass when the server is down again by the time the first ends', async () => {
        rows = [base];
        const gate = deferred();
        reindexOutcome = async () => { await gate.promise; return 'model_server_down'; };
        render(<Harness />);
        setServer(true);
        await waitFor(() => expect(mockedApi.reindexDocument).toHaveBeenCalledTimes(1));

        setServer(false);
        setServer(true);
        setServer(false);
        await act(async () => { gate.resolve(); });
        await act(async () => {});

        expect(mockedApi.reindexDocument).toHaveBeenCalledTimes(1);
        expect(useChatStore.getState().documents[0]?.indexing_status).toBe('failed');
    });
});
