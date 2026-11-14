// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { DocumentLibrary } from '../DocumentLibrary';
import { SessionDocsBar } from '../SessionDocsBar';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import type { Document } from '../../types';

vi.mock('../../services/api');

const NAME = 'ملاحظات الاجتماع.txt';

const DOC: Document = {
    id: 'doc-ar', filename: NAME, filepath: `/home/user/${NAME}`, file_size: 2048,
    chunk_count: 3, indexed_at: '2026-11-12T10:00:00', last_accessed_at: null, sessions_using: 0,
    indexing_status: 'complete',
};

const chat = {
    id: 'sess-1', title: 'Meeting', created_at: '', updated_at: '', model: '',
    system_prompt: null, message_count: 1, document_ids: ['doc-ar'],
};

beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listDocuments).mockResolvedValue({ documents: [DOC], total: 1, total_size_bytes: 2048, total_chunks: 3 });
    useChatStore.setState({ currentSessionId: 'sess-1', sessions: [chat], documents: [DOC] });
});

// An RTL-first name in a direction-auto span puts ".txt" in front of the name.
it('reads an Arabic file name left to right with its extension last in the Document library', async () => {
    render(<DocumentLibrary />);

    const name = (await screen.findAllByText(NAME))[0];
    expect(name).toHaveAttribute('dir', 'ltr');
});

it('reads an Arabic file name left to right with its extension last in the chat documents bar', () => {
    const { container } = render(<SessionDocsBar sessionId="sess-1" onDetachFailed={() => {}} />);

    expect(container.querySelector('.doc-pill-name')).toHaveAttribute('dir', 'ltr');
});
