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

/** Stored before GAIA told accounts apart; the server can't pick one out by it. */
const PLACEHOLDER_ID = 'default';

/** How to name an account to the server: its stable id, or its address while the id is the placeholder. */
export function accountRef(account: ConnectorAccount): string {
    return account.account_id === PLACEHOLDER_ID && account.email ? account.email : account.account_id;
}

/** The account `ref` names, by stable id or by address. */
function findAccount(accounts: ConnectorAccount[], ref: string): ConnectorAccount | undefined {
    const address = ref.toLowerCase();
    return accounts.find((a) => a.account_id === ref || (!!a.email && a.email.toLowerCase() === address));
}

const isOk = (account: ConnectorAccount) => account.status === 'ok';

/** What a chat card offers for one provider. */
export interface ConnectOffer {
    /** Sign in to this account again, named on its button; null keeps the plain Connect button. */
    account: ConnectorAccount | null;
    /** "Use a different account" sits beside the offer. */
    otherAccount: boolean;
}

/**
 * The card's offer: the account the failed call named (`named`, an id or an
 * address); else the only account, when it needs signing in; else the plain
 * Connect button, with "Use a different account" once there are several.
 * It never guesses which of several accounts failed, so one-account users see
 * what they always saw.
 */
export function reconnectOffer(row: ConnectorListEntry | undefined, named?: string): ConnectOffer {
    const accounts = accountsOf(row);
    const account = (named ? findAccount(accounts, named) : undefined)
        ?? (accounts.length === 1 && needsSignIn(accounts[0]) ? accounts[0] : null);
    return { account, otherAccount: account !== null || accounts.length > 1 };
}

/**
 * Whether the sign-in a card asked for has happened, comparing the connector
 * now with `before`, as the card first saw it: the account it offered works
 * again, or a new working account appeared. With nothing offered, any working
 * account counts, except one that already worked among several.
 */
export function signInFixed(
    row: ConnectorListEntry | undefined,
    before: ConnectorListEntry | undefined,
    named?: string,
): boolean {
    if (!row || 'error' in row) return false;
    // A connector that predates per-account state says only whether it is set up.
    if (!row.accounts) return row.configured;
    const then = accountsOf(before);
    const known = new Set(then.map(accountRef));
    const working = row.accounts.filter(isOk);
    if (working.some((a) => !known.has(accountRef(a)))) return true;
    const offered = reconnectOffer(before, named).account;
    if (offered) return working.some((a) => accountRef(a) === accountRef(offered));
    if (then.length <= 1) return working.length > 0;
    const workedThen = new Set(then.filter(isOk).map(accountRef));
    return working.some((a) => !workedThen.has(accountRef(a)));
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

/** The accounts a whole-connector disconnect would remove, when the server refused it for having several. */
export function accountChoices(error: unknown): string[] | null {
    const e = error as { httpStatus?: number; body?: Record<string, unknown> | null } | null;
    const choices = e?.httpStatus === 409 && e.body?.error === 'account_choice_required' ? e.body.choices : null;
    return Array.isArray(choices) ? choices.map(String) : null;
}

/** An account signed in since `before` that GAIA won't use unless asked: not the default. */
export function addedBesideDefault(
    row: ConnectorListEntry | undefined,
    before: ConnectorListEntry | undefined,
): ConnectorAccount | null {
    const known = new Set(accountsOf(before).map(accountRef));
    return accountsOf(row).find((a) => isOk(a) && !a.is_default && !known.has(accountRef(a))) ?? null;
}
