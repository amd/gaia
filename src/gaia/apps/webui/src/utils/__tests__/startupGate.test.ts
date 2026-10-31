// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { shouldShowStartupScreen } from '../startupGate';

describe('startup gate', () => {
    it('waits before the first status answer', () => {
        expect(shouldShowStartupScreen(true, false, false)).toBe(true);
    });

    it('keeps a previously shown main UI during transient status loss', () => {
        expect(shouldShowStartupScreen(true, false, true)).toBe(false);
    });

    it('does not show startup once setup is no longer checking', () => {
        expect(shouldShowStartupScreen(false, false, false)).toBe(false);
    });
});