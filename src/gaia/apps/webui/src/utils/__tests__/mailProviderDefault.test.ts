// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect } from 'vitest';
import { resolveMailProvider } from '../mailProviderDefault';

/**
 * Provider-default logic for issue #1596 — the Agent UI mailbox-provider selector.
 *
 * AC1: exactly one of google/microsoft connected → auto-select it.
 * AC2: both connected (or neither) → fall back to the caller's explicit choice.
 */
describe('resolveMailProvider', () => {
    it('auto-selects google when only google is connected', () => {
        expect(resolveMailProvider(['google'], undefined)).toBe('google');
    });

    it('auto-selects microsoft when only microsoft is connected', () => {
        expect(resolveMailProvider(['microsoft'], undefined)).toBe('microsoft');
    });

    it('returns explicit choice when both providers are connected', () => {
        expect(resolveMailProvider(['google', 'microsoft'], 'microsoft')).toBe('microsoft');
        expect(resolveMailProvider(['google', 'microsoft'], 'google')).toBe('google');
    });

    it('returns explicit choice when neither provider is connected', () => {
        expect(resolveMailProvider([], 'google')).toBe('google');
    });

    it('returns undefined when both connected and no explicit choice provided', () => {
        // No auto-select when ambiguous — caller must show the interactive selector
        expect(resolveMailProvider(['google', 'microsoft'], undefined)).toBeUndefined();
    });

    it('returns undefined when nothing connected and no explicit choice', () => {
        expect(resolveMailProvider([], undefined)).toBeUndefined();
    });

    it('ignores non-mail providers (e.g. github) when determining auto-select', () => {
        // github is connected but is not a mail provider → still auto-selects google
        expect(resolveMailProvider(['google', 'github'], undefined)).toBe('google');
    });
});
