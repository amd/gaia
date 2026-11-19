// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Which replies are connector sign-in errors, and whether the account they name is connected now. Pure, so the phone app can import it. */

import type { ConnectorListEntry } from '../types';
import { signInFixed } from './connectorAccounts';

/** Match the canonical prefixes the connectors framework emits. The
 *  prefixes are stable (see ``connectors/formatting.py``); fuzzy
 *  fallbacks handle agent-specific override messages.
 */
export function isAuthRequiredMessage(content: string): boolean {
    if (!content) return false;
    if (content.includes('NOT_CONNECTED:')) return true;
    if (content.includes('AGENT_NOT_GRANTED:')) return true;
    if (content.includes('AUTH_REQUIRED:')) return true;
    // Lowercased substring check so wording tweaks upstream don't silently
    // break the detection.
    const lower = content.toLowerCase();
    if (
        lower.includes('connectors → google') ||
        lower.includes('connections → google')
    ) {
        return true;
    }
    if (
        lower.includes('connectors → microsoft') ||
        lower.includes('connections → microsoft') ||
        lower.includes('microsoft is not currently connected')
    ) {
        return true;
    }
    return false;
}

/** The mailbox connectors GAIA reads: ``google`` (Gmail) and ``microsoft`` (Outlook). */
export type EmailProvider = 'google' | 'microsoft';

/**
 * Which provider an error message names: ``'google'``, ``'microsoft'``, or
 * ``'all'`` when it names both or neither.
 */
export function detectProvider(content: string): EmailProvider | 'all' {
    if (!content) return 'all';
    const lower = content.toLowerCase();
    const mentionsGoogle = lower.includes('google') || lower.includes('gmail');
    const mentionsMicrosoft =
        lower.includes('microsoft') ||
        lower.includes('outlook') ||
        lower.includes('office365') ||
        lower.includes('o365');
    if (mentionsGoogle === mentionsMicrosoft) return 'all';
    return mentionsGoogle ? 'google' : 'microsoft';
}

/**
 * Given the error message and the current connector rows, decide whether the
 * provider the message referenced is now connected. Pure + exported for tests.
 *
 * ``detectProvider`` returns 'google' | 'microsoft' | 'all' (ambiguous). For
 * an ambiguous message, either one connecting is enough to retry.
 */
export function referencedProviderConnected(
    content: string,
    connectors: ConnectorListEntry[],
    providers: string[] = [],
    before: ConnectorListEntry[] = [],
    accounts: Record<string, string> = {},
): boolean {
    const isFixed = fixedIn(connectors, before, accounts);
    // A Connect card names its connectors outright; no wording to read.
    if (providers.length > 0) return providers.some(isFixed);
    const provider = detectProvider(content);
    if (provider === 'google') return isFixed('google');
    if (provider === 'microsoft') return isFixed('microsoft');
    return isFixed('google') || isFixed('microsoft');
}

/** Whether each provider's sign-in has happened since `before`; `accounts` names the account a card offered. */
function fixedIn(connectors: ConnectorListEntry[], before: ConnectorListEntry[], accounts: Record<string, string>) {
    return (id: string) => signInFixed(
        connectors.find((c) => c.id === id),
        before.find((c) => c.id === id),
        accounts[id],
    );
}

/**
 * Human label for the connected provider, for the banner copy. An ambiguous
 * message resolves to whichever provider is actually configured, preferring
 * Google, then Microsoft (the connectors' canonical
 * display order).
 */
export function connectedProviderLabel(
    content: string,
    connectors: ConnectorListEntry[],
    providers: string[] = [],
    before: ConnectorListEntry[] = [],
    accounts: Record<string, string> = {},
): string {
    const isFixed = fixedIn(connectors, before, accounts);
    const named = providers.find(isFixed);
    if (named) {
        const row = connectors.find((c) => c.id === named && !('error' in c));
        return row && !('error' in row) ? row.display_name : named;
    }
    const provider = detectProvider(content);
    if (provider === 'google') return 'Google';
    if (provider === 'microsoft') return 'Microsoft';
    if (isFixed('google')) return 'Google';
    if (isFixed('microsoft')) return 'Microsoft';
    return 'Google';
}
