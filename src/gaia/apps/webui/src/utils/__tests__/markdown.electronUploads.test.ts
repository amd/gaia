// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect, vi } from 'vitest';

// The packaged app loads from file://, where the backend is on a loopback port.
vi.mock('../apiBase', () => ({ getApiBase: () => 'http://127.0.0.1:51234/api' }));

import { safeUrlTransform } from '../markdown';

describe('safeUrlTransform — upload links in the desktop app', () => {
    it('points an upload link at the backend, not at file://', () => {
        expect(safeUrlTransform('/api/files/uploads/0b9e7a52-1c2d.png')).toBe(
            'http://127.0.0.1:51234/api/files/uploads/0b9e7a52-1c2d.png',
        );
    });

    it('still neutralises other root-relative paths', () => {
        expect(safeUrlTransform('/Windows/System32/calc.exe')).toBe('');
    });
});
