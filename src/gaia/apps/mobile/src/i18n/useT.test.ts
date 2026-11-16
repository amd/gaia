// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { GaiaNetworkError } from '@/api/errors';

import { describeErrorWith, technicalDetail, translator } from './useT';

describe('nested error messages', () => {
    const error = new GaiaNetworkError('http://127.0.0.1:4210/api/setup/check', new Error('fetch failed'));

    it('brackets an Arabic error once, each half in its own language, with no URL or raw cause', () => {
        const t = translator('ar');
        const text = t.compose((say) => say('setup.unknown.failed', { error: describeErrorWith(error, say) }));
        expect(text.split('(').length - 1).toBe(1);
        expect(text.slice(text.indexOf('(')).match(/[\u0600-\u06FF]/)).toBeNull();
        expect(text).not.toContain('127.0.0.1');
        expect(text).not.toContain('fetch failed');
        expect(technicalDetail(error)).toContain('fetch failed');
    });
});
