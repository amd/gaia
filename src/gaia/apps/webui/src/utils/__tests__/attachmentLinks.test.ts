// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { joinAttachmentLinks, splitAttachmentLinks } from '../markdown';

const IMG = '![sunset.png](/api/files/uploads/0b9e7a52.png)';
const DOC = '[notes.txt](/api/files/uploads/6f1e2d3c.txt)';
const SPACED = '[Q3 plan (final).pdf](/api/files/uploads/Q3%20plan%20%28final%29.pdf)';
const ARABIC = '[تقرير الاجتماع.pdf](/api/files/uploads/%D8%AA%D9%82%D8%B1%D9%8A%D8%B1%20%D8%A7%D9%84%D8%A7%D8%AC%D8%AA%D9%85%D8%A7%D8%B9.pdf)';

describe('attachment links in a sent message', () => {
    it('splits the links the composer added off the typed text, and puts them back', () => {
        const content = `hi\n\n${IMG}\n${DOC}`;
        const { text, links } = splitAttachmentLinks(content);
        expect(text).toBe('hi');
        expect(links.map((l) => l.name)).toEqual(['sunset.png', 'notes.txt']);
        expect(joinAttachmentLinks(text, links)).toBe(content);
    });

    it('handles a message that is only attachments', () => {
        const { text, links } = splitAttachmentLinks(DOC);
        expect(text).toBe('');
        expect(joinAttachmentLinks('read this', links)).toBe(`read this\n\n${DOC}`);
    });

    it('keeps an encoded name as the original label', () => {
        const content = `read it\n\n${SPACED}\n${ARABIC}`;
        const { text, links } = splitAttachmentLinks(content);
        expect(text).toBe('read it');
        expect(links.map((l) => l.name)).toEqual(['Q3 plan (final).pdf', 'تقرير الاجتماع.pdf']);
        expect(joinAttachmentLinks(text, links)).toBe(content);
    });

    it('leaves a link the user typed mid-sentence in the text', () => {
        const content = `see\n${DOC}`;
        expect(splitAttachmentLinks(content)).toEqual({ text: content, links: [] });
        expect(splitAttachmentLinks('plain')).toEqual({ text: 'plain', links: [] });
        expect(splitAttachmentLinks('[x](https://example.com/a.txt)').links).toEqual([]);
    });

    it('does not treat an encoded traversal as an attachment link', () => {
        const content = 'hi\n\n[x](/api/files/uploads/..%2F..%2Fsecret.txt)';
        expect(splitAttachmentLinks(content)).toEqual({ text: content, links: [] });
    });
});
