// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { GaiaApiError } from '@/api/errors';
import { translator } from '@/i18n/useT';

import { isQuestionGone, questionCardData, resolveTypedAnswer, timeLeftText } from './questionLogic';

const options = [
    { value: 'a', label: 'Keep it' },
    { value: 'b', label: 'Delete it' },
];

describe('resolveTypedAnswer', () => {
    it('maps a number or a label to its option, and takes free text only when allowed', () => {
        expect(resolveTypedAnswer('2', { options, allowFreeText: true })).toEqual({ value: 'b', shown: 'Delete it' });
        expect(resolveTypedAnswer(' keep it ', { options, allowFreeText: false })).toEqual({ value: 'a', shown: 'Keep it' });
        expect(resolveTypedAnswer('maybe', { options, allowFreeText: true })).toEqual({ value: 'maybe', shown: 'maybe' });
        expect(resolveTypedAnswer('maybe', { options, allowFreeText: false })).toBeNull();
    });
});

describe('questionCardData', () => {
    it('keeps the deadline of a replayed question and ignores one with no id', () => {
        const event = { type: 'needs_input', request_id: 'r1', question: 'Q?', timeout_seconds: 300, expires_at: 2000 } as const;
        expect(questionCardData('s', event, 1_000_000)?.expiresAt).toBe(2_000_000);
        expect(questionCardData('s', { ...event, expires_at: undefined }, 1_000_000)?.expiresAt).toBe(1_300_000);
        expect(questionCardData('s', { ...event, sensitive: true }, 0)?.sensitive).toBe(true);
        expect(questionCardData('s', { type: 'needs_input' })).toBeNull();
    });
});

describe('answer errors and time left', () => {
    it('treats only a 404 as the question being gone', () => {
        expect(isQuestionGone(new GaiaApiError(404, 'gone'))).toBe(true);
        expect(isQuestionGone(new GaiaApiError(409, 'relay'))).toBe(false);
    });

    it('rounds minutes up and counts seconds below a minute', () => {
        const t = translator('en');
        expect(timeLeftText(61_000, t)).toBe('GAIA waits 2 min more for an answer.');
        expect(timeLeftText(40_200, t)).toBe('GAIA waits 40 s more for an answer.');
    });
});
