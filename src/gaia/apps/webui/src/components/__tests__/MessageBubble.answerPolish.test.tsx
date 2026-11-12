// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Answer polish: tables align only number columns to the end (#7377),
 * headings step up from body text (#7361), and the footer says when in a
 * few words with the full date on hover (#7387).
 */

import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MessageBubble, formatShortTimestamp } from '../MessageBubble';
import { tEn } from '../../i18n/useT';
import type { Message } from '../../types';
import bubbleCss from '../MessageBubble.css?raw';

vi.mock('../../services/api');

const flat = bubbleCss.replace(/\s+/g, ' ');

function renderAnswer(content: string, created_at = '') {
    const message: Message = { id: 1, session_id: 's', role: 'assistant', content, created_at, rag_sources: null };
    return render(<MessageBubble message={message} />);
}

const columnClasses = (c: HTMLElement, col: number) =>
    Array.from(c.querySelectorAll(`tr > :nth-child(${col})`)).map((cell) => cell.className);

describe('table columns', () => {
    it('aligns a column of numbers to the end and leaves prose at the start', () => {
        const { container } = renderAnswer([
            '| Month | Total | Note |',
            '|---|---|---|',
            '| October 2026 | 47.70 | rent and two lunches |',
            '| November 2026 | $1,236.75 | — |',
            '| **Grand total** | **84.45** | spans both months |',
        ].join('\n'));
        expect(columnClasses(container, 1)).toEqual(['', '', '', '']);
        expect(columnClasses(container, 2)).toEqual(['is-numeric', 'is-numeric', 'is-numeric', 'is-numeric']);
        expect(columnClasses(container, 3)).toEqual(['', '', '', '']);
        expect(flat).not.toMatch(/td:last-child/);
    });

    it('keeps the alignment the markdown asks for', () => {
        const { container } = renderAnswer('| Item | Count |\n|:---:|---:|\n| pens | 3 |');
        const cells = container.querySelectorAll('td');
        expect((cells[0] as HTMLElement).style.textAlign).toBe('center');
        expect((cells[1] as HTMLElement).style.textAlign).toBe('right');
        expect(cells[1].className).toBe('');
    });
});

describe('headings', () => {
    it('step up from the 15 px body text', () => {
        const size = (cls: string) => Number(new RegExp(`\\.${cls} \\{[^}]*font-size: (\\d+)px`).exec(flat)?.[1]);
        expect(size('md-h2')).toBeGreaterThanOrEqual(20);
        expect(size('md-h3')).toBeGreaterThanOrEqual(17);
        expect(size('md-h4')).toBeGreaterThanOrEqual(16);
    });
});

describe('the answer footer', () => {
    const now = new Date(2026, 10, 12, 9, 30);

    it.each([
        [new Date(2026, 10, 12, 8, 5), '8:05 AM'],
        [new Date(2026, 10, 11, 16, 10), 'Yesterday, 4:10 PM'],
        [new Date(2026, 10, 2, 16, 10), 'Nov 2, 4:10 PM'],
        [new Date(2025, 10, 2, 16, 10), 'Nov 2, 2025, 4:10 PM'],
    ])('names %s as "%s"', (when, text) => {
        expect(formatShortTimestamp(when.toISOString(), tEn, now)).toBe(text);
    });

    it('keeps the full date and seconds for the hover', () => {
        const { container } = renderAnswer('Canberra.', '2026-03-14T10:00:05Z');
        const stamp = container.querySelector('.msg-stats-ts')!;
        expect(stamp.getAttribute('title')).toMatch(/2026/);
        expect(stamp.getAttribute('title')).toMatch(/:05/);
        expect(stamp.textContent).not.toMatch(/:05/);
    });
});
