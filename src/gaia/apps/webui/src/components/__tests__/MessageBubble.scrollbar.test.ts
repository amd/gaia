// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Wide answer content scrolls sideways inside its own box. The page-wide
 * scrollbar thumb is the same colour as a table's border, so without its own
 * scrollbar colour a clipped table gives no sign that more columns exist.
 */

import { describe, it, expect } from 'vitest';
import bubbleCss from '../MessageBubble.css?raw';

const rule = (selector: string) => {
    const i = bubbleCss.indexOf(`${selector} {`);
    expect(i, `${selector} rule missing`).toBeGreaterThanOrEqual(0);
    return bubbleCss.slice(i, bubbleCss.indexOf('}', i));
};

describe('MessageBubble horizontal scroll cue', () => {
    it.each(['.md-table-wrap', '.code-block pre'])('%s shows a visible thin scrollbar', (selector) => {
        const css = rule(selector);
        expect(css).toMatch(/overflow-x:\s*auto/);
        expect(css).toMatch(/scrollbar-width:\s*thin/);
        expect(css).toMatch(/scrollbar-color:\s*var\(--text[\w-]*\)\s+transparent/);
    });
});
