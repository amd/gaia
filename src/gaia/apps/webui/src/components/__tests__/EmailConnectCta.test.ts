// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect } from 'vitest';
import { isAuthRequiredMessage } from '../email/EmailConnectCta';

/**
 * Tests for isAuthRequiredMessage — the CTA detection function that decides
 * whether to mount EmailConnectCta next to an assistant message.
 *
 * Root cause 2 of #1592: email tools used str(exc) which drops the canonical
 * prefix, so the CTA never fired. After the fix, format_connector_error is
 * used, which always produces one of the recognised prefixes.
 */

describe('isAuthRequiredMessage', () => {
    // Canonical prefix tests (these are what format_connector_error produces)
    it('returns true for NOT_CONNECTED: prefix', () => {
        expect(
            isAuthRequiredMessage('NOT_CONNECTED: google is not currently connected.')
        ).toBe(true);
    });

    it('returns true for AGENT_NOT_GRANTED: prefix', () => {
        expect(
            isAuthRequiredMessage(
                'AGENT_NOT_GRANTED: this agent isn\'t granted these scopes on google: gmail.readonly.'
            )
        ).toBe(true);
    });

    it('returns true for AUTH_REQUIRED: prefix', () => {
        expect(
            isAuthRequiredMessage('AUTH_REQUIRED: Authentication required for google')
        ).toBe(true);
    });

    // Agent-specific override message from _AGENT_GRANT_MIGRATION_MESSAGES
    it('returns true for email agent migration message', () => {
        expect(
            isAuthRequiredMessage(
                'Email agent needs additional Google permissions (gmail.modify, gmail.send, calendar.events). ' +
                'Open Settings → Connectors → Google → Reconnect to grant the missing scopes.'
            )
        ).toBe(true);
    });

    // Fallback fuzzy matches
    it('returns true when message mentions connectors → google', () => {
        expect(
            isAuthRequiredMessage('Please go to Settings → Connectors → Google and reconnect.')
        ).toBe(true);
    });

    it('returns true when message mentions connections → google (case-insensitive)', () => {
        expect(
            isAuthRequiredMessage('Visit Settings → Connections → Google to fix this.')
        ).toBe(true);
    });

    // Negative cases (should NOT trigger the CTA)
    it('returns false for a normal assistant message', () => {
        expect(isAuthRequiredMessage('Here are your emails for today.')).toBe(false);
    });

    it('returns false for empty string', () => {
        expect(isAuthRequiredMessage('' as string)).toBe(false);
    });

    it('returns false for generic error without prefix', () => {
        // This is what str(exc) produced BEFORE the fix — no prefix, no CTA.
        expect(
            isAuthRequiredMessage(
                "Agent 'installed:email' has no grant for google. Grant the required scopes..."
            )
        ).toBe(false);
    });

    it('returns false for an unrelated error message', () => {
        expect(
            isAuthRequiredMessage('Failed to connect to Lemonade server at port 13305.')
        ).toBe(false);
    });
});
