// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it, vi } from 'vitest';
import {
    buildPersonalizePrompt, personalizeSource, personalizeSourceKind, PERSONALIZE_EVENT, requestPersonalize,
} from '../connectorPersonalize';

describe('connectorPersonalize', () => {
    it('names the right data for known connectors', () => {
        expect(personalizeSource('google', 'Google')).toMatch(/Gmail/);
        expect(personalizeSource('microsoft', 'Microsoft')).toMatch(/Outlook/);
        expect(personalizeSource('slack', 'Slack')).toMatch(/Slack/);
        expect(personalizeSource('mcp-github', 'GitHub')).toMatch(/review requests/);
        expect(personalizeSource('mcp-slack', 'Slack')).toMatch(/channels/);
    });

    it('names the source kind for on-screen copy', () => {
        expect(personalizeSourceKind('mcp-slack')).toBe('slack');
        expect(personalizeSourceKind('microsoft_work')).toBe('microsoft');
        expect(personalizeSourceKind('mcp-notion')).toBeNull();
    });

    it('falls back to the display name for other connectors', () => {
        expect(personalizeSource('mcp-notion', 'Notion')).toBe('my recent activity in Notion');
    });

    it('builds a read-only prompt that saves memory and asks before scheduling', () => {
        const prompt = buildPersonalizePrompt({ connectorId: 'google', displayName: 'Google' });
        expect(prompt).toMatch(/I just connected Google/);
        expect(prompt).toMatch(/Read only/);
        expect(prompt).toMatch(/memory/);
        expect(prompt).toMatch(/Don't create any schedules yet/);
    });

    it('dispatches the personalize event with the request', () => {
        const handler = vi.fn();
        window.addEventListener(PERSONALIZE_EVENT, handler);
        requestPersonalize({ connectorId: 'slack', displayName: 'Slack' });
        window.removeEventListener(PERSONALIZE_EVENT, handler);
        expect((handler.mock.calls[0][0] as CustomEvent).detail).toEqual({ connectorId: 'slack', displayName: 'Slack' });
    });
});
