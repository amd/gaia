// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// One newline in an answer is a line break, not a space (remarkSoftBreaks).

import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { RenderedContent } from '../MessageBubble';

function breaks(content: string) {
    const { container } = render(<RenderedContent content={content} />);
    return container;
}

describe('line breaks in an answer', () => {
    it('keeps "one item per line" on separate lines', () => {
        const c = breaks('1\n2\n3');
        expect(c.querySelectorAll('p.md-p')).toHaveLength(1);
        expect(c.querySelectorAll('p.md-p br')).toHaveLength(2);
    });

    it('a hard break (two trailing spaces) is one break, not two', () => {
        const c = breaks('Grey skies,  \nPuddles shine,  \nRoots drink');
        expect(c.querySelectorAll('br')).toHaveLength(2);
    });

    it('leaves code blocks and blank-line paragraphs alone', () => {
        const c = breaks('First para.\n\nSecond para.\n\n```\nline a\nline b\n```');
        expect(c.querySelectorAll('p.md-p')).toHaveLength(2);
        expect(c.querySelectorAll('p.md-p br')).toHaveLength(0);
        expect(c.textContent).toContain('line a\nline b');
    });

    it('breaks lines inside a list item', () => {
        const c = breaks('- Name: Ada\n  Role: Engineer');
        expect(c.querySelectorAll('li br')).toHaveLength(1);
    });
});
