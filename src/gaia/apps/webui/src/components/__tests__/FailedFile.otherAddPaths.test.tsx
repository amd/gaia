// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A file GAIA couldn't read is not attached by a drop on the chat or by the file browser either (#7417). */

import { render, screen, within, fireEvent, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { FileBrowser } from '../FileBrowser';
import { SessionDocsBar } from '../SessionDocsBar';
import { addDroppedFiles } from '../../utils/chatDocuments';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import type { BrowseResponse, Document } from '../../types';

vi.mock('../../services/api');
vi.mock('../../stores/modelNeededStore', () => ({ ensureModel: vi.fn(async () => true) }));

const mockedApi = vi.mocked(api);

const BROKEN: Document = {
    id: 'doc-broken', filename: 'broken.pdf', filepath: '/home/user/docs/broken.pdf', file_size: 1024,
    chunk_count: 0, indexed_at: '', last_accessed_at: null, sessions_using: 0,
    indexing_status: 'failed', error_kind: 'corrupt', last_error: 'Could not read PDF: broken.pdf',
};

const BROWSE: BrowseResponse = {
    current_path: '/home/user/docs',
    parent_path: '/home/user',
    entries: [{ name: 'broken.pdf', path: '/home/user/docs/broken.pdf', type: 'file', size: 1024, extension: '.pdf', modified: '' }],
    quick_links: [],
};

const chat = {
    id: 'sess-1', title: 'Budget review', created_at: '', updated_at: '', model: '',
    system_prompt: null, message_count: 1, document_ids: [] as string[],
};

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.browseFiles.mockResolvedValue(BROWSE);
    mockedApi.listDocuments.mockResolvedValue({ documents: [BROKEN], total: 1, total_size_bytes: 1024, total_chunks: 0 });
    mockedApi.attachDocument.mockResolvedValue(undefined);
    mockedApi.uploadDocumentBlob.mockResolvedValue(BROKEN);
    mockedApi.uploadDocumentByPath.mockResolvedValue(BROKEN);
    useChatStore.setState({
        currentSessionId: 'sess-1', sessions: [chat], documents: [], draftDocumentIds: [],
        showDocLibrary: false, showFileBrowser: true,
    });
});

describe('a dropped file GAIA could not read', () => {
    it('is reported and not attached to the chat', async () => {
        const failures = await addDroppedFiles('sess-1', [new File(['%PDF'], 'broken.pdf')]);

        expect(failures).toEqual([{ name: 'broken.pdf', stage: 'unreadable', error: 'Could not read PDF: broken.pdf' }]);
        expect(mockedApi.attachDocument).not.toHaveBeenCalled();
        expect(useChatStore.getState().sessions[0].document_ids).toEqual([]);
    });
});

describe('a file the file browser adds that GAIA could not read', () => {
    async function addBroken() {
        render(<FileBrowser onStartChat={vi.fn()} />);
        const row = (await screen.findByText('broken.pdf')).closest('.fb-entry') as HTMLElement;
        fireEvent.click(within(row).getByRole('checkbox'));
        fireEvent.click(screen.getByRole('button', { name: /Add to library/i }));
        await waitFor(() => expect(mockedApi.uploadDocumentByPath).toHaveBeenCalled());
        await screen.findByText(/Could not read PDF: broken\.pdf/);
    }

    it('is not attached to the open chat', async () => {
        await addBroken();

        expect(mockedApi.attachDocument).not.toHaveBeenCalled();
        expect(useChatStore.getState().showFileBrowser).toBe(true);
    });

    it('is not held for the next new chat', async () => {
        useChatStore.setState({ currentSessionId: null, sessions: [] });

        await addBroken();

        expect(useChatStore.getState().draftDocumentIds).toEqual([]);
    });
});

describe('the chat bar holding only a document that failed', () => {
    it('is announced as unreadable, not as zero indexed documents', () => {
        useChatStore.setState({ sessions: [{ ...chat, document_ids: ['doc-broken'] }], documents: [BROKEN] });
        const { container } = render(<SessionDocsBar sessionId="sess-1" onDetachFailed={() => {}} />);

        expect(container.querySelector('.doc-context-bar')).toHaveAttribute('aria-label', "1 couldn't be read");
    });
});
