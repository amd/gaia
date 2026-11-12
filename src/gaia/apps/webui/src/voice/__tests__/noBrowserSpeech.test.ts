// @vitest-environment node
// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Speech is recognized and spoken on this PC through Lemonade. The browser's own
// speech APIs send audio to a cloud service in some browsers, so the UI must never use them.

import { readdirSync, readFileSync } from 'fs';
import { join, resolve } from 'path';
import { describe, expect, it } from 'vitest';

const SRC = resolve(__dirname, '../..');
const FORBIDDEN = /\b(?:webkit)?SpeechRecognition\b|\bspeechSynthesis\b|\bSpeechSynthesisUtterance\b/;

function sources(dir: string): string[] {
    return readdirSync(dir, { withFileTypes: true }).flatMap((d) => {
        const path = join(dir, d.name);
        if (d.isDirectory()) return d.name === '__tests__' ? [] : sources(path);
        return /\.(ts|tsx|js|cjs)$/.test(d.name) ? [path] : [];
    });
}

describe('no browser speech services', () => {
    it('finds the sources it checks', () => {
        expect(sources(SRC).length).toBeGreaterThan(100);
    });

    it('never references SpeechRecognition or speechSynthesis', () => {
        const offenders = sources(SRC).filter((file) => FORBIDDEN.test(readFileSync(file, 'utf-8')));
        expect(offenders).toEqual([]);
    });
});
