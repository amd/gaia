// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { normalizeAddress } from '../address';

describe('normalizeAddress', () => {
    it.each([
        ['example.com', 'https://example.com/'],
        ['  example.com/docs?q=1  ', 'https://example.com/docs?q=1'],
        ['sub.example.co.uk:8443/x', 'https://sub.example.co.uk:8443/x'],
        ['https://example.com', 'https://example.com'],
        ['http://example.com/plain', 'http://example.com/plain'],
        ['localhost:5173', 'http://localhost:5173/'],
        ['localhost', 'http://localhost/'],
        ['app.localhost:3000/settings', 'http://app.localhost:3000/settings'],
        ['127.0.0.1:8000', 'http://127.0.0.1:8000/'],
        ['127.4.5.6', 'http://127.4.5.6/'],
        ['[::1]:4000', 'http://[::1]:4000/'],
        ['192.168.1.20', 'https://192.168.1.20/'],
        ['file:///etc/hosts', 'file:///etc/hosts'],
        ['javascript:alert(1)', 'javascript:alert(1)'],
    ])('%s loads %s', (typed, url) => {
        expect(normalizeAddress(typed)).toBe(url);
    });

    it.each([[''], ['   '], ['two words'], ['intranet'], ['http//broken']])('%j is not a web address', (typed) => {
        expect(normalizeAddress(typed)).toBeNull();
    });
});
