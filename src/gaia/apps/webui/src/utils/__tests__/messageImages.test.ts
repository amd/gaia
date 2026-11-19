// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { splitUploadedImages } from '../messageImages';

describe('splitUploadedImages', () => {
    it('lifts uploaded image lines out of the text', () => {
        const { text, images } = splitUploadedImages('look\n![cat.png](/api/files/uploads/cat.png)');
        expect(text).toBe('look');
        expect(images).toEqual([{ src: '/api/files/uploads/cat.png', name: 'cat.png' }]);
    });

    it('decodes the file name when the link has no label', () => {
        const { images } = splitUploadedImages('![Q3 plan (final).pdf](/api/files/uploads/Q3%20plan%20%28final%29.pdf)');
        expect(images).toEqual([
            { src: '/api/files/uploads/Q3%20plan%20%28final%29.pdf', name: 'Q3 plan (final).pdf' },
        ]);
    });

    it('leaves an image line inside a code block alone', () => {
        const content = '```md\n![x](/api/files/uploads/y.png)\n```';
        expect(splitUploadedImages(content)).toEqual({ text: content, images: [] });
    });

    it('leaves a traversal link in the text', () => {
        const content = '![x](/api/files/uploads/..%2F..%2Fsecret.png)';
        expect(splitUploadedImages(content)).toEqual({ text: content, images: [] });
    });
});
