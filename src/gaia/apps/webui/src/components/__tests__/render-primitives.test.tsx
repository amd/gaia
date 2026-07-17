// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Contract tests for the five generic render primitives added in issue
 * #2108, Increment 2: `table`, `key_value`, `list`, `image`, `diff`.
 *
 * None of these render keys are registered yet — `CARD_REGISTRY` (see
 * ../render/registry.tsx) only knows `email_pre_scan` as of Increment 1.
 * Every assertion below that expects real card output is EXPECTED TO FAIL
 * right now: RenderCard falls back to UnsupportedCard, so e.g.
 * `screen.getByRole('table')` will not find anything. That is correct TDD —
 * this file pins the frozen contract the Increment 2 implementer must
 * conform to; do not loosen these assertions to match a different shape
 * without confirming it against the plan in issue #2108.
 *
 * Only test #18 (oversized payload notice) is expected to pass today — it
 * exercises UnsupportedCard behavior that already shipped in Increment 1.
 *
 * Everything is driven through the public `<RenderCard render data />`
 * surface; the primitive components' file/module layout is the
 * implementer's choice and is deliberately not imported here.
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { RenderCard } from '../render/RenderCard';

describe('table primitive', () => {
    it('renders a real table with headers and cell text, wrapped in .render-table', () => {
        const { container } = render(
            <RenderCard render="table" data={{ columns: ['A', 'B'], rows: [[1, 'x']] }} />,
        );

        expect(screen.getByRole('table')).toBeInTheDocument();
        expect(screen.getByText('A')).toBeInTheDocument();
        expect(screen.getByText('B')).toBeInTheDocument();
        expect(screen.getByText('1')).toBeInTheDocument();
        expect(screen.getByText('x')).toBeInTheDocument();
        expect(container.querySelector('.render-table')).not.toBeNull();
    });

    it('renders an optional title', () => {
        render(<RenderCard render="table" data={{ title: 'My Table', columns: ['A'], rows: [['v']] }} />);

        expect(screen.getByText('My Table')).toBeInTheDocument();
    });

    it('caps rows at 500 and shows a truncation notice', () => {
        const rows = Array.from({ length: 501 }, (_, i) => [`row-${i}`]);
        render(<RenderCard render="table" data={{ columns: ['Name'], rows }} />);

        expect(screen.getByText('row-499')).toBeInTheDocument();
        expect(screen.queryByText('row-500')).not.toBeInTheDocument();
        expect(screen.getByText('+1 more (truncated)')).toBeInTheDocument();
    });

    it('renders the invalid-payload fallback for a malformed payload', () => {
        render(<RenderCard render="table" data={{ columns: 'nope' }} />);

        expect(screen.getByText('Invalid table payload')).toBeInTheDocument();
    });

    it('caps columns at 500 and appends one truncation header cell', () => {
        const columns = Array.from({ length: 501 }, (_, i) => `col-${i}`);
        const { container } = render(<RenderCard render="table" data={{ columns, rows: [] }} />);

        const headers = container.querySelectorAll('thead th');
        expect(headers).toHaveLength(501);
        expect(screen.getByText('col-499')).toBeInTheDocument();
        expect(screen.queryByText('col-500')).not.toBeInTheDocument();
        expect(screen.getByText('+1 more (truncated)')).toBeInTheDocument();
    });
});

describe('key_value primitive', () => {
    it('renders each item key and value as visible text', () => {
        render(<RenderCard render="key_value" data={{ items: [{ key: 'Status', value: 'ok' }] }} />);

        expect(screen.getByText('Status')).toBeInTheDocument();
        expect(screen.getByText('ok')).toBeInTheDocument();
    });

    it('renders the invalid-payload fallback for a malformed payload', () => {
        render(<RenderCard render="key_value" data={{ items: [{ wrong: true }] }} />);

        expect(screen.getByText('Invalid key_value payload')).toBeInTheDocument();
    });
});

describe('list primitive', () => {
    it('renders an <ol> with exactly the given <li> items when ordered', () => {
        const { container } = render(
            <RenderCard render="list" data={{ ordered: true, items: ['first', 'second'] }} />,
        );

        const ol = container.querySelector('ol');
        expect(ol).not.toBeNull();
        expect(ol?.querySelectorAll('li')).toHaveLength(2);
        expect(screen.getByText('first')).toBeInTheDocument();
        expect(screen.getByText('second')).toBeInTheDocument();
    });

    it('renders a <ul> (not <ol>) when ordered is omitted', () => {
        const { container } = render(<RenderCard render="list" data={{ items: ['only'] }} />);

        expect(container.querySelector('ul')).not.toBeNull();
        expect(container.querySelector('ol')).toBeNull();
    });

    it('caps items at 500 and shows a truncation notice', () => {
        const items = Array.from({ length: 501 }, (_, i) => `item-${i}`);
        render(<RenderCard render="list" data={{ items }} />);

        expect(screen.getByText('item-499')).toBeInTheDocument();
        expect(screen.queryByText('item-500')).not.toBeInTheDocument();
        expect(screen.getByText('+1 more (truncated)')).toBeInTheDocument();
    });

    it('renders the invalid-payload fallback for a malformed payload', () => {
        render(<RenderCard render="list" data={{ items: 'nope' }} />);

        expect(screen.getByText('Invalid list payload')).toBeInTheDocument();
    });
});

describe('image primitive', () => {
    it('renders an <img> with the exact src and alt attributes for a valid base64 PNG', () => {
        const { container } = render(
            <RenderCard render="image" data={{ src: 'data:image/png;base64,iVBORw0KGgo=', alt: 'chart' }} />,
        );

        const img = container.querySelector('img');
        expect(img).not.toBeNull();
        expect(img?.getAttribute('src')).toBe('data:image/png;base64,iVBORw0KGgo=');
        expect(img?.getAttribute('alt')).toBe('chart');
    });

    it('rejects a remote https:// URL — no <img>, invalid-payload fallback', () => {
        const { container } = render(<RenderCard render="image" data={{ src: 'https://example.com/x.png' }} />);

        expect(container.querySelector('img')).toBeNull();
        expect(screen.getByText('Invalid image payload')).toBeInTheDocument();
    });

    it('rejects data:image/svg+xml (deliberately excluded) — no <img>, invalid-payload fallback', () => {
        const { container } = render(
            <RenderCard render="image" data={{ src: 'data:image/svg+xml;base64,PHN2Zz4=' }} />,
        );

        expect(container.querySelector('img')).toBeNull();
        expect(screen.getByText('Invalid image payload')).toBeInTheDocument();
    });

    it('rejects a javascript: scheme — no <img>, invalid-payload fallback', () => {
        const { container } = render(<RenderCard render="image" data={{ src: 'javascript:alert(1)' }} />);

        expect(container.querySelector('img')).toBeNull();
        expect(screen.getByText('Invalid image payload')).toBeInTheDocument();
    });

    it('renders an optional caption as visible text', () => {
        render(
            <RenderCard
                render="image"
                data={{ src: 'data:image/png;base64,iVBORw0KGgo=', caption: 'Q3 numbers' }}
            />,
        );

        expect(screen.getByText('Q3 numbers')).toBeInTheDocument();
    });
});

describe('diff primitive', () => {
    it('applies per-line classes by +/-/@@ prefix, with plain context lines carrying only the base class', () => {
        const unified = '@@ -1,2 +1,2 @@\n context\n-old line\n+new line';
        const { container } = render(<RenderCard render="diff" data={{ unified }} />);

        const lines = Array.from(container.querySelectorAll('.render-diff__line'));
        expect(lines.length).toBeGreaterThan(0);

        const hunkLine = lines.find((el) => el.textContent?.includes('@@ -1,2 +1,2 @@'));
        const removedLine = lines.find((el) => el.textContent?.includes('old line'));
        const addedLine = lines.find((el) => el.textContent?.includes('new line'));
        const contextLine = lines.find(
            (el) => el.textContent?.includes('context') && !el.textContent?.includes('old line') && !el.textContent?.includes('new line'),
        );

        expect(hunkLine).toBeTruthy();
        expect(hunkLine?.classList.contains('render-diff__line--hunk')).toBe(true);

        expect(removedLine).toBeTruthy();
        expect(removedLine?.classList.contains('render-diff__line')).toBe(true);
        expect(removedLine?.classList.contains('render-diff__line--removed')).toBe(true);

        expect(addedLine).toBeTruthy();
        expect(addedLine?.classList.contains('render-diff__line--added')).toBe(true);

        expect(contextLine).toBeTruthy();
        expect(contextLine?.classList.contains('render-diff__line')).toBe(true);
        expect(contextLine?.classList.contains('render-diff__line--added')).toBe(false);
        expect(contextLine?.classList.contains('render-diff__line--removed')).toBe(false);
        expect(contextLine?.classList.contains('render-diff__line--hunk')).toBe(false);
    });

    it('renders the invalid-payload fallback for a malformed payload', () => {
        render(<RenderCard render="diff" data={{ unified: 42 }} />);

        expect(screen.getByText('Invalid diff payload')).toBeInTheDocument();
    });

    it('styles unified-diff file header lines as neutral, not added/removed', () => {
        const unified = '--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new';
        const { container } = render(<RenderCard render="diff" data={{ unified }} />);

        const lines = Array.from(container.querySelectorAll('.render-diff__line'));

        const minusHeader = lines.find((el) => el.textContent === '--- a/x');
        const plusHeader = lines.find((el) => el.textContent === '+++ b/x');
        const removedLine = lines.find((el) => el.textContent === '-old');
        const addedLine = lines.find((el) => el.textContent === '+new');

        expect(minusHeader).toBeTruthy();
        expect(minusHeader?.className).toBe('render-diff__line');
        expect(plusHeader).toBeTruthy();
        expect(plusHeader?.className).toBe('render-diff__line');

        expect(removedLine).toBeTruthy();
        expect(removedLine?.classList.contains('render-diff__line--removed')).toBe(true);
        expect(addedLine).toBeTruthy();
        expect(addedLine?.classList.contains('render-diff__line--added')).toBe(true);
    });
});

describe('UnsupportedCard — oversized payload notice (Increment 1 behavior, should already pass)', () => {
    it('shows a byte-count notice instead of the JSON dump for a payload over 2,000,000 chars', () => {
        const data = { blob: 'x'.repeat(2_000_001) };
        render(<RenderCard render="nonexistent_widget" data={data} />);

        expect(screen.getByText(/^Payload too large to display \(\d+ bytes\)$/)).toBeInTheDocument();
    });
});
