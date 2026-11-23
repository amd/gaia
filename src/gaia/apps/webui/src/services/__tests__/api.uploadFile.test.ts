// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { uploadFile } from '../api';

function fail(status: number, body: string) {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
        new Response(body, { status, headers: { 'content-type': 'application/json' } }),
    );
}

describe('a refused attachment upload', () => {
    beforeEach(() => { vi.stubGlobal('fetch', vi.fn()); });
    afterEach(() => { vi.unstubAllGlobals(); });

    it("says the server's reason in words, not the raw JSON body", async () => {
        fail(400, JSON.stringify({ detail: "File type '.doc' is not allowed." }));
        await expect(uploadFile(new File(['x'], 'report.doc'))).rejects.toHaveProperty(
            'message',
            "File type '.doc' is not allowed.",
        );
    });

    it('keeps the refusal code and its numbers so the composer can translate it', async () => {
        fail(413, JSON.stringify({ detail: { status: 'too_large', message: 'File too large (25.0 MB).', size_mb: '25.0', max_mb: '20' } }));
        const err = await uploadFile(new File(['x'], 'big.txt')).catch((e) => e);
        expect(err.message).toBe('File too large (25.0 MB).');
        expect(err.reason).toBe('too_large');
        expect(err.body).toMatchObject({ size_mb: '25.0', max_mb: '20' });
    });

    it('turns a bare 500 into a plain sentence', async () => {
        fail(500, 'Internal Server Error');
        await expect(uploadFile(new File(['x'], 'a.txt'))).rejects.toHaveProperty(
            'message',
            'Server error. Please try again.',
        );
    });
});
