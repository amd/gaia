// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { isResearchMessage, withResearchCommand } from '../research';

describe('research mode', () => {
    it('recognises a leading /research command only', () => {
        expect(isResearchMessage('/research RISC-V')).toBe(true);
        expect(isResearchMessage('  /RESEARCH jwst')).toBe(true);
        expect(isResearchMessage('/research')).toBe(true);
        expect(isResearchMessage('/researcher notes')).toBe(false);
        expect(isResearchMessage('please /research this')).toBe(false);
    });

    it('prefixes a message only while research mode is on', () => {
        expect(withResearchCommand('the EU AI Act', true)).toBe('/research the EU AI Act');
        expect(withResearchCommand('the EU AI Act', false)).toBe('the EU AI Act');
    });

    it('never doubles the command or prefixes an empty message', () => {
        expect(withResearchCommand('/research jwst', true)).toBe('/research jwst');
        expect(withResearchCommand('   ', true)).toBe('   ');
    });
});
