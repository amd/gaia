// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useConnectionStore } from '@/state/connection';

import { isPcUrl } from './pcUrl';

describe('isPcUrl', () => {
    beforeEach(() => useConnectionStore.getState().setConnection({ baseUrl: 'http://pc.local:4200/', token: null }));
    afterEach(() => useConnectionStore.getState().clearConnection());

    it('is true for the paired PC and for a relative API path, false for an external link', () => {
        expect(isPcUrl('http://PC.local:4200/api/files/image?path=a')).toBe(true);
        expect(isPcUrl('/api/documents/d1/file')).toBe(true);
        expect(isPcUrl('https://example.com/api/files/image')).toBe(false);
        expect(isPcUrl('http://pc.local:4201/x')).toBe(false);
        expect(isPcUrl('//example.com/x')).toBe(false);
        expect(isPcUrl('mailto:a@b.c')).toBe(false);
    });

    it('is false for everything when no PC is paired', () => {
        useConnectionStore.getState().clearConnection();
        expect(isPcUrl('/api/files/image')).toBe(false);
    });
});
