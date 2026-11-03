// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// A library document named in an answer opens the file, and each page cited
// after it opens that page; unknown filenames stay plain text.

import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { RenderedContent } from '../MessageBubble';
import { useChatStore } from '../../stores/chatStore';
import { stripIsolates } from '../../i18n/registry';
import { findPages } from '../../utils/citations';
import type { Document } from '../../types';

const doc = (id: string, filename: string): Document => ({
    id, filename, filepath: `C:\\docs\\${filename}`, file_size: 1, chunk_count: 1,
    indexed_at: '2026-01-01T00:00:00Z', last_accessed_at: null, sessions_using: 0, indexing_status: 'complete',
});

const LIBRARY = [
    doc('terms', 'business-terms-2025.pdf'),
    doc('ar25', 'annual-report-2025-ar.pdf'),
    doc('ar24', 'annual-report-2024-ar.pdf'),
    doc('rep', 'report.pdf'),
];

beforeEach(() => useChatStore.setState({ documents: LIBRARY }));
afterEach(() => {
    act(() => useChatStore.getState().setLocale('en'));
    useChatStore.setState({ documents: [] });
});

/** Each citation link as [doc id, page or null, text], in order. */
function links(markdown: string) {
    const { container } = render(<RenderedContent content={markdown} />);
    return [...container.querySelectorAll<HTMLAnchorElement>('a.citation-link, a.citation-page')].map((a) => ({
        kind: a.classList.contains('citation-link') ? 'doc' : 'page',
        id: a.dataset.docId,
        page: a.dataset.page ? Number(a.dataset.page) : null,
        text: a.textContent,
        href: a.getAttribute('href'),
    }));
}

describe('citations in an answer', () => {
    it('Arabic, filename in inline code, "pages 6–7"', () => {
        expect(links('وفق `business-terms-2025.pdf`، الصفحتان 6–7:')).toEqual([
            expect.objectContaining({ kind: 'doc', id: 'terms', page: 6, text: 'business-terms-2025.pdf' }),
            expect.objectContaining({ kind: 'page', id: 'terms', page: 6, text: '6' }),
            expect.objectContaining({ kind: 'page', id: 'terms', page: 7, text: '7' }),
        ]);
    });

    it('Arabic, filename in prose, "page 60"', () => {
        const found = links('وفقاً لتقرير annual-report-2025-ar.pdf، صفحة 60:');
        expect(found.map(({ kind, id, page }) => [kind, id, page])).toEqual([['doc', 'ar25', 60], ['page', 'ar25', 60]]);
        expect(found[1].href).toMatch(/\/api\/documents\/ar25\/file#page=60$/);
    });

    it('English, "pages 175 and 205"', () => {
        expect(links('According to annual-report-2024-ar.pdf, pages 175 and 205:').map(({ kind, page }) => [kind, page]))
            .toEqual([['doc', 175], ['page', 175], ['page', 205]]);
    });

    it('a full path to a library file cites it, instead of revealing it in Explorer', () => {
        const { container } = render(<RenderedContent content={'According to C:\\Users\\me\\docs\\report.pdf (page 2):'} />);
        const cite = container.querySelector<HTMLAnchorElement>('a.citation-link')!;
        expect(cite.dataset.docId).toBe('rep');
        expect(cite.dataset.page).toBe('2');
        expect(container.querySelector('.file-path-link')).toBeNull();
    });

    it('reads Arabic-Indic page numbers', () => {
        expect(links('راجع report.pdf، صفحة ٤٢').map(({ kind, page }) => [kind, page])).toEqual([['doc', 42], ['page', 42]]);
    });

    it('gives a page to the document it follows, not an earlier one', () => {
        const found = links('Compare report.pdf with annual-report-2024-ar.pdf, page 5.');
        expect(found.map(({ kind, id, page }) => [kind, id, page])).toEqual([
            ['doc', 'rep', null], ['doc', 'ar24', 5], ['page', 'ar24', 5],
        ]);
    });

    it('leaves filenames that are not in the library alone', () => {
        const { container } = render(<RenderedContent content={'See notes.pdf, page 3, and `budget.xlsx`.'} />);
        expect(container.querySelector('a.citation-link, a.citation-page')).toBeNull();
        expect(container.textContent).toBe('See notes.pdf, page 3, and budget.xlsx.');
    });

    it('keeps today\'s Explorer link for a path outside the library', () => {
        const { container } = render(<RenderedContent content={'Saved to C:\\Users\\me\\other.pdf'} />);
        expect(container.querySelector('.file-path-link')).not.toBeNull();
        expect(container.querySelector('a.citation-link')).toBeNull();
    });

    it('says which page a link opens, in the chosen language', () => {
        act(() => useChatStore.getState().setLocale('ar'));
        const { container } = render(<RenderedContent content={'وفق report.pdf، صفحة 7'} />);
        expect(stripIsolates(container.querySelector('a.citation-page')!.getAttribute('title')!))
            .toBe('فتح الصفحة 7 (Open page 7)');
    });
});

describe('page references', () => {
    const pages = (text: string) => findPages(text).map((p) => p.page);
    it.each([
        [', pages 2, 5', [2, 5]],
        [' (pp. 3-4)', [3, 4]],
        ['، الصفحات 2 و 5', [2, 5]],
        ['، الصفحتان ٦–٧', [6, 7]],
        [', p. 12', [12]],
        ['، ص 9', [9]],
    ])('%s', (text, expected) => expect(pages(text)).toEqual(expected));

    it('ignores a page reference too far from the filename', () => {
        expect(pages(' is the report that covers everything since last year, page 4')).toEqual([]);
    });
});
