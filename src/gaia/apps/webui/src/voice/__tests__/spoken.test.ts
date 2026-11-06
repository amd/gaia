// @vitest-environment node
// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { readFileSync } from 'fs';
import { resolve } from 'path';
import { describe, expect, it } from 'vitest';
import { leadLine, SpokenSplitter, splitSpoken } from '../spoken';

interface Case {
    name: string;
    stream: Array<string | { reset: true }>;
    spoken: string;
    detail: string;
    missing: boolean;
}

// The cases the Python and Go splitters run too.
const FIXTURE = resolve(__dirname, '../../../../../../../tests/fixtures/voice/spoken_cases.json');
const { cases } = JSON.parse(readFileSync(FIXTURE, 'utf-8')) as { cases: Case[] };

function run(stream: Case['stream'], oneCharAtATime: boolean) {
    const splitter = new SpokenSplitter();
    let detail = '';
    for (const item of stream) {
        if (typeof item !== 'string') {
            splitter.reset();
            detail = '';
            continue;
        }
        const parts = oneCharAtATime ? [...item] : [item];
        if (parts.length === 0) parts.push('');
        for (const part of parts) {
            for (const [channel, text] of splitter.feed(part)) if (channel === 'detail') detail += text;
        }
    }
    for (const [channel, text] of splitter.finish()) if (channel === 'detail') detail += text;
    return { spoken: splitter.spokenText, detail: detail.trim(), missing: splitter.missing };
}

describe('SpokenSplitter, shared cases', () => {
    it('reads the shared fixture', () => {
        expect(cases.length).toBeGreaterThan(5);
    });

    for (const c of cases) {
        it(`${c.name} (whole strings)`, () => {
            expect(run(c.stream, false)).toEqual({ spoken: c.spoken, detail: c.detail, missing: c.missing });
        });
        it(`${c.name} (one character at a time)`, () => {
            expect(run(c.stream, true)).toEqual({ spoken: c.spoken, detail: c.detail, missing: c.missing });
        });
    }
});

describe('splitSpoken and leadLine', () => {
    it('splits a stored answer', () => {
        expect(splitSpoken('<spoken>Done.</spoken>\n\nThe table.')).toEqual({ spoken: 'Done.', detail: 'The table.', missing: false });
    });

    it('leaves a plain answer untouched', () => {
        expect(leadLine('Just text\n\n- a list')).toEqual({ lead: '', body: 'Just text\n\n- a list' });
    });

    it('never shows a half-written tag', () => {
        expect(leadLine('<spok')).toEqual({ lead: '', body: '' });
        expect(leadLine('<spoken>Four invoi')).toEqual({ lead: 'Four invoi', body: '' });
    });

    it('lifts the lead line off the detail', () => {
        expect(leadLine('<spoken>Four invoices, $3,412.50.</spoken>\n| a | b |')).toEqual({
            lead: 'Four invoices, $3,412.50.', body: '| a | b |',
        });
    });
});
