// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect } from 'vitest';
import {
    detectProvider,
    emailAgentGrantIds,
    isAuthRequiredMessage,
} from '../email/EmailConnectCta';

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

    // Microsoft-side detection (#1770)
    it('returns true for NOT_CONNECTED: microsoft prefix', () => {
        expect(
            isAuthRequiredMessage('NOT_CONNECTED: microsoft is not currently connected.')
        ).toBe(true);
    });

    it('returns true when message mentions connectors → microsoft', () => {
        expect(
            isAuthRequiredMessage('Please go to Settings → Connectors → Microsoft and reconnect.')
        ).toBe(true);
    });

    it('returns true when message mentions connections → microsoft (case-insensitive)', () => {
        expect(
            isAuthRequiredMessage('Visit Settings → Connections → Microsoft to fix this.')
        ).toBe(true);
    });
});

describe('detectProvider', () => {
    it('returns google when only google is mentioned', () => {
        expect(detectProvider('NOT_CONNECTED: google is not currently connected.')).toBe('google');
    });

    it('returns microsoft when only microsoft is mentioned', () => {
        expect(detectProvider('NOT_CONNECTED: microsoft is not currently connected.')).toBe('microsoft');
    });

    it('returns google when gmail is mentioned', () => {
        expect(detectProvider('Please reconnect Gmail to continue.')).toBe('google');
    });

    it('returns microsoft when outlook is mentioned', () => {
        expect(detectProvider('Please reconnect Outlook to continue.')).toBe('microsoft');
    });

    it('returns both when both providers are mentioned', () => {
        expect(detectProvider('Google and Microsoft accounts need reconnecting.')).toBe('both');
    });

    it('returns both for empty string', () => {
        expect(detectProvider('')).toBe('both');
    });

    it('returns both for an ambiguous message without provider names', () => {
        expect(detectProvider('AGENT_NOT_GRANTED: this agent is not granted.')).toBe('both');
    });
});

/**
 * #2117 consent-scoping: the email connect CTA must grant ONLY the email agent,
 * never co-installed agents that also declare the connector — granting a sibling
 * agent its mailbox scopes here would bypass the per-agent consent surface.
 */
describe('emailAgentGrantIds', () => {
    const email = {
        id: 'email',
        namespaced_agent_id: 'installed:email',
        required_connections: [
            { connector_id: 'google' },
            { connector_id: 'microsoft' },
        ],
    };
    const connectorsDemo = {
        id: 'connectors-demo',
        namespaced_agent_id: 'installed:connectors-demo',
        required_connections: [{ connector_id: 'google' }],
    };

    it('grants only the email agent, not co-installed agents that declare the connector', () => {
        expect(emailAgentGrantIds([email, connectorsDemo], 'google')).toEqual([
            'installed:email',
        ]);
    });

    it('does not grant a co-installed agent even when the email agent is absent', () => {
        // No silent fall-through to a sibling agent — returns empty, not the demo.
        expect(emailAgentGrantIds([connectorsDemo], 'google')).toEqual([]);
    });

    it('returns the email agent for microsoft too', () => {
        expect(emailAgentGrantIds([email, connectorsDemo], 'microsoft')).toEqual([
            'installed:email',
        ]);
    });

    it('returns empty when the email agent does not declare the connector', () => {
        const emailNoGoogle = {
            id: 'email',
            namespaced_agent_id: 'installed:email',
            required_connections: [{ connector_id: 'microsoft' }],
        };
        expect(emailAgentGrantIds([emailNoGoogle], 'google')).toEqual([]);
    });

    it('is robust to a builtin: namespace prefix', () => {
        const builtinEmail = {
            id: 'email',
            namespaced_agent_id: 'builtin:email',
            required_connections: [{ connector_id: 'google' }],
        };
        expect(emailAgentGrantIds([builtinEmail], 'google')).toEqual(['builtin:email']);
    });

    it('returns empty for an empty agent list', () => {
        expect(emailAgentGrantIds([], 'google')).toEqual([]);
    });
});
