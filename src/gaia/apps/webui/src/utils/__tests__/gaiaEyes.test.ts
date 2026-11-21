// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { eyesPhase, messageMood } from '../gaiaEyes';

describe('eyesPhase', () => {
    it('waits on the user before anything else', () => {
        expect(eyesPhase('Reading your request', true)).toBe('wait');
    });

    it.each([
        [null, 'think'],
        [undefined, 'think'],
        ['Thinking...', 'think'],
        ['Reasoning', 'think'],
        ['Reasoning — 38 words so far', 'think'],
        ['Waiting for GAIA to start this answer…', 'think'],
        ['Reading your request', 'read'],
        ['Reading the tool results', 'read'],
        ['Reading page 3 of 12…', 'read'],
        ['Loading Gemma-4-E4B-it-GGUF into memory', 'tool'],
        ['Searching the web', 'tool'],
        ['Reconnecting to the model server…', 'tool'],
    ] as const)('%s -> %s', (label, phase) => {
        expect(eyesPhase(label, false)).toBe(phase);
    });
});

describe('messageMood', () => {
    it.each([
        ["I'm feeling really sad today", 'sad'],
        ['my dog passed away last night', 'sad'],
        ['Thank you so much!', 'happy'],
        ['it works now', 'happy'],
        ["you're useless", 'angry'],
        ['FUCK YOU gaia', 'angry'],
        ['stupid bot', 'angry'],
        ['this fucking printer is broken', 'worried'],
        ["I'm so stressed about this deadline", 'worried'],
        ['lol that was great', 'laugh'],
        ['wow, no way', 'surprised'],
        ["I don't get it??", 'confused'],
        ['I love this', 'love'],
        ["that's wrong, the total is 42", 'embarrassed'],
        ["I'm so tired, good night", 'sleepy'],
        ['just kidding ;)', 'playful'],
        ['I did it! I passed the exam', 'excited'],
        ["I'm proud of this one", 'proud'],
        ['are you sure about that number', 'skeptical'],
        ['I wonder how tides work', 'curious'],
        ["ok let's try again", 'determined'],
    ] as const)('%s -> %s', (text, mood) => {
        expect(messageMood(text)).toBe(mood);
    });

    it.each([
        'summarize this report',
        'What were the Q3 sales in the spreadsheet?',
        'show me the downloads folder',
        'أنا حزين اليوم',
        '',
    ])('finds no feeling in "%s"', (text) => {
        expect(messageMood(text)).toBeNull();
    });
});
