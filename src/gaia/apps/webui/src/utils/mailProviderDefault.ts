// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Mail providers the backend accepts (backend pattern: ^(google|microsoft)$). */
export const MAIL_PROVIDERS = ['google', 'microsoft'] as const;
export type MailProvider = (typeof MAIL_PROVIDERS)[number];

/**
 * Resolve the mail provider to use for a new email-triage session.
 *
 * AC1 — exactly one of google/microsoft is connected → auto-select it.
 * AC2 — both connected (or neither) → return the caller's explicit choice (may be undefined).
 *
 * @param connectedProviders  All provider ids currently in the connections store.
 * @param explicitChoice      Value the user explicitly selected (or undefined if none).
 */
export function resolveMailProvider(
    connectedProviders: string[],
    explicitChoice: string | undefined,
): string | undefined {
    const mailConnected = connectedProviders.filter(
        (p): p is MailProvider => (MAIL_PROVIDERS as readonly string[]).includes(p),
    );
    if (mailConnected.length === 1) return mailConnected[0];
    return explicitChoice;
}
