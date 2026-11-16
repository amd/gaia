// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { REMOVE_UNDO_MS } from '@webui/utils/documentLibrary.core';

import type { DocumentListResponse } from '@/api/client.types';
import { createFakeClient } from '@/api/fake';
import { GaiaApiError, GaiaError, GaiaNetworkError } from '@/api/errors';
import { GaiaClientProvider } from '@/data';
import { pickDocuments } from '@/platform/pickers';
import { ThemeProvider } from '@/ui';

import { DocumentsScreen } from './DocumentsScreen';
import { useNextChatDocs } from './nextChatDocs';

jest.mock('@/features/nav/ScreenHeader', () => ({ ScreenHeader: () => null }));
jest.mock('@/platform/pickers', () => ({ pickDocuments: jest.fn(), pickPhotos: jest.fn() }));

const doc = (id: string, filename: string, status: string, size: number) => ({
    id,
    filename,
    filepath: `/docs/${filename}`,
    file_size: size,
    chunk_count: 3,
    indexed_at: new Date().toISOString(),
    indexing_status: status,
});

async function mount(client: ReturnType<typeof createFakeClient>, chatId?: string) {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrap = ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={queryClient}>
            <GaiaClientProvider client={client}>
                <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 800 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}>
                    <ThemeProvider>{children}</ThemeProvider>
                </SafeAreaProvider>
            </GaiaClientProvider>
        </QueryClientProvider>
    );
    await render(<DocumentsScreen chatId={chatId} />, { wrapper: wrap });
}

describe('DocumentsScreen', () => {
    it('lists documents with a count and total size, and no chooser without a chat', async () => {
        const documents = [doc('a', 'notes.txt', 'complete', 2048), doc('b', 'bad.pdf', 'failed', 1024)];
        await mount(createFakeClient({ documents: { documents, total: 2, total_chunks: 6, total_size_bytes: 3072 } as DocumentListResponse }));
        expect(await screen.findByText(/notes\.txt/)).toBeTruthy();
        expect(screen.getByText(/^\u2066?1\u2069? document$/)).toBeTruthy();
        expect(screen.getByText(/couldn't be read/)).toBeTruthy();
        expect(screen.getByText(/3 KB/)).toBeTruthy();
        expect(screen.getByText(/go to your next new chat/)).toBeTruthy();
    });

    it('says so when the library is empty', async () => {
        await mount(createFakeClient());
        expect(await screen.findByText('No documents yet')).toBeTruthy();
    });

    it('shows a load failure with Try again, which reads the library again', async () => {
        const client = createFakeClient();
        const listDocuments = jest.fn().mockRejectedValueOnce(new Error('backend is starting')).mockResolvedValue({ documents: [], total: 0, total_chunks: 0, total_size_bytes: 0 });
        client.listDocuments = listDocuments;
        await mount(client);
        expect(await screen.findByText(/Couldn't load your documents: backend is starting/)).toBeTruthy();
        fireEvent.press(screen.getByText('Try again'));
        expect(await screen.findByText('No documents yet')).toBeTruthy();
        expect(listDocuments).toHaveBeenCalledTimes(2);
    });

    describe('adding a file from the phone', () => {
        const file = (name: string) => ({ uri: `file:///cache/${name}`, name, mimeType: null, size: 10 });
        const chat = { id: 'c1', title: 'A chat', created_at: '2026-11-14T00:00:00Z', updated_at: '2026-11-14T00:00:00Z', model: '', system_prompt: null, message_count: 1, document_ids: [] };

        it('uploads it and attaches it to the chat the library was opened from', async () => {
            (pickDocuments as jest.Mock).mockResolvedValue([file('notes.txt')]);
            const client = createFakeClient({ sessions: [chat] });
            await mount(client, 'c1');
            fireEvent.press(await screen.findByText('Add a file'));
            expect(await screen.findByText(/Added notes\.txt to/)).toBeTruthy();
            expect(client.uploaded.map((f) => f.name)).toEqual(['notes.txt']);
            expect(client.attached).toEqual([{ sessionId: 'c1', docId: 'doc-1' }]);
            await waitFor(() => expect(screen.getAllByText(/notes\.txt/).length).toBeGreaterThan(1));
        });

        it('shows the category help when GAIA refuses the file', async () => {
            (pickDocuments as jest.Mock).mockResolvedValue([file('big.pdf')]);
            const documentUploadError = new GaiaApiError(400, 'too big', undefined, undefined, { status: 'too_large', max_mb: 20 });
            await mount(createFakeClient({ documentUploadError }));
            fireEvent.press(await screen.findByText('Add a file'));
            expect(await screen.findByText('"big.pdf" is too large')).toBeTruthy();
            expect(screen.getByText(/up to 20 MB/)).toBeTruthy();
        });

        it('shows a warning, and no spinner, when the paired connection cannot send files yet', async () => {
            (pickDocuments as jest.Mock).mockResolvedValue([file('notes.txt')]);
            const refused = Object.assign(new GaiaError('mobileApp.pairing.uploadUnsupported'), { code: 'unsupported_body' });
            const documentUploadError = new GaiaNetworkError('http://pc', refused);
            await mount(createFakeClient({ documentUploadError }));
            fireEvent.press(await screen.findByText('Add a file'));
            expect(await screen.findByText(/can't send files to your PC yet/)).toBeTruthy();
            expect(screen.queryByText(/Indexing notes\.txt/)).toBeNull();
        });

        it('rules out an unsupported type before sending it', async () => {
            (pickDocuments as jest.Mock).mockResolvedValue([file('clip.mp4')]);
            const client = createFakeClient();
            await mount(client);
            fireEvent.press(await screen.findByText('Add a file'));
            expect(await screen.findByText('Video files not supported')).toBeTruthy();
            expect(client.uploaded).toEqual([]);
        });
    });

    describe('remove with Undo', () => {
        const library = () => ({ documents: [doc('a', 'notes.txt', 'complete', 2048)], total: 1, total_chunks: 3, total_size_bytes: 2048 }) as DocumentListResponse;
        beforeEach(() => jest.useFakeTimers());
        afterEach(() => jest.useRealTimers());

        it('Undo puts the row back and the document is never deleted', async () => {
            const client = createFakeClient({ documents: library() });
            await mount(client);
            await fireEvent.press(await screen.findByLabelText('Remove notes.txt'));
            expect(screen.queryByLabelText('Remove notes.txt')).toBeNull();
            await fireEvent.press(screen.getByText('Undo'));
            await act(() => jest.advanceTimersByTimeAsync(REMOVE_UNDO_MS * 2));
            expect(screen.getByLabelText('Remove notes.txt')).toBeTruthy();
            expect(client.documentActions).toEqual([]);
        });

        it('without Undo the DELETE goes out once the window ends, not before', async () => {
            const client = createFakeClient({ documents: library() });
            await mount(client);
            await fireEvent.press(await screen.findByLabelText('Remove notes.txt'));
            await act(() => jest.advanceTimersByTimeAsync(REMOVE_UNDO_MS - 1));
            expect(client.documentActions).toEqual([]);
            await act(() => jest.advanceTimersByTimeAsync(1));
            expect(client.documentActions).toEqual([{ action: 'delete', docId: 'a' }]);
        });

        it('a failed DELETE brings the row back with the reason', async () => {
            const client = createFakeClient({ documents: library(), documentFailures: { delete: 'disk is read-only' } });
            await mount(client);
            await fireEvent.press(await screen.findByLabelText('Remove notes.txt'));
            await act(() => jest.advanceTimersByTimeAsync(REMOVE_UNDO_MS));
            expect(await screen.findByText(/Couldn't remove notes\.txt: .*disk is read-only/)).toBeTruthy();
            expect(screen.getByLabelText('Remove notes.txt')).toBeTruthy();
        });
    });

    describe('attaching a library document', () => {
        const library = () => ({ documents: [doc('a', 'notes.txt', 'complete', 2048), doc('b', 'bad.pdf', 'failed', 1024)], total: 2, total_chunks: 6, total_size_bytes: 3072 }) as DocumentListResponse;
        const chat = { id: 'c1', title: 'A chat', created_at: '2026-11-14T00:00:00Z', updated_at: '2026-11-14T00:00:00Z', model: '', system_prompt: null, message_count: 1, document_ids: ['b'] };
        beforeEach(() => useNextChatDocs.getState().clear());

        it('holds it for the next chat in the store New chat reads, and offers nothing for an unreadable document', async () => {
            await mount(createFakeClient({ documents: library() }));
            expect(screen.queryByLabelText('Add bad.pdf to your next chat')).toBeNull();
            fireEvent.press(await screen.findByLabelText('Add notes.txt to your next chat'));
            expect(await screen.findByText(/notes\.txt will be added to your next new chat/)).toBeTruthy();
            expect(useNextChatDocs.getState().ids).toEqual(['a']);
            expect(screen.queryByLabelText('Add notes.txt to your next chat')).toBeNull();
        });

        it('attaches it to the chat the library was opened from', async () => {
            const client = createFakeClient({ documents: library(), sessions: [chat] });
            await mount(client, 'c1');
            fireEvent.press(await screen.findByLabelText(/Add notes\.txt to/));
            expect(await screen.findByText(/Added notes\.txt to/)).toBeTruthy();
            expect(client.attached).toEqual([{ sessionId: 'c1', docId: 'a' }]);
            expect(useNextChatDocs.getState().ids).toEqual([]);
        });
    });
});
