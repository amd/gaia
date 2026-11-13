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
});
