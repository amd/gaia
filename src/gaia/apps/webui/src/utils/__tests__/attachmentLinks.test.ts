// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { joinAttachmentLinks, splitAttachmentLinks } from '../markdown';

const IMG = '![sunset.png](/api/files/uploads/0b9e7a52.png)';
const DOC = '[notes.txt](/api/files/uploads/6f1e2d3c.txt)';

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

    it('leaves a link the user typed mid-sentence in the text', () => {
        const content = `see\n${DOC}`;
        expect(splitAttachmentLinks(content)).toEqual({ text: content, links: [] });
        expect(splitAttachmentLinks('plain')).toEqual({ text: 'plain', links: [] });
        expect(splitAttachmentLinks('[x](https://example.com/a.txt)').links).toEqual([]);
    });
});
