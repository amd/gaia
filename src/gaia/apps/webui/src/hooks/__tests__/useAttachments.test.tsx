// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useAttachments } from '../useAttachments';
import * as api from '../../services/api';
import { useChatStore } from '../../stores/chatStore';

vi.mock('../../services/api', async (importOriginal) => ({
    ...(await importOriginal<typeof import('../../services/api')>()),
    uploadFile: vi.fn(),
}));

function refusal(status: string, httpStatus: number, body: Record<string, unknown>) {
    return new api.ApiError(String(body.message), httpStatus, String(body.message), status, [], { status, ...body });
}

async function attachRefused(name: string, err: unknown): Promise<string | undefined> {
    vi.mocked(api.uploadFile).mockRejectedValue(err);
    const { result } = renderHook(() => useAttachments());
    act(() => result.current.addFiles([new File(['x'], name)]));
    await waitFor(() => expect(result.current.attachments[0].error).toBeTruthy());
    return result.current.attachments[0].error;
}

beforeEach(() => vi.clearAllMocks());
afterEach(() => useChatStore.setState({ locale: 'en' }));

describe('useAttachments upload refusals', () => {
    it('explains a file with no extension in plain words', async () => {
        const msg = await attachRefused('noext', refusal('no_extension', 400, { message: 'server english', extension: '' }));
        expect(msg).toBe("This file has no extension. Add one, such as .txt, and try again.");
    });

    it('names the unsupported extension', async () => {
        const msg = await attachRefused('a.xyz', refusal('unsupported_type', 400, { message: 'server english', extension: '.xyz' }));
        expect(msg).toContain('.xyz');
        expect(msg).not.toContain("''");
    });

    it('says how big the file is and what the limit is', async () => {
        const msg = await attachRefused('big.txt', refusal('too_large', 413, { message: 'server english', size_mb: '25.0', max_mb: '20' }));
        expect(msg).toBe('This file is 25.0 MB. The limit is 20 MB.');
    });

    it('shows refusals in Arabic, not the server English', async () => {
        useChatStore.setState({ locale: 'ar' });
        const empty = await attachRefused('blank.txt', refusal('empty_file', 400, { message: 'Uploaded file is empty' }));
        expect(empty).toContain('فارغ');
        expect(empty).not.toContain('Uploaded file is empty');
    });

    it('keeps the server words for a refusal it has no translation for', async () => {
        const msg = await attachRefused('a.txt', refusal('something_new', 400, { message: 'Brand new refusal' }));
        expect(msg).toBe('Brand new refusal');
    });
});

describe('useAttachments the same file twice', () => {
    it('keeps one chip when the same file is attached again', async () => {
        vi.mocked(api.uploadFile).mockResolvedValue({ url: '/files/notes.txt' } as Awaited<ReturnType<typeof api.uploadFile>>);
        const { result } = renderHook(() => useAttachments());
        const file = new File(['hello'], 'notes.txt', { lastModified: 1000 });
        act(() => result.current.addFiles([file]));
        act(() => result.current.addFiles([new File(['hello'], 'notes.txt', { lastModified: 1000 })]));
        act(() => result.current.addFiles([file, file]));
        await waitFor(() => expect(result.current.hasUploaded).toBe(true));
        expect(result.current.attachments).toHaveLength(1);
        expect(api.uploadFile).toHaveBeenCalledTimes(1);
    });

    it('still adds a different file with the same name', () => {
        vi.mocked(api.uploadFile).mockResolvedValue({ url: '/files/x' } as Awaited<ReturnType<typeof api.uploadFile>>);
        const { result } = renderHook(() => useAttachments());
        act(() => result.current.addFiles([new File(['one'], 'notes.txt', { lastModified: 1 })]));
        act(() => result.current.addFiles([new File(['two!'], 'notes.txt', { lastModified: 2 })]));
        expect(result.current.attachments).toHaveLength(2);
    });
});

describe('useAttachments old Office files', () => {
    it.each([['.doc', '.docx'], ['.xls', '.xlsx'], ['.ppt', '.pptx']])('tells the user to save %s as %s', async (ext, modern) => {
        const msg = await attachRefused(`old${ext}`, refusal('unsupported_type', 400, { message: 'server english', extension: ext }));
        expect(msg).toContain(`save it as ${modern}`);
        expect(msg).not.toContain('Attach an image');
    });
});
