// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { commandAtCaret, findSkillCommand, namesASkill } from '../skillCommand';

const INSTALLED = new Set(['pdf', 'humanizer', 'garden-kit:plant-care']);

/** The marked text, or null: what the user sees highlighted. */
function marked(text: string): string | null {
    const word = findSkillCommand(text, INSTALLED);
    return word ? text.slice(word.start, word.end) : null;
}

describe('findSkillCommand', () => {
    it('finds the command at the start or later in the text, in any case', () => {
        expect(marked('/pdf make a report')).toBe('/pdf');
        expect(marked('rewrite this with /humanizer')).toBe('/humanizer');
        expect(marked('rewrite this with /Humanizer.')).toBe('/Humanizer');
        expect(marked('water it\n/garden-kit:plant-care now')).toBe('/garden-kit:plant-care');
        expect(marked('لخّص هذا الملف بـ /pdf؟')).toBe('/pdf');
        expect(findSkillCommand('use /HUMANIZER', INSTALLED)?.name).toBe('humanizer');
    });

    it('marks the first installed skill only, the one that runs', () => {
        const text = 'use /nope then /pdf and /humanizer';
        const word = findSkillCommand(text, INSTALLED)!;
        expect(word).toEqual({ start: text.indexOf('/pdf'), end: text.indexOf('/pdf') + 4, name: 'pdf' });
    });

    it('never matches a path, a URL, a word glued to text, or a skill that is not installed', () => {
        for (const text of ['/tmp/x.txt', 'see /etc/hosts', 'https://x.test/pdf', 'and/pdf', '(/pdf)', '`/pdf`', '/pdf.txt', '/zzz', '‏/pdf']) {
            expect(marked(text), text).toBeNull();
        }
    });

    it('reads a map of skills as well as a set of names', () => {
        expect(findSkillCommand('go /pdf', new Map([['pdf', {}]]))?.name).toBe('pdf');
    });
});

describe('namesASkill', () => {
    it('is true for any command-shaped word, installed or not', () => {
        expect(namesASkill('try /anything')).toBe(true);
        expect(namesASkill('see /etc/hosts and https://x.test/a')).toBe(false);
    });
});

describe('commandAtCaret', () => {
    it('finds the / word the caret is in or just after, anywhere in the text', () => {
        expect(commandAtCaret('/', 1)).toEqual({ start: 0, end: 1, typed: '/' });
        expect(commandAtCaret('summarize this with /hum', 24)).toEqual({ start: 20, end: 24, typed: '/hum' });
        expect(commandAtCaret('use /hu for this', 7)).toEqual({ start: 4, end: 7, typed: '/hu' });
        expect(commandAtCaret('use /human for this', 7)).toEqual({ start: 4, end: 10, typed: '/hu' });
        expect(commandAtCaret('/garden-kit:pl', 14)).toEqual({ start: 0, end: 14, typed: '/garden-kit:pl' });
        expect(commandAtCaret('سطر\n/pd', 7)).toEqual({ start: 4, end: 7, typed: '/pd' });
    });

    it('stays out of paths, URLs, words glued to text, and a caret before the slash', () => {
        expect(commandAtCaret('/tmp/x.txt', 10)).toBeNull();
        expect(commandAtCaret('open /tmp/', 10)).toBeNull();
        expect(commandAtCaret('https://x.test/p', 16)).toBeNull();
        expect(commandAtCaret('and/p', 5)).toBeNull();
        expect(commandAtCaret('say /pdf', 4)).toBeNull();
        expect(commandAtCaret('say /pdf ', 9)).toBeNull();
        expect(commandAtCaret('plain words', 5)).toBeNull();
    });
});
