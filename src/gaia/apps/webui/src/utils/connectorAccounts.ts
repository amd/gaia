// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ConnectorAccount, ConnectorListEntry, ConnectorRow } from '../types';

export function accountsOf(row: ConnectorListEntry | undefined): ConnectorAccount[] {
    return row && !('error' in row) ? row.accounts ?? [] : [];
}

export function needsSignIn(account: ConnectorAccount): boolean {
    return account.status !== 'ok';
}

/** Settings lists accounts one per row once there are two, or one has stopped working. */
export function showsAccountList(connector: Pick<ConnectorRow, 'accounts'>): boolean {
    const accounts = connector.accounts ?? [];
    return accounts.length > 1 || accounts.some(needsSignIn);
}

/**
 * The accounts a chat card offers to sign in to again, beside "Use a different
 * account". `null` keeps the card's plain Connect button: no account yet, or
 * exactly one that works, so a one-account user sees what they always saw.
 */
export function reconnectOffer(row: ConnectorListEntry | undefined): ConnectorAccount[] | null {
    const accounts = accountsOf(row);
    const failing = accounts.filter(needsSignIn);
    if (failing.length > 0) return failing;
    if (accounts.length < 2) return null;
    return [accounts.find((a) => a.is_default) ?? accounts[0]];
}

/** Cut a long address in its middle, keeping the domain whole when it is short. */
export function middleCut(text: string, max = 32): string {
    if (text.length <= max) return text;
    const at = text.lastIndexOf('@');
    const domain = at > 0 ? text.length - at : 0;
    const tail = domain > 0 && domain <= max / 2 ? domain : Math.floor((max - 1) / 2);
    // Drop a dot left dangling before the cut, so it never reads as "name.…".
    return `${text.slice(0, max - 1 - tail).replace(/[.\-_]+$/, '')}…${text.slice(text.length - tail)}`;
}
