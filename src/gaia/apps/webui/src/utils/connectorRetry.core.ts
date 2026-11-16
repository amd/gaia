// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Which replies are connector sign-in errors, and whether the account they name is connected now. Pure, so the phone app can import it. */

import type { ConnectorListEntry } from '../types';

/** Match the canonical prefixes the connectors framework emits. The
 *  prefixes are stable (see ``connectors/formatting.py``); fuzzy
 *  fallbacks handle agent-specific override messages.
 */
export function isAuthRequiredMessage(content: string): boolean {
    if (!content) return false;
    if (content.includes('NOT_CONNECTED:')) return true;
    if (content.includes('AGENT_NOT_GRANTED:')) return true;
    if (content.includes('AUTH_REQUIRED:')) return true;
    // Agent-specific override (``_AGENT_GRANT_MIGRATION_MESSAGES`` for
    // installed:email). Lowercased substring check so wording tweaks
    // upstream don't silently break the detection.
    const lower = content.toLowerCase();
    if (
        lower.includes('connectors → google') ||
        lower.includes('connections → google') ||
        lower.includes('email agent needs additional google permissions')
    ) {
        return true;
    }
    // Microsoft-side error messages from format_connector_error. Each
    // connector id needs its own phrase check below — "microsoft_work is not
    // currently connected" does NOT contain "microsoft is not currently
    // connected" as a substring.
    if (
        lower.includes('connectors → microsoft') ||
        lower.includes('connections → microsoft') ||
        lower.includes('microsoft is not currently connected') ||
        lower.includes('microsoft_work is not currently connected')
    ) {
        return true;
    }
    return false;
}

/** The three mailbox connectors the email agent can grant-check (#2629):
 *  ``google`` (Gmail), ``microsoft`` (personal Outlook), and
 *  ``microsoft_work`` (work Microsoft 365 / Entra). */
export type EmailProvider = 'google' | 'microsoft' | 'microsoft_work';

/**
 * Detect which provider(s) an error message references.
 *
 * Returns:
 * - ``'google'``         — only Google connector mentioned
 * - ``'microsoft'``      — only the personal Outlook connector mentioned
 * - ``'microsoft_work'`` — only the work Microsoft 365 connector mentioned
 * - ``'all'``            — ambiguous / no specific provider found → show all three
 *
 * The work connector must be checked BEFORE the generic Microsoft check:
 * ``format_connector_error`` embeds the literal connector id, and
 * "microsoft_work" contains "microsoft" as a substring, so checking in the
 * wrong order would always classify a work-mailbox error as personal.
 */
export function detectProvider(content: string): EmailProvider | 'all' {
    if (!content) return 'all';
    const lower = content.toLowerCase();
    const mentionsGoogle = lower.includes('google') || lower.includes('gmail');
    const mentionsWork =
        lower.includes('microsoft_work') ||
        lower.includes('microsoft 365') ||
        lower.includes('office365') ||
        lower.includes('o365');
    const mentionsPersonal =
        !mentionsWork && (lower.includes('microsoft') || lower.includes('outlook'));

    const hits = [mentionsGoogle, mentionsPersonal, mentionsWork].filter(Boolean).length;
    if (hits !== 1) return 'all';
    if (mentionsGoogle) return 'google';
    if (mentionsWork) return 'microsoft_work';
    return 'microsoft';
}

/**
 * Given the error message and the current connector rows, decide whether the
 * provider the message referenced is now connected. Pure + exported for tests.
 *
 * ``detectProvider`` returns 'google' | 'microsoft' | 'microsoft_work' | 'all'
 * (ambiguous). For an ambiguous message, any of the three connecting is
 * enough to retry.
 */
export function referencedProviderConnected(
    content: string,
    connectors: ConnectorListEntry[],
    providers: string[] = [],
): boolean {
    const isConfigured = (id: string) =>
        connectors.some((c) => c.id === id && !('error' in c) && c.configured);
    // A Connect card names its connectors outright; no wording to read.
    if (providers.length > 0) return providers.some(isConfigured);
    const provider = detectProvider(content);
    if (provider === 'google') return isConfigured('google');
    if (provider === 'microsoft') return isConfigured('microsoft');
    if (provider === 'microsoft_work') return isConfigured('microsoft_work');
    return isConfigured('google') || isConfigured('microsoft') || isConfigured('microsoft_work');
}

/**
 * Human label for the connected provider, for the banner copy. An ambiguous
 * message resolves to whichever provider is actually configured, preferring
 * Google, then Microsoft, then Microsoft 365 (the connectors' canonical
 * display order).
 */
export function connectedProviderLabel(
    content: string,
    connectors: ConnectorListEntry[],
    providers: string[] = [],
): string {
    const isConfigured = (id: string) =>
        connectors.some((c) => c.id === id && !('error' in c) && c.configured);
    const named = providers.find(isConfigured);
    if (named) {
        const row = connectors.find((c) => c.id === named && !('error' in c));
        return row && !('error' in row) ? row.display_name : named;
    }
    const provider = detectProvider(content);
    if (provider === 'google') return 'Google';
    if (provider === 'microsoft') return 'Microsoft';
    if (provider === 'microsoft_work') return 'Microsoft 365';
    if (isConfigured('google')) return 'Google';
    if (isConfigured('microsoft')) return 'Microsoft';
    if (isConfigured('microsoft_work')) return 'Microsoft 365';
    return 'Google';
}
