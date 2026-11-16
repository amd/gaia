// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { GaiaApiError, GaiaConfigError } from '@/api/errors';

import { shouldRetryQuery } from '@/data/queryRetry';

import { kindOf } from './readAloudStore';

describe('kindOf', () => {
    it('reads a 404 from the speech routes as a PC too old to speak', () => {
        expect(kindOf(new GaiaApiError(404, 'Not Found'))).toBe('unsupported');
        expect(kindOf(new GaiaApiError(503, 'down'))).toBe('unreachable');
    });
});

describe('shouldRetryQuery', () => {
    it('shows a refusal at once and retries a server failure twice', () => {
        expect(shouldRetryQuery(0, new GaiaApiError(404, 'Not Found'))).toBe(false);
        expect(shouldRetryQuery(0, new GaiaApiError(403, 'no'))).toBe(false);
        expect(shouldRetryQuery(0, new GaiaConfigError('mobileApp.error.noConnection'))).toBe(false);
        expect(shouldRetryQuery(1, new GaiaApiError(503, 'down'))).toBe(true);
        expect(shouldRetryQuery(2, new GaiaApiError(503, 'down'))).toBe(false);
    });
});
