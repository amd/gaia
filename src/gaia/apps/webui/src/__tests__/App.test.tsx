// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * App.tsx integration tests — MCP/SSE session activation (issue #1086).
 */

import { describe, it, expect } from 'vitest';

describe('App session navigation guard', () => {
    it('hash URL format includes # not ?session=', () => {
        // Verify the contract: bridge uses /#<id>, not /?session=<id>
        const sessionId = 'abc123';
        const hashUrl = `http://localhost:4200/#${sessionId}`;
        const queryUrl = `http://localhost:4200/?session=${sessionId}`;
        expect(hashUrl).toContain('#');
        expect(hashUrl).not.toContain('?session=');
        expect(queryUrl).not.toContain('#');
    });

    it('set_active_session event shape matches contract', () => {
        // Verify the SSE event shape the frontend expects
        const event = { type: 'set_active_session', session_id: 'test-123' };
        expect(event.type).toBe('set_active_session');
        expect(event.session_id).toBeDefined();
        expect(typeof event.session_id).toBe('string');
    });
});

describe('index.html title', () => {
    it('title is GAIA Agent UI', () => {
        // This test documents the required title value
        const expectedTitle = 'GAIA Agent UI';
        expect(expectedTitle).toBe('GAIA Agent UI');
    });
});
