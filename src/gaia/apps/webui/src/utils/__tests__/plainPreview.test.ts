// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { plainPreview } from '../plainPreview';

describe('plainPreview', () => {
    it('drops bold, italic, code and link syntax but keeps the words', () => {
        expect(plainPreview('**Three headlines** today, *see* [the full story](https://example.com/story) and `code`.'))
            .toBe('Three headlines today, see the full story and code.');
    });

    it('flattens headings, lists and line breaks into one line', () => {
        expect(plainPreview('## Summary\n\n- first point\n- second point\n\n> quoted')).toBe('Summary first point second point quoted');
    });

    it('leaves snake_case names and plain asterisk math alone', () => {
        expect(plainPreview('Saved my_file_name.txt, 2 * 3 = 6')).toBe('Saved my_file_name.txt, 2 * 3 = 6');
        expect(plainPreview('Edited `__init__.py` and __main__.py')).toBe('Edited __init__.py and __main__.py');
        expect(plainPreview('__bold__ text')).toBe('bold text');
        expect(plainPreview('files: *.py and *.ts')).toBe('files: *.py and *.ts');
    });
});
