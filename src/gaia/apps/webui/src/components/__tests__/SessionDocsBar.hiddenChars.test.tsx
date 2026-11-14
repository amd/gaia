// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A document name with a hidden direction character reads in the docs bar as it does on the approval card. */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SessionDocsBar } from '../SessionDocsBar';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import type { Document, Session } from '../../types';

vi.mock('../../services/api');
const mockedApi = vi.mocked(api);

const NAME = 'plan\u202Efdp.md';
const DOC: Document = {
    id: 'd1', filename: NAME, filepath: `C:\\docs\\${NAME}`, file_size: 1, chunk_count: 1,
    indexed_at: '2026-01-01T00:00:00Z', last_accessed_at: null, sessions_using: 1, indexing_status: 'complete',
};

beforeEach(() => {
    vi.clearAllMocks();
    useChatStore.setState({
        sessions: [{ id: 's', title: 'chat', document_ids: ['d1'] } as unknown as Session],
        documents: [DOC],
    });
});

describe('SessionDocsBar', () => {
    it('shows a right-to-left override in a document name as a code', () => {
        const { container } = render(<SessionDocsBar sessionId="s" onDetachFailed={vi.fn()} />);
        const pill = container.querySelector('.doc-pill')!;
        expect(pill.querySelector('.doc-pill-name')!.textContent).toBe('plan[U+202E]fdp.md');
        expect(pill.getAttribute('title')).toBe('C:\\docs\\plan[U+202E]fdp.md');
        expect(screen.getByRole('button', { name: 'Remove plan[U+202E]fdp.md from this chat' })).toBeInTheDocument();
        expect(container.textContent).not.toContain('\u202E');
    });

    it('names the document the same way when removing it fails', async () => {
        mockedApi.detachDocument.mockRejectedValue(new Error('offline'));
        const onDetachFailed = vi.fn();
        render(<SessionDocsBar sessionId="s" onDetachFailed={onDetachFailed} />);
        fireEvent.click(screen.getByRole('button', { name: /^Remove / }));
        await waitFor(() => expect(onDetachFailed).toHaveBeenCalled());
        expect(onDetachFailed.mock.calls[0][0]).toBe('plan[U+202E]fdp.md');
    });
});
