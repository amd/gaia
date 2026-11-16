// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { uploadDocumentBlob } from '../api';

describe('a document refused by the library', () => {
    beforeEach(() => { vi.stubGlobal('fetch', vi.fn()); });
    afterEach(() => { vi.unstubAllGlobals(); });

    it('keeps the refusal code and the size limit so the library can say it plainly', async () => {
        (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(new Response(
            JSON.stringify({ detail: { status: 'too_large', message: 'File too large. Maximum size is 20 MB.', max_mb: '20' } }),
            { status: 413, headers: { 'content-type': 'application/json' } },
        ));
        const err = await uploadDocumentBlob(new File(['x'], 'big.txt')).catch((e) => e);
        expect(err.reason).toBe('too_large');
        expect(err.body).toMatchObject({ max_mb: '20' });
    });
});
