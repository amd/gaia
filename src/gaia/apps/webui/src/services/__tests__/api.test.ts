// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { listConnectors, disconnectConnector } from '../api';

/**
 * Regression tests for `apiFetch`'s non-JSON handling (issue #983).
 *
 * The bug: a GET that returned a non-JSON 200 (e.g. the SPA index.html served
 * because the route wasn't mounted) was silently cast to `undefined`, so
 * `const { connectors } = await api.listConnectors()` threw an opaque
 * "Cannot destructure property 'connectors'" instead of an actionable error.
 */

function jsonResponse(body: unknown, status = 200): Response {
    return new Response(JSON.stringify(body), {
        status,
        headers: { 'content-type': 'application/json' },
    });
}

function htmlResponse(body: string, status = 200): Response {
    return new Response(body, {
        status,
        headers: { 'content-type': 'text/html' },
    });
}

describe('apiFetch non-JSON handling', () => {
    beforeEach(() => {
        vi.stubGlobal('fetch', vi.fn());
    });

    afterEach(() => {
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
    });

    it('returns parsed JSON when the response is application/json', async () => {
        (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
            jsonResponse({ connectors: [{ id: 'google' }] }),
        );
        const { connectors } = await listConnectors();
        expect(connectors).toEqual([{ id: 'google' }]);
    });

    it('throws an actionable error for a non-JSON 200 (SPA fallthrough) instead of returning undefined', async () => {
        // Fresh Response per call — a Response body can only be read once.
        (fetch as ReturnType<typeof vi.fn>).mockImplementation(async () =>
            htmlResponse('<!doctype html><html><body>app</body></html>'),
        );
        await expect(listConnectors()).rejects.toThrow(/expected JSON, got text\/html/);
        // And specifically NOT the opaque destructure TypeError the bug produced.
        await expect(listConnectors()).rejects.not.toThrow(/Cannot destructure/);
    });

    it('names the method, path, and status in the thrown error', async () => {
        (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(htmlResponse('oops', 200));
        await expect(listConnectors()).rejects.toThrow(/GET \/connectors:.*HTTP 200/);
    });

    it('returns undefined for a 204 No Content (DELETE) without throwing', async () => {
        (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
            new Response(null, { status: 204 }),
        );
        await expect(disconnectConnector('google')).resolves.toBeUndefined();
    });

    it('returns undefined for an empty 200 body without throwing', async () => {
        (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
            new Response('', { status: 200 }),
        );
        await expect(disconnectConnector('google')).resolves.toBeUndefined();
    });
});
