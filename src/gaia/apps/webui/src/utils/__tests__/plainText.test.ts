// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { plainAnswerText } from '../plainText';

describe('plainAnswerText', () => {
    it('drops bold marks and turns dashes into bullets', () => {
        const md = '- **Sunlight** — light energy\n- **Water** — from the roots';
        expect(plainAnswerText(md)).toBe('• Sunlight — light energy\n• Water — from the roots');
    });

    it('drops heading and quote marks and keeps link addresses', () => {
        const md = '## Steps\n\n> note\n\nSee [the docs](https://amd-gaia.ai/docs) and `gaia init`.';
        expect(plainAnswerText(md)).toBe('Steps\n\nnote\n\nSee the docs (https://amd-gaia.ai/docs) and gaia init.');
    });

    it('keeps code block text exactly, without fences or emphasis rewriting', () => {
        const md = 'Run:\n\n```python\nx = 2 * 3 * 4\nprint(**kw)\n```\n\n**Done**';
        expect(plainAnswerText(md)).toBe('Run:\n\nx = 2 * 3 * 4\nprint(**kw)\n\nDone');
    });

    it('turns a table into tab-separated rows without the rule line', () => {
        const md = '| Step | Note |\n| --- | --- |\n| 1 | **Mix** |';
        expect(plainAnswerText(md)).toBe('Step\tNote\n1\tMix');
    });

    it('leaves snake_case and ordered lists alone', () => {
        expect(plainAnswerText('1. set max_ctx_size\n2. restart')).toBe('1. set max_ctx_size\n2. restart');
    });

    it('keeps inline code and arithmetic exactly', () => {
        expect(plainAnswerText('Call `__init__` then `*args, **kwargs`.')).toBe('Call __init__ then *args, **kwargs.');
        expect(plainAnswerText('Use `[a](b)` here')).toBe('Use [a](b) here');
        expect(plainAnswerText('a 5*3 = 15\nand 4*2 = 8')).toBe('a 5*3 = 15\nand 4*2 = 8');
    });

    it('drops backslash escapes and keeps the escaped character', () => {
        expect(plainAnswerText('Escaped \\*star\\* and 1\\. item')).toBe('Escaped *star* and 1. item');
    });

    it('handles an empty code block without swallowing the text after it', () => {
        expect(plainAnswerText('```\n```\nSome **bold** text')).toBe('Some bold text');
    });
});
