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

    it('names the answer\'s chat, which away from the PC is what may open the document', () => {
        const { container } = render(<RenderedContent content={'According to report.pdf, page 2:'} sessionId="chat 1" />);
        const hrefs = [...container.querySelectorAll<HTMLAnchorElement>('a.citation-link, a.citation-page')].map((a) => a.getAttribute('href'));
        expect(hrefs).toHaveLength(2);
        for (const href of hrefs) expect(href).toMatch(/\/api\/documents\/rep\/file\?session_id=chat\+1#page=2$/);
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

describe('citations to dropped files and same-named files', () => {
    const at = (id: string, filename: string, filepath: string): Document => ({ ...doc(id, filename), filepath });
    const UPLOADS = 'C:\\Users\\me\\.gaia\\documents';

    it('a full path to a dropped file, stored with an id in front, cites it', () => {
        useChatStore.setState({ documents: [at('up', 'report.pdf', `${UPLOADS}\\a1b2c3d4e5f6_report.pdf`)] });
        const found = links(`According to ${UPLOADS}\\a1b2c3d4e5f6_report.pdf (page 3):`);
        expect(found.map(({ kind, id, page }) => [kind, id, page])).toEqual([['doc', 'up', 3], ['page', 'up', 3]]);
        expect(found[0].text).toBe('report.pdf');
    });

    it('the stored name of a dropped file on its own cites it', () => {
        useChatStore.setState({ documents: [at('up', 'notes.md', `${UPLOADS}\\0123456789ab_notes.md`)] });
        expect(links('See `0123456789ab_notes.md`, page 1.').map(({ kind, id }) => [kind, id])).toEqual([
            ['doc', 'up'], ['page', 'up'],
        ]);
    });

    it('a Linux path to a dropped file cites it', () => {
        useChatStore.setState({ documents: [at('up', 'report.pdf', '/home/me/.gaia/documents/a1b2c3d4e5f6_report.pdf')] });
        const found = links('According to /home/me/.gaia/documents/a1b2c3d4e5f6_report.pdf, page 2:');
        expect(found.map(({ kind, id, page }) => [kind, id, page])).toEqual([['doc', 'up', 2], ['page', 'up', 2]]);
    });

    const TWINS = [
        at('a23', 'report.pdf', 'C:\\work\\2023\\report.pdf'),
        at('a24', 'report.pdf', 'C:\\work\\2024\\report.pdf'),
        at('annual', 'annual_report.pdf', 'C:\\work\\annual_report.pdf'),
    ];

    it('each of two same-named files opens itself, labelled with its folder', () => {
        useChatStore.setState({ documents: TWINS });
        const found = links('C:\\work\\2023\\report.pdf says 5; C:\\work\\2024\\report.pdf says 7.');
        expect(found.map(({ id, text }) => [id, text])).toEqual([['a23', '2023/report.pdf'], ['a24', '2024/report.pdf']]);
    });

    it('a same-named file under a folder with a space opens itself', () => {
        useChatStore.setState({ documents: [
            at('js23', 'plan.pdf', 'C:\\Users\\John Smith\\2023\\plan.pdf'),
            at('js24', 'plan.pdf', 'C:\\Users\\John Smith\\2024\\plan.pdf'),
        ] });
        const found = links('See C:\\Users\\John Smith\\2024\\plan.pdf, page 4.');
        expect(found.map(({ kind, id }) => [kind, id])).toEqual([['doc', 'js24'], ['page', 'js24']]);
        expect(found[0].text).toBe('2024/plan.pdf');
    });

    describe('paths with spaces in their folders', () => {
        const SPACED = [
            at('only', 'summary.pdf', 'C:\\Users\\John Smith\\2023\\summary.pdf'),
            at('js23', 'plan.pdf', 'C:\\Users\\John Smith\\2023\\plan.pdf'),
            at('js24', 'plan.pdf', 'C:\\Users\\John Smith\\2024\\plan.pdf'),
            at('od', 'budget.xlsx', 'C:\\Users\\me\\OneDrive - AMD\\Finance\\budget.xlsx'),
            at('wide', 'q.md', 'C:\\A  B\\c   d\\q.md'),
        ];
        beforeEach(() => useChatStore.setState({ documents: SPACED }));

        /** The rendered text, with each link's label standing in for what it covers. */
        function shown(markdown: string) {
            const { container } = render(<RenderedContent content={markdown} />);
            return stripIsolates(container.textContent ?? '');
        }

        it('a file no other document shares a name with links the whole path', () => {
            const text = 'See C:\\Users\\John Smith\\2023\\summary.pdf, page 4.';
            expect(links(text).map(({ kind, id, page }) => [kind, id, page])).toEqual([['doc', 'only', 4], ['page', 'only', 4]]);
            expect(shown(text)).toBe('See summary.pdf, page 4.');
        });

        it.each([
            ['a period', 'Per C:\\Users\\me\\OneDrive - AMD\\Finance\\budget.xlsx.', 'Per budget.xlsx.'],
            ['a comma', 'Per C:\\Users\\me\\OneDrive - AMD\\Finance\\budget.xlsx, the total is 9.', 'Per budget.xlsx, the total is 9.'],
            ['a closing paren', 'Totals (C:\\Users\\me\\OneDrive - AMD\\Finance\\budget.xlsx) match.', 'Totals (budget.xlsx) match.'],
            ['a semicolon', 'Per C:\\Users\\me\\OneDrive - AMD\\Finance\\budget.xlsx; done', 'Per budget.xlsx; done'],
        ])('an OneDrive - AMD path followed by %s links only the path', (_, text, expected) => {
            expect(links(text).map(({ id }) => id)).toEqual(['od']);
            expect(shown(text)).toBe(expected);
        });

        it('several spaces in a row inside a folder name', () => {
            const text = 'Per C:\\A  B\\c   d\\q.md, ok';
            expect(links(text).map(({ id }) => id)).toEqual(['wide']);
            expect(shown(text)).toBe('Per q.md, ok');
        });

        it('two spaced paths in one sentence each link to their own file', () => {
            const text = 'Compare C:\\Users\\John Smith\\2023\\plan.pdf and C:\\Users\\John Smith\\2024\\plan.pdf for totals.';
            expect(links(text).map(({ id }) => id)).toEqual(['js23', 'js24']);
            expect(shown(text)).toBe('Compare 2023/plan.pdf and 2024/plan.pdf for totals.');
        });

        it('a spaced path not in the library does not swallow the cited path after it', () => {
            const text = 'in C:\\Docs\\a b.pdf and C:\\Users\\John Smith\\2024\\plan.pdf';
            expect(links(text).map(({ id }) => id)).toEqual(['js24']);
            expect(shown(text)).toContain('a b.pdf and 2024/plan.pdf');
        });

        it('forward slashes and different case still find the stored path', () => {
            expect(links('See c:/users/john smith/2023/summary.pdf now').map(({ id }) => id)).toEqual(['only']);
        });

        it('prose around a bare name is not pulled into the link', () => {
            const text = 'see the notes in my folder and then summary.pdf, page 2.';
            expect(links(text).map(({ kind, id }) => [kind, id])).toEqual([['doc', 'only'], ['page', 'only']]);
            expect(shown(text)).toBe(text);
        });

        it('a folder named in prose before a bare name does not join it into a path', () => {
            const text = 'My John Smith folder has summary.pdf in it.';
            expect(links(text).map(({ id, text: label }) => [id, label])).toEqual([['only', 'summary.pdf']]);
            expect(shown(text)).toBe(text);
        });

        it('a partial path that only ends like a stored one links nothing extra', () => {
            const text = 'See D:\\Other Place\\2023\\plan.pdf instead.';
            expect(links(text)).toEqual([]);
        });
    });

    it('a bare name two files share links neither, rather than the wrong one', () => {
        useChatStore.setState({ documents: TWINS });
        expect(links('According to report.pdf, page 2.')).toEqual([]);
        expect(links('According to annual_report.pdf, page 2.').map(({ id }) => id)).toEqual(['annual', 'annual']);
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
