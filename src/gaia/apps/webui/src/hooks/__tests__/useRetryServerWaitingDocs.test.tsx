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

function status(up: boolean): SystemStatus {
    return { lemonade_running: up } as SystemStatus;
}

function listing(docs: Document[]) {
    return { documents: docs, total: docs.length, total_size_bytes: 0, total_chunks: 0 };
}

beforeEach(() => {
    vi.clearAllMocks();
    useChatStore.setState({ systemStatus: status(false), documents: [] });
    mockedApi.reindexDocument.mockResolvedValue({ ...base, indexing_status: 'complete', chunk_count: 3 } as Document);
});

afterEach(() => {
    useChatStore.setState({ systemStatus: null, documents: [] });
});

describe('useRetryServerWaitingDocs', () => {
    it('does nothing while the model server is down', async () => {
        mockedApi.listDocuments.mockResolvedValue(listing([base]));
        render(<Harness />);
        await act(async () => {});
        expect(mockedApi.listDocuments).not.toHaveBeenCalled();
        expect(mockedApi.reindexDocument).not.toHaveBeenCalled();
    });

    it('re-indexes only the server-failed rows when the server comes back', async () => {
        const done = { ...base, indexing_status: 'complete', chunk_count: 3, error_kind: null } as Document;
        mockedApi.listDocuments
            .mockResolvedValueOnce(listing([base, unreadable]))
            .mockResolvedValue(listing([done, unreadable]));
        render(<Harness />);

        act(() => { useChatStore.setState({ systemStatus: status(true) }); });

        await waitFor(() => expect(useChatStore.getState().documents).toEqual([done, unreadable]));
        expect(mockedApi.reindexDocument).toHaveBeenCalledTimes(1);
        expect(mockedApi.reindexDocument).toHaveBeenCalledWith('doc-down');
    });

    it('does not retry again while the server stays up, but does after the next outage', async () => {
        mockedApi.listDocuments.mockResolvedValue(listing([base]));
        mockedApi.reindexDocument.mockRejectedValue(new Error('Reindex failed'));
        render(<Harness />);

        act(() => { useChatStore.setState({ systemStatus: status(true) }); });
        await waitFor(() => expect(mockedApi.reindexDocument).toHaveBeenCalledTimes(1));
        await waitFor(() => expect(mockedApi.listDocuments).toHaveBeenCalledTimes(2));

        act(() => { useChatStore.setState({ systemStatus: { ...status(true), model_loaded: 'x' } as SystemStatus }); });
        await act(async () => {});
        expect(mockedApi.reindexDocument).toHaveBeenCalledTimes(1);

        act(() => { useChatStore.setState({ systemStatus: status(false) }); });
        act(() => { useChatStore.setState({ systemStatus: status(true) }); });
        await waitFor(() => expect(mockedApi.reindexDocument).toHaveBeenCalledTimes(2));
    });
});
