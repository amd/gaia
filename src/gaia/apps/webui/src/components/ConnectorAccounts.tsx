// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { AlertCircle, CheckCircle2, Loader2 } from 'lucide-react';

import * as api from '../services/api';
import { useT } from '../i18n/useT';
import type { ConnectorAccount, ConnectorRow } from '../types';
import { accountRef, middleCut, needsSignIn } from '../utils/connectorAccounts';
import { ConfirmSheet } from './setup/ConfirmSheet';
import { ltrIn } from './setup/format';

/** An address kept left-to-right in any language, cut in its middle when long, whole on hover. */
export function AccountAddress({ email, max }: { email: string; max?: number }) {
    const shown = middleCut(email, max);
    return (
        <bdi dir="ltr" className="account-address" title={shown === email ? undefined : email}>
            {shown}
        </bdi>
    );
}

export function AccountStatus({ account }: { account: ConnectorAccount }) {
    const t = useT();
    if (account.status === 'ok') {
        return (
            <span className="connector-status ok">
                <CheckCircle2 size={12} aria-hidden="true" />
                {t('settings.connectors.connected')}
            </span>
        );
    }
    return (
        <span className="connector-status failed">
            <AlertCircle size={12} aria-hidden="true" />
            {t(account.status === 'reauth_required' ? 'settings.connectors.signInExpired' : 'settings.connectors.notWorking')}
        </span>
    );
}

/** Each signed-in account of an OAuth connector, with its own Reconnect, default choice and Disconnect. */
export function ConnectorAccountList({
    connector,
    onChanged,
    onSignIn,
}: {
    connector: ConnectorRow;
    onChanged: () => void;
    /** Start sign-in again for one stored account. */
    onSignIn: (accountId: string) => Promise<void>;
}) {
    const t = useT();
    const accounts = connector.accounts ?? [];
    const name = connector.display_name;
    const [busy, setBusy] = useState<string | null>(null);
    const [err, setErr] = useState<string | null>(null);
    const [asking, setAsking] = useState<ConnectorAccount | null>(null);

    const run = async (key: string, action: () => Promise<unknown>) => {
        setBusy(key);
        setErr(null);
        try {
            await action();
            return true;
        } catch (e) {
            setErr(e instanceof Error ? e.message : String(e));
            return false;
        } finally {
            setBusy(null);
        }
    };

    const makeDefault = (account: ConnectorAccount) =>
        void run(`default:${accountRef(account)}`, async () => {
            await api.setDefaultConnectorAccount(connector.id, accountRef(account));
            onChanged();
        });

    const disconnect = async (account: ConnectorAccount) => {
        const done = await run('disconnect', async () => {
            await api.disconnectConnectorAccount(connector.id, accountRef(account));
            onChanged();
        });
        if (done) setAsking(null);
    };

    return (
        <div className="connector-accounts">
            <ul aria-label={t('settings.connectors.accountList', { name })}>
                {accounts.map((account, i) => {
                    const ref = accountRef(account);
                    const address = { address: ltrIn(t.locale, account.email || account.account_id) };
                    return (
                        <li key={ref === 'default' ? `default:${i}` : ref} className={`connector-account${needsSignIn(account) ? ' connector-account--attention' : ''}`}>
                            <span className="connector-account-who">
                                <AccountAddress email={account.email || account.account_id} />
                                {account.is_default && accounts.length > 1 && (
                                    <span className="connector-account-default">{t('settings.connectors.default')}</span>
                                )}
                            </span>
                            <AccountStatus account={account} />
                            <span className="connector-account-actions">
                                {needsSignIn(account) && (
                                    <button
                                        type="button"
                                        className="btn-primary"
                                        disabled={busy !== null}
                                        aria-label={t('settings.connectors.reconnectNamed', address)}
                                        onClick={() => void run(`signin:${ref}`, () => onSignIn(ref))}
                                    >
                                        {busy === `signin:${ref}` && <Loader2 size={12} className="spin" />}
                                        {t('settings.connectors.reconnect')}
                                    </button>
                                )}
                                {!account.is_default && account.status === 'ok' && (
                                    <button
                                        type="button"
                                        className="btn-secondary"
                                        disabled={busy !== null}
                                        aria-label={t('settings.connectors.makeDefaultNamed', address)}
                                        onClick={() => makeDefault(account)}
                                    >
                                        {t('settings.connectors.makeDefault')}
                                    </button>
                                )}
                                <button
                                    type="button"
                                    className="btn-secondary"
                                    disabled={busy !== null}
                                    aria-label={t('settings.connectors.disconnectNamed', address)}
                                    onClick={() => setAsking(account)}
                                >
                                    {t('settings.connectors.disconnect')}
                                </button>
                            </span>
                        </li>
                    );
                })}
            </ul>
            {accounts.length > 1 && <p className="connector-account-hint">{t('settings.connectors.defaultHint')}</p>}
            {err && !asking && (
                <div className="configure-error" role="alert">
                    <AlertCircle size={12} /> {err}
                </div>
            )}
            {asking && (
                <ConfirmSheet
                    title={t('settings.connectors.disconnectAccountAsk', { address: ltrIn(t.locale, asking.email || asking.account_id) })}
                    confirmLabel={t('settings.connectors.disconnect')}
                    cancelLabel={t('settings.connectors.cancel')}
                    onConfirm={() => void disconnect(asking)}
                    onCancel={() => { setAsking(null); setErr(null); }}
                    danger
                    busy={busy === 'disconnect'}
                >
                    <p>{t(accounts.length > 1 ? 'settings.connectors.disconnectAccountOthers' : 'settings.connectors.disconnectAccountLast', { name })}</p>
                    {err && <p className="configure-error" role="alert">{err}</p>}
                </ConfirmSheet>
            )}
        </div>
    );
}
