// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { listConnectors } from '../../services/api';
import { isUiTokenRejection, noteBackendAnswer, useUiAuthStore } from '../uiAuthStore';

const REFUSAL = JSON.stringify({ detail: 'Sign in first.', code: 'ui_token_required' });

const json = (body: string, status: number) =>
    new Response(body, { status, headers: { 'content-type': 'application/json' } });

describe('uiAuthStore', () => {
    beforeEach(() => useUiAuthStore.setState({ signInRequired: false }));

    it('only the backend\'s token refusal counts, not any 401', () => {
        expect(isUiTokenRejection(401, REFUSAL)).toBe(true);
        expect(isUiTokenRejection(401, JSON.stringify({ detail: 'Bad key', code: 'other' }))).toBe(false);
        expect(isUiTokenRejection(401, '<html>proxy</html>')).toBe(false);
        expect(isUiTokenRejection(403, REFUSAL)).toBe(false);
    });

    it('a refusal sets it, a later error leaves it, a success clears it', () => {
        noteBackendAnswer(401, REFUSAL);
        expect(useUiAuthStore.getState().signInRequired).toBe(true);
        noteBackendAnswer(500, '{"detail":"x"}');
        expect(useUiAuthStore.getState().signInRequired).toBe(true);
        noteBackendAnswer(200);
        expect(useUiAuthStore.getState().signInRequired).toBe(false);
    });
});

describe('apiFetch reports a missing API token', () => {
    beforeEach(() => {
        useUiAuthStore.setState({ signInRequired: false });
        vi.stubGlobal('fetch', vi.fn());
    });
    afterEach(() => vi.unstubAllGlobals());

    it('a 401 ui_token_required asks the page to sign in, and the next success clears it', async () => {
        vi.mocked(fetch).mockResolvedValueOnce(json(REFUSAL, 401));
        await expect(listConnectors()).rejects.toMatchObject({ httpStatus: 401 });
        expect(useUiAuthStore.getState().signInRequired).toBe(true);

        vi.mocked(fetch).mockResolvedValueOnce(json(JSON.stringify({ connectors: [] }), 200));
        await listConnectors();
        expect(useUiAuthStore.getState().signInRequired).toBe(false);
    });
});
