// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { FIRST_PIECE_CHARS, PIECE_CHARS, speakableText, speechPieces } from '../speechText';

describe('speakableText', () => {
    it('reads the words and skips what a voice cannot say', () => {
        const md = [
            '## Results',
            '',
            'See **the report** at [our docs](https://amd-gaia.ai/docs) or https://example.com/x.',
            '',
            '```python',
            'print("hello")',
            '```',
            '',
            '- first `item`',
            '1. second item',
            '> quoted',
            '![chart](chart.png)',
        ].join('\n');
        expect(speakableText(md)).toBe(
            'Results\nSee the report at our docs or\nfirst item\nsecond item\nquoted',
        );
    });

    it('reads a table as its cells, without the rule line', () => {
        expect(speakableText('| Model | Size |\n|---|---|\n| Gemma | 6 GB |')).toBe(
            'Model, Size\nGemma, 6 GB',
        );
    });

    it('drops emphasis and rules but keeps snake_case words whole', () => {
        expect(speakableText('Set _really_ `max_ctx_size` to __64K__.\n\n---\n\n* * *\nDone ~~now~~.')).toBe(
            'Set really max_ctx_size to 64K.\nDone now.',
        );
    });

    it('is empty for a reply that is only code', () => {
        expect(speakableText('```\nls -la\n```')).toBe('');
        expect(speechPieces('```\nls -la\n```')).toEqual([]);
    });
});

describe('speechPieces', () => {
    const sentence = (i: number) => `This is sentence number ${i} of a long reply.`;
    const long = Array.from({ length: 60 }, (_, i) => sentence(i)).join(' ');

    it('starts with a short piece, then longer ones, keeping every word in order', () => {
        const pieces = speechPieces(long);
        expect(pieces[0].length).toBeLessThanOrEqual(FIRST_PIECE_CHARS);
        expect(pieces.slice(1).every((p) => p.length <= PIECE_CHARS)).toBe(true);
        expect(pieces.length).toBeGreaterThan(2);
        expect(pieces.join(' ')).toBe(long);
    });

    it('cuts a sentence longer than the server limit at a space', () => {
        const run = Array.from({ length: 300 }, () => 'word').join(' ');
        const pieces = speechPieces(run, 100);
        expect(pieces.every((p) => p.length <= 100)).toBe(true);
        expect(pieces.join(' ')).toBe(run);
    });

    it('keeps the first piece short when the reply opens with a long sentence', () => {
        const opening = Array.from({ length: 80 }, () => 'word').join(' ') + '.';
        const reply = `${opening} ${long}`;
        const pieces = speechPieces(reply);
        expect(pieces[0].length).toBeLessThanOrEqual(FIRST_PIECE_CHARS);
        expect(pieces.join(' ')).toBe(reply);
    });

    it('splits Arabic text at its question mark too', () => {
        expect(speechPieces('ما هذا؟ هذا اختبار.', 12)).toEqual(['ما هذا؟', 'هذا اختبار.']);
    });
});
