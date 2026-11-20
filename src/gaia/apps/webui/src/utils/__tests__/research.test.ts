// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { isResearchMessage, isSkillCommand, withResearchCommand } from '../research';

describe('research mode', () => {
    it('recognises a leading /research command only', () => {
        expect(isResearchMessage('/research RISC-V')).toBe(true);
        expect(isResearchMessage('  /RESEARCH jwst')).toBe(true);
        expect(isResearchMessage('/research')).toBe(true);
        expect(isResearchMessage('/research-report jwst')).toBe(true);
        expect(isResearchMessage('/researcher notes')).toBe(false);
        expect(isResearchMessage('/research-reports jwst')).toBe(false);
        expect(isResearchMessage('please /research this')).toBe(false);
    });

    it('prefixes a message only while research mode is on', () => {
        expect(withResearchCommand('the EU AI Act', true)).toBe('/research the EU AI Act');
        expect(withResearchCommand('the EU AI Act', false)).toBe('the EU AI Act');
    });

    it('never doubles the command or prefixes an empty message', () => {
        expect(withResearchCommand('/research jwst', true)).toBe('/research jwst');
        expect(withResearchCommand('/research-report jwst', true)).toBe('/research-report jwst');
        expect(withResearchCommand('   ', true)).toBe('   ');
    });

    it('leaves a command for an installed skill alone and researches anything else', () => {
        const skills = new Set(['docx', 'research-report']);
        expect(withResearchCommand('/docx draft a memo', true, skills)).toBe('/docx draft a memo');
        expect(withResearchCommand('/docx', true, skills)).toBe('/docx');
        expect(withResearchCommand('/docs draft a memo', true, skills)).toBe('/research /docs draft a memo');
        expect(withResearchCommand('/tmp/notes.txt', true, skills)).toBe('/research /tmp/notes.txt');
        expect(withResearchCommand('/docx.txt notes', true, skills)).toBe('/research /docx.txt notes');
        expect(withResearchCommand('plain question', true, skills)).toBe('/research plain question');
        expect(withResearchCommand('/docx\nthe memo', true, skills)).toBe('/docx\nthe memo');
        for (const text of ['/DOCX memo', '/docx-pro memo', '/docx/notes', 'see /docx memo']) {
            expect(withResearchCommand(text, true, skills)).toBe(`/research ${text}`);
        }
        expect(withResearchCommand('/docx memo', false, skills)).toBe('/docx memo');
        expect(isSkillCommand('/docx memo', new Set())).toBe(false);
    });
});
