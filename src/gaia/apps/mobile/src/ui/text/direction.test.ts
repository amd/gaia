// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { isolate } from '@webui/i18n/registry';

import { contentDir, isolateMisleads } from './direction';

describe('contentDir', () => {
    it('skips an isolated LTR run before Arabic', () => {
        expect(contentDir(`${isolate('469.7 KB', 'ltr')} · قبل 5 د`, 'rtl')).toBe('rtl');
        expect(contentDir(`${isolate('469.7 KB', 'ltr')} · قبل 5 د`, 'ltr')).toBe('rtl');
    });

    it('reads an English line with an isolated size as LTR', () => {
        expect(contentDir(`${isolate('469.7 KB', 'ltr')} · 5 min ago`, 'ltr')).toBe('ltr');
    });

    it('skips nested isolates until the outer one closes', () => {
        expect(contentDir('⁦a⁧b⁩c⁩ · د', 'ltr')).toBe('rtl');
    });

    it('ignores an unmatched PDI and falls back when nothing is left', () => {
        expect(contentDir('⁩ د', 'ltr')).toBe('rtl');
        expect(contentDir(isolate('KB', 'ltr'), 'rtl')).toBe('rtl');
    });
});

describe('isolateMisleads', () => {
    it('is true only when the isolate changes the first-strong guess', () => {
        expect(isolateMisleads(`${isolate('9 KB', 'ltr')} · د`, 'rtl')).toBe(true);
        expect(isolateMisleads('Hello د', 'ltr')).toBe(false);
        expect(isolateMisleads(`${isolate('9 KB', 'ltr')} · more`, 'ltr')).toBe(false);
    });
});
