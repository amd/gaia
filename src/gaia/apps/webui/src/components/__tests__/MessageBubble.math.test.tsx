// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Formulas in an answer are drawn, not shown as LaTeX source (#7359), while
 * dollar amounts and code stay exactly as written.
 */

import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MessageBubble } from '../MessageBubble';
import { normalizeMathDelimiters } from '../../utils/mathDelimiters';
import type { Message } from '../../types';

vi.mock('../../services/api');

function renderAnswer(content: string) {
    const message: Message = { id: 1, session_id: 's', role: 'assistant', content, created_at: '', rag_sources: null };
    return render(<MessageBubble message={message} />);
}

const formulas = (c: HTMLElement) => Array.from(c.querySelectorAll('.md-math'));

describe('maths in an answer', () => {
    it('draws a display formula and an inline one', () => {
        const { container } = renderAnswer(
            'The ratio is\n\n$$R = \\frac{C_{\\text{row}}}{C_{\\text{col}}} \\approx \\frac{W}{w}$$\n\nso $R \\approx 12.5$ here.',
        );
        const shown = formulas(container);
        expect(shown).toHaveLength(2);
        expect(shown[0].classList.contains('md-math-display')).toBe(true);
        expect(shown[0].querySelector('math mfrac')).not.toBeNull();
        expect(shown[1].querySelector('math')).not.toBeNull();
        expect(container.textContent).not.toContain('\\frac');
        expect(container.textContent).not.toContain('$');
    });

    it('reads \\( \\) and \\[ \\] too', () => {
        const { container } = renderAnswer('Inline \\(x^2\\) and\n\n\\[\ny = mx + b\n\\]');
        expect(formulas(container)).toHaveLength(2);
    });

    it('leaves money alone', () => {
        const { container } = renderAnswer('It costs $5 and $10, or $3.50 each.');
        expect(formulas(container)).toHaveLength(0);
        expect(container.textContent).toContain('It costs $5 and $10, or $3.50 each.');
    });

    it('leaves code alone', () => {
        const { container } = renderAnswer('Run `echo $HOME_{x}$` then\n\n```sh\nprice=$((a^b))\necho \\(x\\)\n```');
        expect(formulas(container)).toHaveLength(0);
        expect(container.querySelector('.code-block code')?.textContent).toBe('price=$((a^b))\necho \\(x\\)');
    });

    it('shows a formula that does not parse as its source, not nothing', () => {
        const { container } = renderAnswer('Broken: $$\\frac{1}{$$');
        expect(formulas(container)).toHaveLength(1);
        expect(container.textContent).toContain('\\frac{1}{');
    });
});

describe('normalizeMathDelimiters', () => {
    it.each([
        ['$x$ alone', '$x$ alone'],
        ['so $a_1 + b$ holds', 'so $$a_1 + b$$ holds'],
        ['escaped \\$x^2$ stays', 'escaped \\$x^2$ stays'],
        ['\\[E = mc^2\\]', '$$\nE = mc^2\n$$'],
    ])('%s', (input, output) => {
        expect(normalizeMathDelimiters(input)).toBe(output);
    });
});
