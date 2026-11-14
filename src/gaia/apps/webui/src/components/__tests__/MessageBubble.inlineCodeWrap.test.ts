// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A long unbroken inline code span wraps inside the chat column. With only
 * `overflow-wrap: break-word` (inherited) the span still claims its full width
 * as min-content, so the whole column scrolls sideways.
 */

import { describe, it, expect } from 'vitest';
import bubbleCss from '../MessageBubble.css?raw';

const rule = (selector: string) => {
    const i = bubbleCss.indexOf(`\n${selector} {`);
    expect(i, `${selector} rule missing`).toBeGreaterThanOrEqual(0);
    return bubbleCss.slice(i, bubbleCss.indexOf('}', i));
};

describe('MessageBubble inline code wrapping', () => {
    it('lets a long span break anywhere so it cannot widen the column', () => {
        expect(rule('.inline-code')).toMatch(/overflow-wrap:\s*anywhere/);
    });

    it('repeats the border and padding on every wrapped line', () => {
        const css = rule('.inline-code');
        expect(css).toMatch(/(^|[^-])box-decoration-break:\s*clone/);
        expect(css).toMatch(/-webkit-box-decoration-break:\s*clone/);
    });

    it('does not stop the span from wrapping', () => {
        expect(rule('.inline-code')).not.toMatch(/white-space:\s*(nowrap|pre)\b/);
    });

    it('keeps a short span whole inside a table cell instead of splitting it', () => {
        expect(rule('.md-table .inline-code')).toMatch(/overflow-wrap:\s*normal/);
    });

    it('leaves fenced code blocks scrolling instead of wrapping', () => {
        expect(rule('.code-block pre')).not.toMatch(/overflow-wrap:\s*anywhere/);
    });
});
