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
        // Traversal or a nested path
        '/api/files/uploads/../../secrets.txt',
        '/api/files/uploads/..',
        '/api/files/uploads/sub/x.png',
        '/api/files/uploads/%2e%2e%2fx.png',
        '/api/files/uploads/.hidden',
        '/api/files/uploads/',
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
