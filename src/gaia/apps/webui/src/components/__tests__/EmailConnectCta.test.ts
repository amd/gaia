// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect } from 'vitest';
import {
    detectProvider,
    mailGrantIds,
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

    it('returns all when both google and microsoft are mentioned', () => {
        expect(detectProvider('Google and Microsoft accounts need reconnecting.')).toBe('all');
    });

    it('returns all for empty string', () => {
        expect(detectProvider('')).toBe('all');
    });

    it('returns all for an ambiguous message without provider names', () => {
        expect(detectProvider('AGENT_NOT_GRANTED: this agent is not granted.')).toBe('all');
    });

    // GAIA reads Gmail and Outlook only, so work wording gets the Microsoft button.
    it.each([
        'NOT_CONNECTED: microsoft_work is not currently connected.',
        'Your Microsoft 365 account needs to be reconnected.',
        'Please reconnect your office365 mailbox.',
    ])('treats work-account wording as Microsoft: %s', (message) => {
        expect(detectProvider(message)).toBe('microsoft');
    });
});

/**
 * #2117 consent-scoping: the connect CTA grants ONLY GAIA, never another
 * installed agent that also declares the connector.
 */
describe('mailGrantIds', () => {
    const gaia = {
        id: 'gaia',
        namespaced_agent_id: 'installed:gaia',
        required_connections: [
            { connector_id: 'google' },
            { connector_id: 'microsoft' },
        ],
    };
    const customAgent = {
        id: 'my-agent',
        namespaced_agent_id: 'installed:my-agent',
        required_connections: [{ connector_id: 'google' }],
    };

    it('grants only GAIA, not another agent that declares the connector', () => {
        expect(mailGrantIds([gaia, customAgent], 'google')).toEqual(['installed:gaia']);
    });

    it('does not fall through to another agent when GAIA is absent', () => {
        expect(mailGrantIds([customAgent], 'google')).toEqual([]);
    });

    it('grants GAIA for microsoft too', () => {
        expect(mailGrantIds([gaia, customAgent], 'microsoft')).toEqual(['installed:gaia']);
    });

    it('returns empty when GAIA does not declare the connector', () => {
        const noGoogle = { ...gaia, required_connections: [{ connector_id: 'microsoft' }] };
        expect(mailGrantIds([noGoogle], 'google')).toEqual([]);
    });

    it('does not grant the retired email agent', () => {
        const email = { ...gaia, id: 'email', namespaced_agent_id: 'installed:email' };
        expect(mailGrantIds([email], 'google')).toEqual([]);
    });

    it('returns empty for an empty agent list', () => {
        expect(mailGrantIds([], 'google')).toEqual([]);
    });
});
