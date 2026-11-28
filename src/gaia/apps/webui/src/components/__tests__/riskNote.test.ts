// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { stripIsolates } from '../../i18n/registry';
import { translator } from '../../i18n/useT';
import { riskNoteText } from '../riskNote';

const en = translator('en');
const ar = translator('ar');
const NOTE = "GAIA can't check where this command reads or writes: python3 runs code, which can read and write anywhere you can. It can reach files outside the folders this chat may use.";

describe('riskNoteText', () => {
    it('leaves English as Fast and light wrote it', () => {
        expect(stripIsolates(riskNoteText(NOTE, en))).toBe(NOTE);
        const online = NOTE.replace(/\.$/, ", and GAIA can't tell whether it goes online.");
        expect(stripIsolates(riskNoteText(online, en))).toBe(online);
    });

    it('translates the note and its reason into Arabic', () => {
        const said = stripIsolates(riskNoteText(NOTE, ar));
        expect(said).toMatch(/^لا يستطيع GAIA التحقق/);
        expect(said).toContain('python3 يشغّل تعليمات برمجية');
    });

    it('says the note without a reason it does not know, rather than in English', () => {
        const odd = NOTE.replace('python3 runs code, which can read and write anywhere you can', 'git is pointed at another folder');
        expect(stripIsolates(riskNoteText(odd, en))).toBe(odd);
        expect(stripIsolates(riskNoteText(odd, ar))).toMatch(/^لا يستطيع GAIA التحقق[^:]*\./);
    });

    it("shows any other agent's note as written", () => {
        expect(riskNoteText('Sends your commits to the remote repository.', ar)).toBe('Sends your commits to the remote repository.');
    });
});
