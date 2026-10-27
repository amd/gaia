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

    it('turns a bare 500 into a plain sentence', async () => {
        fail(500, 'Internal Server Error');
        await expect(uploadFile(new File(['x'], 'a.txt'))).rejects.toHaveProperty(
            'message',
            'Server error. Please try again.',
        );
    });
});
