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

    it.each([
        ['en', 'Not all 150 documents in this chat are searched at once: a chat searches up to 100 documents. Ask about a file by name to search it.'],
        ['ar', 'لا يُبحث في كل مستندات هذه المحادثة البالغ عددها 150 في المرة الواحدة'],
    ])('says when a chat has more documents than it searches at once (%s)', (locale, text) => {
        const many = Array.from({ length: 150 }, (_, i) => doc(i + 10));
        useChatStore.setState({ locale, sessions: [{ ...session, document_ids: many.map((d) => d.id) } as never], documents: many });
        render(<SessionDocsBar sessionId="s1" onDetachFailed={() => {}} />);
        expect(screen.getByRole('status').textContent).toContain(text);
    });

    it.each([
        ['en', 'Not all 9 documents in this chat are searched at once: they are long, and a chat searches up to 10,000 passages. Ask about a file by name to search it.'],
        ['ar', 'فهي طويلة، وتبحث المحادثة في'],
    ])('names the passage limit for a few long documents, never the document count (%s)', (locale, text) => {
        const books = Array.from({ length: 9 }, (_, i) => ({ ...doc(i + 10), chunk_count: 1200 }));
        useChatStore.setState({ locale, sessions: [{ ...session, document_ids: books.map((d) => d.id) } as never], documents: books });
        render(<SessionDocsBar sessionId="s1" onDetachFailed={() => {}} />);
        const notice = screen.getByRole('status').textContent;
        expect(notice).toContain(text);
        expect(notice).not.toContain('100');
    });

    it('names the limit the chat reaches first, in the order its documents load', () => {
        // 100 small documents fill the count first; the long one after them never loads.
        const small = Array.from({ length: 100 }, (_, i) => doc(i + 10));
        const long = { ...doc(200), chunk_count: 20000 };
        useChatStore.setState({ sessions: [{ ...session, document_ids: [...small, long].map((d) => d.id) } as never], documents: [long, ...small] });
        render(<SessionDocsBar sessionId="s1" onDetachFailed={() => {}} />);
        expect(screen.getByRole('status').textContent).toContain('a chat searches up to 100 documents.');
    });

    it('says nothing about a limit for one document longer than the passage limit', () => {
        const book = { ...doc(10), chunk_count: 12000 };
        useChatStore.setState({ sessions: [{ ...session, document_ids: [book.id] } as never], documents: [book] });
        render(<SessionDocsBar sessionId="s1" onDetachFailed={() => {}} />);
        expect(screen.queryByRole('status')).toBeNull();
    });

    it('says nothing about a limit for documents that fit in it exactly', () => {
        const books = Array.from({ length: 8 }, (_, i) => ({ ...doc(i + 10), chunk_count: 1250 }));
        useChatStore.setState({ sessions: [{ ...session, document_ids: books.map((d) => d.id) } as never], documents: books });
        render(<SessionDocsBar sessionId="s1" onDetachFailed={() => {}} />);
        expect(screen.queryByRole('status')).toBeNull();
    });

    it('says nothing about a limit at 100 documents', () => {
        const hundred = Array.from({ length: 100 }, (_, i) => doc(i + 10));
        useChatStore.setState({ sessions: [{ ...session, document_ids: hundred.map((d) => d.id) } as never], documents: hundred });
        render(<SessionDocsBar sessionId="s1" onDetachFailed={() => {}} />);
        expect(screen.queryByRole('status')).toBeNull();
    });

    it('names its manage button and remove buttons in Arabic', () => {
        setup('ar');
        expect(screen.getByRole('button', { name: /إزالة.*notes-5\.txt.*من هذه المحادثة/ })).toBeInTheDocument();
        expect(screen.getByTitle(/إدارة المستندات/)).toBeInTheDocument();
    });
});
