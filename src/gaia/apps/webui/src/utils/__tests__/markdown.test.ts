// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect } from 'vitest';
import { safeUrlTransform } from '../markdown';

describe('safeUrlTransform', () => {
    it.each([
        'https://amd-gaia.ai/docs/guides/chat',
        'http://localhost:4200',
        'HTTPS://AMD-GAIA.AI',
        'mailto:someone@example.com',
        '#section-2',
    ])('keeps %s', (url) => {
        expect(safeUrlTransform(url)).toBe(url);
    });

    it.each([
        // Root-relative — resolves to file:///C:/Windows/System32/calc.exe
        '/Windows/System32/calc.exe',
        // Protocol-relative / UNC — resolves to an SMB fetch
        '//attacker/share/x.exe',
        '\\\\attacker\\share\\x.exe',
        // Plain relative — resolves next to the file:// index.html
        'x.exe',
        '../../x.exe',
        'C:/Windows/System32/calc.exe',
        'file:///C:/Windows/System32/calc.exe',
        'javascript:alert(1)',
        'data:text/html,<b>x</b>',
        'vbscript:msgbox(1)',
        '',
    ])('neutralises %s', (url) => {
        expect(safeUrlTransform(url)).toBe('');
    });
});

describe('safeUrlTransform — chat upload links', () => {
    it('keeps an upload link same-origin when the backend serves the app', () => {
        expect(safeUrlTransform('/api/files/uploads/0b9e7a52-1c2d.png')).toBe(
            '/api/files/uploads/0b9e7a52-1c2d.png',
        );
    });

    it.each([
        '/api/files/uploads/Q3%20plan%20%28final%29.pdf',
        '/api/files/uploads/%D8%AA%D9%82%D8%B1%D9%8A%D8%B1%20%D8%A7%D9%84%D8%A7%D8%AC%D8%AA%D9%85%D8%A7%D8%B9.pdf',
        '/api/files/uploads/report%231%20%26%20notes.pdf',
        '/api/files/uploads/x%2Ey.png',
    ])('keeps the encoded name in %s', (url) => {
        expect(safeUrlTransform(url)).toBe(url);
    });

    it.each([
        // Traversal or a nested path, raw or percent-encoded
        '/api/files/uploads/../../secrets.txt',
        '/api/files/uploads/..',
        '/api/files/uploads/sub/x.png',
        '/api/files/uploads/%2e%2e%2fx.png',
        '/api/files/uploads/%2Fetc%2Fpasswd',
        '/api/files/uploads/x%5Cevil',
        '/api/files/uploads/x%00.png',
        '/api/files/uploads/%2Ehidden.png',
        '/api/files/uploads/.hidden',
        '/api/files/uploads/',
        '/api/files/uploads/%',
        '/api/files/uploads/x%2.png',
        '/api/files/uploads/x%zz.png',
        // Raw characters that belong percent-encoded
        '/api/files/uploads/Q3 plan.pdf',
        '/api/files/uploads/تقرير.pdf',
        // Extra parts after the file name
        '/api/files/uploads/x.png?y=1',
        '/api/files/uploads/x.png#y',
        '/api/files/uploads/x.png/',
        // Other app routes and look-alikes
        '/api/files/x.png',
        '/api/sessions',
        '//api/files/uploads/x.png',
        '/API/files/uploads/x.png',
        'api/files/uploads/x.png',
        ' /api/files/uploads/x.png',
        '/api/files/uploads/x.png\\evil',
    ])('still neutralises %s', (url) => {
        expect(safeUrlTransform(url)).toBe('');
    });
});
