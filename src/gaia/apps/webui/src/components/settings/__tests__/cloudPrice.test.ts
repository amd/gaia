// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { formatCost } from '../cloudPrice';

describe('formatCost', () => {
    it.each([
        [0.002832, '$0.0028'],
        [0.0000012, '$0.0000012'],
        [1.5e-9, '$0.0000000015'],
        [0, '$0.00'],
        [3.456, '$3.46'],
    ])('%s reads %s, never in exponent form', (n, want) => {
        expect(formatCost(n)).toBe(want);
        expect(formatCost(n)).not.toMatch(/e/i);
    });
});
