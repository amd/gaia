// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { EYES_GLYPHS, asksUser, eyesPhase, finalGlyph, messageMood } from '../gaiaEyes';

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

describe('finalGlyph', () => {
    it.each([
        ['love', 'Right back at you.', 'heart'],
        ['grateful', 'My pleasure.', 'heart'],
        ['sad', "I'm so sorry.", 'tear'],
        ['angry', 'That was uncalled for.', 'bolt'],
        ['surprised', 'Really?', 'exclaim'],
        ['impressed', 'That robot is brilliant.', 'star'],
        ['happy', 'Glad to help.', 'sun'],
        ['wink', 'Got me.', 'note'],
        ['worried', 'That sounds stressful.', 'cloud'],
        ['hopeful', 'Good luck!', 'sprout'],
        ['goodbye', 'Sleep well.', 'moon'],
        ['curious', 'Black holes form when...', 'brain'],
        ['relieved', 'Phew, all good.', 'check'],
        ['confused', 'Hm.', 'question'],
    ] as const)('%s settles on %s', (mood, answer, glyph) => {
        expect(finalGlyph(mood, answer)).toBe(glyph);
    });

    it('asks with a question mark when a quiet reply ends on a question', () => {
        expect(finalGlyph('focused', 'Which number do you mean?')).toBe('question');
        expect(finalGlyph(null, 'Shall I run it?')).toBe('question');
        expect(finalGlyph(null, 'هل أبدأ؟')).toBe('question');
    });

    it('keeps an intense reaction over a closing question', () => {
        expect(finalGlyph('excited', 'Congratulations! When do you start?')).toBe('star');
        expect(finalGlyph('sad', "I'm so sorry. Do you want to talk about it?")).toBe('tear');
    });

    it('lets a question replace a mild reaction, since it is their turn', () => {
        expect(finalGlyph('happy', 'Happy to help!\n\nWhere are you leaving from?')).toBe('question');
        expect(finalGlyph('playful', 'Want another one?')).toBe('question');
        expect(finalGlyph('happy', 'Happy to help!')).toBe('sun');
    });

    it('settles on the brain otherwise', () => {
        expect(finalGlyph(null, 'Here is the summary.')).toBe('brain');
        expect(finalGlyph('skeptical', 'That claim does not hold up.')).toBe('brain');
        expect(finalGlyph('not-a-mood', 'Done.')).toBe('brain');
    });

    it('reads a question in the last paragraph, or a list of them', () => {
        expect(asksUser('Want me to keep going?')).toBe(true);
        expect(asksUser('**Should I continue?**')).toBe(true);
        expect(asksUser('Here is the plan.\n\nWhere are you leaving from? I assume Friday.')).toBe(true);
        expect(asksUser('A few things first:\n- Where to?\n- How many people?\n\nI will draft it after.')).toBe(true);
        expect(asksUser('Done? Yes.\n\nAll three files are saved.')).toBe(false);
        expect(asksUser('Why does it work? Caching.\n\nThat is all.')).toBe(false);
    });

    it('draws every icon it can settle on', () => {
        expect(EYES_GLYPHS).toContain('brain');
        expect(new Set(EYES_GLYPHS).size).toBe(EYES_GLYPHS.length);
    });
});
