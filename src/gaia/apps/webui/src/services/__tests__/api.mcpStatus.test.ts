// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The backend emits one `mcp_status` SSE event per request. The stream parser
 * must recognise it (no "Unknown SSE event type" warning), keep it out of the
 * chat callbacks, and warn only for a server that failed to connect.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { waitFor } from '@testing-library/react';
import { sendMessageStream, type StreamCallbacks } from '../api';
import { log } from '../../utils/logger';

function sseResponse(events: Record<string, unknown>[]): Response {
    const body = events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('');
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
        start(controller) {
            controller.enqueue(encoder.encode(body));
            controller.close();
        },
    });
    return new Response(stream, {
        status: 200,
        headers: { 'content-type': 'text/event-stream' },
    });
}

describe('mcp_status SSE dispatch (services/api.ts)', () => {
    beforeEach(() => {
        vi.stubGlobal('fetch', vi.fn());
    });

    afterEach(() => {
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
    });

    it('handles mcp_status without an unknown-type warning and warns only for failed servers', async () => {
        const warn = vi.spyOn(log.stream, 'warn');
        (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
            sseResponse([
                {
                    type: 'mcp_status',
                    servers: [
                        { name: 'github', connected: true, tool_count: 12, error: null },
                        { name: 'jira', connected: false, tool_count: 0, error: 'spawn ENOENT' },
                    ],
                },
                { type: 'done', content: 'ok' },
            ]),
        );

        const callbacks: StreamCallbacks = {
            onChunk: vi.fn(),
            onAgentEvent: vi.fn(),
            onDone: vi.fn(),
            onError: vi.fn(),
        };

        sendMessageStream('session-1', 'hi', callbacks);
        await waitFor(() => expect(callbacks.onDone).toHaveBeenCalled());

        const messages = warn.mock.calls.map((c) => String(c[0]));
        expect(messages.some((m) => m.includes('Unknown SSE event type'))).toBe(false);
        expect(messages).toEqual(['MCP server "jira" is not connected: spawn ENOENT']);
        expect(callbacks.onAgentEvent).not.toHaveBeenCalled();
        expect(callbacks.onChunk).not.toHaveBeenCalled();
        expect(callbacks.onError).not.toHaveBeenCalled();
    });
});
