// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SessionDocsBar } from '../SessionDocsBar';
import { useChatStore } from '../../stores/chatStore';
import type { Document } from '../../types';

vi.mock('../../services/api');

const doc = (n: number): Document => ({
    id: `doc-${n}`, filename: `notes-${n}.txt`, filepath: `/docs/notes-${n}.txt`, file_size: 10,
    chunk_count: 1, indexed_at: `2026-11-12T10:0${n}:00Z`, last_accessed_at: null, sessions_using: 1,
    indexing_status: 'complete',
});
const docs = [1, 2, 3, 4, 5].map(doc);
const session = {
    id: 's1', title: 'Chat', created_at: '', updated_at: '', model: '', system_prompt: null,
    message_count: 0, document_ids: docs.map((d) => d.id),
};

function setup(locale: string) {
    useChatStore.setState({ locale, sessions: [session as never], documents: docs });
    render(<SessionDocsBar sessionId="s1" onDetachFailed={() => {}} />);
}

afterEach(() => useChatStore.setState({ locale: 'en', sessions: [], documents: [] }));

describe('the documents bar above the chat', () => {
    it('says how many more and offers show less in English', () => {
        setup('en');
        fireEvent.click(screen.getByRole('button', { name: '+2 more' }));
        expect(screen.getByRole('button', { name: 'show less' })).toBeInTheDocument();
    });

    it('says how many more and offers show less in Arabic', () => {
        setup('ar');
        fireEvent.click(screen.getByRole('button', { name: /\+2 أخرى/ }));
        expect(screen.getByRole('button', { name: /عرض أقل/ })).toBeInTheDocument();
    });

    it('names its manage button and remove buttons in Arabic', () => {
        setup('ar');
        expect(screen.getByRole('button', { name: /إزالة.*notes-5\.txt.*من هذه المحادثة/ })).toBeInTheDocument();
        expect(screen.getByTitle(/إدارة المستندات/)).toBeInTheDocument();
    });
});
