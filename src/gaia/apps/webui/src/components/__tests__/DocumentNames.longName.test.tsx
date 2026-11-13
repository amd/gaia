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

const LONG = 'quarterly_budget_review_for_the_regional_sales_team_final_version_2026.txt';

const DOC: Document = {
    id: 'doc-long', filename: LONG, filepath: `/home/user/${LONG}`, file_size: 2048,
    chunk_count: 3, indexed_at: '2026-11-12T10:00:00', last_accessed_at: null, sessions_using: 0,
    indexing_status: 'complete',
};

const chat = {
    id: 'sess-1', title: 'Budget review', created_at: '', updated_at: '', model: '',
    system_prompt: null, message_count: 1, document_ids: ['doc-long'],
};

beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listDocuments).mockResolvedValue({ documents: [DOC], total: 1, total_size_bytes: 2048, total_chunks: 3 });
    useChatStore.setState({ currentSessionId: 'sess-1', sessions: [chat], documents: [DOC] });
});

it('keeps the file extension visible on a long name in the Document library', async () => {
    render(<DocumentLibrary />);

    const name = await screen.findByTitle(LONG);
    expect(name.textContent).toMatch(/…/);
    expect(name.textContent?.endsWith('.txt')).toBe(true);
});

it('keeps the file extension visible on a long name in the chat documents bar', () => {
    const { container } = render(<SessionDocsBar sessionId="sess-1" onDetachFailed={() => {}} />);

    const name = container.querySelector('.doc-pill-name');
    expect(name?.textContent).toMatch(/…/);
    expect(name?.textContent?.endsWith('.txt')).toBe(true);
});
