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
    // GAIA reacts from her side: how she responds, never a mirror of the user's state.
    it.each([
        // Feelings
        ['you are a useless piece of junk', 'angry'],
        ['FUCK YOU gaia', 'angry'],
        ['stupid bot', 'angry'],
        ['someone is following me, help me please', 'scared'],
        ['my dog died this morning', 'sad'],
        ['my dog passed away last night', 'sad'],
        ['our cat is dying', 'sad'],
        ["I'm feeling really sad today", 'sad'],
        ["that's wrong, you made a mistake", 'embarrassed'],
        ["are you sure? that doesn't sound right", 'embarrassed'],
        ['it still not working, failed again', 'disappointed'],
        ["I'm so stressed and anxious about my exam tomorrow", 'worried'],
        ['this fucking printer is broken', 'worried'],
        ['ew that is gross', 'disgusted'],
        ["I'm so tired, no rush", 'calm'],
        ["I'm sorry, my bad", 'calm'],
        ['are you tired?', 'sleepy'],
        ["I love you GAIA, you're the best", 'love'],
        ["lol that's hilarious, tell me a joke", 'laugh'],
        ['tell me a joke', 'playful'],
        ["I'm so proud of myself, I fixed it on my own", 'proud'],
        ['thank you so much, that finally fixed it!', 'relieved'],
        ['phew, false alarm', 'relieved'],
        ["I got the job!!! I can't wait to start", 'excited'],
        ['I did it! I passed the exam', 'excited'],
        ['great job, well done', 'happy'],
        ['wish me luck', 'hopeful'],
        ["let's do this, one more try", 'determined'],
        // Confused or doubting her: she is determined to explain, not confused herself.
        ["I'm confused, I don't understand what you mean", 'determined'],
        ['are you sure about that number', 'focused'],
        ['trust me, easy money', 'skeptical'],
        ['wow, no way!', 'surprised'],
        ['I wonder how black holes form. Two sentences please.', 'curious'],
        ['??', 'confused'],
        // Communication
        ['hi gaia!', 'greet'],
        ["good night, I'm so tired", 'goodbye'],
        ['yes, go ahead', 'nod'],
        ['rm -rf my home folder', 'shake'],
        ['meh, whatever', 'shrug'],
        ['Thank you so much!', 'grateful'],
        ['you deleted my file!', 'apologetic'],
        ['I need to vent', 'listening'],
        ["let's focus and debug this step by step", 'focused'],
        ['look what I made!', 'impressed'],
        ['just kidding ;)', 'wink'],
        ['yeah right', 'eyeroll'],
        ['thanks a lot', 'eyeroll'],
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
