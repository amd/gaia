// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The remove (x) on a chat's document pill stays in the tab order. A button with
 * display:none is skipped by Tab, so keyboard users could never reach it.
 */

import { describe, it, expect } from 'vitest';
import chatCss from '../ChatView.css?raw';

const flat = chatCss.replace(/\s+/g, ' ');

describe('document pill remove button', () => {
    it('is never display:none, which would take it out of the tab order', () => {
        const rule = flat.match(/\.doc-pill-remove \{([^}]*)\}/)?.[1] ?? '';
        expect(rule).not.toBe('');
        expect(rule).not.toMatch(/display:\s*none/);
    });

    it('fades in on hover and when focus is inside the pill', () => {
        expect(flat).toMatch(/\.doc-pill:hover \.doc-pill-remove, \.doc-pill:focus-within \.doc-pill-remove \{ opacity: 1; \}/);
    });

    it('is always visible where there is no hover', () => {
        expect(flat).toMatch(/@media \(hover: none\) \{ \.doc-pill-remove \{ opacity: 1; \} \}/);
    });
});
