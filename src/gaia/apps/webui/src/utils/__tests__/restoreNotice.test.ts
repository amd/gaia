// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { stripIsolates } from '../../i18n/registry';
import { translator } from '../../i18n/useT';
import { restoreFailedText } from '../restoreNotice';

const ar = translator('ar');
const en = translator('en');

// The backend's failed restore for a Fireworks model with no key.
const noKey = {
    message: "Couldn't restore your last model, deepseek-v4p1-flash · Fireworks AI: it has no working key. Pick a model to continue.",
    model: 'deepseek-v4p1-flash · Fireworks AI',
    reason: 'it has no working key',
    reasonCode: 'no_key',
};

describe('failed restore banner', () => {
    it('says it in Arabic, naming the provider once', () => {
        const text = stripIsolates(restoreFailedText(ar, noKey));
        expect(text).toContain('تعذّرت استعادة آخر نموذج استخدمته، deepseek-v4p1-flash · Fireworks AI: لا يوجد له مفتاح صالح.');
        expect(text.split('Fireworks AI').length - 1).toBe(2); // once in Arabic, once in the English gloss
    });

    it('reads as the backend sent it in English', () => {
        expect(restoreFailedText(en, noKey)).toBe(noKey.message);
    });

    it('keeps a reason it has no words for inside the Arabic sentence', () => {
        const reason = 'Lemonade reports no Ryzen AI NPU on this PC';
        const text = stripIsolates(restoreFailedText(ar, {
            message: `Couldn't restore your last model, x · local: ${reason}. Pick a model to continue.`,
            model: 'x · local',
            reason,
            reasonCode: null,
        }));
        expect(text.startsWith('تعذّرت استعادة آخر نموذج استخدمته، x · local: Lemonade reports')).toBe(true);
    });

    it('shows a line it has no parts for as sent, left to right', () => {
        const message = 'Your last model was not restored: config.json is not valid JSON. Pick a model.';
        expect(restoreFailedText(ar, { message })).toBe(`⁦${message}⁩`);
    });
});
