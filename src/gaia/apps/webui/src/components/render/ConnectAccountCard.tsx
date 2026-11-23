// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { CheckCircle2, ExternalLink, Link2, Loader2 } from 'lucide-react';
import * as api from '../../services/api';
import { useChatStore } from '../../stores/chatStore';
import { useT } from '../../i18n/useT';
import { useConnectorsSSE } from '../../hooks/useConnectorsSSE';
import { log } from '../../utils/logger';
import type { ConnectorAccount, ConnectorListEntry, Message } from '../../types';
import { accountRef, accountsOf, addedBesideDefault, middleCut, reconnectOffer, signInFixed, type ConnectOffer } from '../../utils/connectorAccounts';
import { useConnectorPoll } from '../../hooks/useConnectorPoll';
import { ltrIn } from '../setup/format';
import { UnsupportedCard } from './UnsupportedCard';
import {
    CONNECT_ACCOUNT_RENDER,
    isConnectAccountPayload,
    type ConnectAccountPayload,
} from '../../utils/connectCards';

/**
 * ConnectAccountCard — the `connect_account` surface.
 *
 * A mail or calendar tool that finds no usable account sends this card with
 * its result, so the Connect button is on screen whatever the model writes.
 * Pressing it starts sign-in for the access the failed call needs (reading
 * mail asks only to read it), and gives the asking agent access in the same
 * step. A card that names no access asks for what the agent declares.
 */

export { CONNECT_ACCOUNT_RENDER, isConnectAccountPayload };

/**
 * What a Connect press asks for: what the call needs, plus what the connection
 * already carries, so signing in again never takes access away. Empty when the
 * card names nothing, so the backend asks for what the agent declares.
 */
export function requestScopes(needed: string[] | undefined, held: string[] | undefined): string[] {
    if (!needed?.length) return [];
    return [...new Set([...(held ?? []), ...needed])];
}

/** The providers whose becoming connected is the fix: those not connected when the tool ran. */
function awaited(card: ConnectAccountPayload): string[] {
    return card.providers.filter((p) => !card.reconnect?.includes(p));
}

/**
 * The connectors a message's Connect card is waiting on; empty when it has no
 * card, or when every account on it was connected already and needs signing
 * in again, which the connector list cannot show.
 */
export function connectCardProviders(message: Pick<Message, 'cards'> | undefined): string[] {
    const found = new Set<string>();
    for (const card of message?.cards ?? []) {
        if (card.render === CONNECT_ACCOUNT_RENDER && isConnectAccountPayload(card.data)) {
            awaited(card.data).forEach((p) => found.add(p));
        }
    }
    return [...found];
}

/** Per provider, the account a message's Connect card offered to sign in to again. */
export function connectCardAccounts(message: Pick<Message, 'cards'> | undefined): Record<string, string> {
    const named: Record<string, string> = {};
    for (const card of message?.cards ?? []) {
        if (card.render === CONNECT_ACCOUNT_RENDER && isConnectAccountPayload(card.data)) {
            Object.assign(named, card.data.account);
        }
    }
    return named;
}

/** Whether a message carries a Connect card at all. */
export function hasConnectCard(message: Pick<Message, 'cards'> | undefined): boolean {
    return (message?.cards ?? []).some(
        (card) => card.render === CONNECT_ACCOUNT_RENDER && isConnectAccountPayload(card.data),
    );
}

/**
 * The agent to give access when sign-in completes: only the one that asked,
 * and only when it declares this connector. Anything else connects without a
 * grant rather than handing access to an agent nobody chose.
 */
export function grantIdsFor(
    agents: Array<{
        namespaced_agent_id?: string;
        required_connections?: Array<{ connector_id: string }>;
    }>,
    agentId: string | undefined,
    connectorId: string,
): string[] {
    if (!agentId) return [];
    return agents
        .filter(
            (a) =>
                a.namespaced_agent_id === agentId &&
                (a.required_connections?.some((rc) => rc.connector_id === connectorId) ?? false),
        )
        .map((a) => a.namespaced_agent_id!);
}

// Brand names, the same in every language.
const PROVIDER_NAMES: Record<string, string> = {
    google: 'Google',
    microsoft: 'Microsoft',
    microsoft_work: 'Microsoft 365',
};

function openAuthUrl(url: string): void {
    const bridge = (window as unknown as { gaia?: { openExternal?: (url: string) => void } }).gaia;
    if (bridge?.openExternal) bridge.openExternal(url);
    else window.open(url, '_blank', 'noopener');
}

/** Sign-in refused because this PC has no OAuth client for the provider yet. */
function isUnconfigured(e: unknown): boolean {
    const err = e as { httpStatus?: number; body?: Record<string, unknown> | null };
    return err?.httpStatus === 503 && err.body?.error === 'not_configured';
}

function ProviderRow({
    connectorId,
    name,
    connected,
    saysConnected,
    added,
    offer,
    needsSetup,
    grantAgents,
    scopes,
    onOpened,
}: {
    connectorId: string;
    name: string;
    connected: boolean;
    /** Say the provider is connected: never while the account that failed may still be broken. */
    saysConnected: boolean;
    /** The account just signed in beside the default, which GAIA uses only when asked. */
    added: ConnectorAccount | null;
    offer: ConnectOffer;
    /** No OAuth client on this PC yet, so sign-in cannot start. */
    needsSetup: boolean;
    grantAgents: string[];
    scopes: string[];
    onOpened: () => void;
}) {
    const t = useT();
    const openSettings = useChatStore((s) => s.openSettings);
    const openConnectorSettings = useChatStore((s) => s.openConnectorSettings);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [setup, setSetup] = useState(false);

    // Set up in Settings since: the sentence no longer applies.
    useEffect(() => {
        if (!needsSetup) setSetup(false);
    }, [needsSetup]);

    const connect = useCallback(async (target?: api.AuthorizeTarget) => {
        if (needsSetup) {
            setError(null);
            setSetup(true);
            return;
        }
        setSetup(false);
        // With no agent to size the request, Settings is where access is chosen.
        if (grantAgents.length === 0) {
            openSettings('connectors');
            return;
        }
        setBusy(true);
        setError(null);
        try {
            // A plain Connect sends the request it always did.
            const flow = target
                ? await api.authorizeConnector(connectorId, scopes, grantAgents, target)
                : await api.authorizeConnector(connectorId, scopes, grantAgents);
            openAuthUrl(flow.authorization_url);
            onOpened();
        } catch (e) {
            if (isUnconfigured(e)) setSetup(true);
            else setError(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(false);
        }
    }, [connectorId, needsSetup, grantAgents, scopes, onOpened, openSettings]);

    const icon = busy
        ? <Loader2 size={12} className="render-connect__spinner" aria-hidden="true" />
        : <ExternalLink size={12} aria-hidden="true" />;
    const label = t(connected ? 'connectCard.reconnect' : 'connectCard.connect', { name });
    const { account } = offer;
    return (
        <div className="render-connect__provider">
            {account ? (
                <button
                    type="button"
                    className="render-connect__button"
                    title={account.email || undefined}
                    onClick={() => void connect({ account: accountRef(account) })}
                    disabled={busy}
                >
                    {icon}
                    <span>{t('connectCard.reconnectAccount', { address: ltrIn(t.locale, middleCut(account.email || account.account_id)) })}</span>
                </button>
            ) : (
                <button
                    type="button"
                    className={`render-connect__button${connected ? ' render-connect__button--quiet' : ''}`}
                    onClick={() => void connect()}
                    disabled={busy}
                >
                    {icon}
                    <span>{label}</span>
                </button>
            )}
            {offer.otherAccount && (
                <button
                    type="button"
                    className="render-connect__button render-connect__button--quiet"
                    onClick={() => void connect({ addAccount: true })}
                    disabled={busy}
                >
                    <span>{t('connectCard.otherAccount')}</span>
                </button>
            )}
            {saysConnected && !account && (
                <span className="render-connect__connected" role="status">
                    <CheckCircle2 size={13} aria-hidden="true" />
                    {added
                        ? t('connectCard.addedAccount', { address: ltrIn(t.locale, middleCut(added.email || added.account_id)), name })
                        : t('connectCard.connected', { name })}
                </span>
            )}
            {error && (
                <div className="render-connect__error" role="alert">{error}</div>
            )}
            {setup && (
                <div className="render-connect__setup" role="status">
                    <span>{t('connectCard.needsSetup', { name })}</span>
                    <button
                        type="button"
                        className="render-connect__settings"
                        onClick={() => openConnectorSettings(connectorId)}
                    >
                        {t('connectCard.setUp', { name })}
                    </button>
                </div>
            )}
        </div>
    );
}

/** Watches for a sign-in this card opened to end without connecting.
 *  Mounted only while one is open: each watcher holds a connection. */
function SignInWatcher({
    providers,
    onEnded,
}: {
    providers: string[];
    onEnded: (why: 'cancelled' | 'failed') => void;
}) {
    useConnectorsSSE(
        useCallback(
            (event) => {
                if (event.reason !== 'oauth_error') return;
                if (!event.connectorId || !providers.includes(event.connectorId)) return;
                onEnded(event.payload.reason === 'cancelled' ? 'cancelled' : 'failed');
            },
            [providers, onEnded],
        ),
    );
    return null;
}

export function ConnectAccountCard({ data }: { data: unknown }) {
    const t = useT();
    const agents = useChatStore((s) => s.agents);
    const openSettings = useChatStore((s) => s.openSettings);
    const [connectors, setConnectors] = useState<ConnectorListEntry[]>([]);
    // The connectors as this card first saw them: only a change since counts as the fix.
    const [before, setBefore] = useState<ConnectorListEntry[] | null>(null);
    const [opened, setOpened] = useState(false);
    const [ended, setEnded] = useState<'cancelled' | 'failed' | null>(null);
    const valid = isConnectAccountPayload(data);

    const refresh = useCallback(() => {
        api.listConnectors()
            .then((res) => {
                const rows = res.connectors || [];
                setConnectors(rows);
                setBefore((prev) => prev ?? rows);
            })
            .catch((err) => log.ui.warn('ConnectAccountCard: connector fetch failed', err));
    }, []);

    // Re-read on mount and when the window regains focus (back from sign-in).
    useEffect(() => {
        if (!valid) return undefined;
        refresh();
        window.addEventListener('focus', refresh);
        return () => window.removeEventListener('focus', refresh);
    }, [valid, refresh]);

    // Settings opens inside the app, so closing it brings no window focus back.
    const settingsOpen = useChatStore((s) => s.settingsSection !== null);
    const settingsWasOpen = useRef(settingsOpen);
    useEffect(() => {
        if (valid && settingsWasOpen.current && !settingsOpen) refresh();
        settingsWasOpen.current = settingsOpen;
    }, [valid, settingsOpen, refresh]);

    const rowOf = (rows: ConnectorListEntry[] | null, id: string) => rows?.find((c) => c.id === id);
    const isFixed = (id: string) =>
        valid && before !== null && signInFixed(rowOf(connectors, id), rowOf(before, id), data.account?.[id]);
    const anyFixed = valid && awaited(data).some(isFixed);

    // While a sign-in tab is open, watch for the account to appear.
    useConnectorPoll(opened && !anyFixed, refresh);

    const onEnded = useCallback((why: 'cancelled' | 'failed') => {
        setOpened(false);
        setEnded(why);
    }, []);

    if (!isConnectAccountPayload(data)) {
        return <UnsupportedCard variant="invalid" render={CONNECT_ACCOUNT_RENDER} data={data} />;
    }

    const isConnected = (id: string) =>
        connectors.some((c) => c.id === id && !('error' in c) && c.configured);
    // With several accounts, a working one says nothing about the one that failed.
    const saysConnected = (id: string) => isFixed(id) || (isConnected(id) && accountsOf(rowOf(connectors, id)).length <= 1);
    const needsSetup = (id: string) =>
        connectors.some(
            (c) => c.id === id && !('error' in c) && !c.configured && c.configurable === false,
        );
    const displayName = (id: string) =>
        PROVIDER_NAMES[id]
        ?? connectors.find((c) => c.id === id && !('error' in c))?.display_name
        ?? id;
    const heldScopes = (id: string) => {
        const row = connectors.find((c) => c.id === id);
        return row && !('error' in row) ? row.scopes : undefined;
    };
    let prompt = t('connectCard.prompt');
    if (anyFixed) prompt = t('connectCard.askAgain');
    else if (ended) prompt = t(ended === 'cancelled' ? 'connectCard.cancelled' : 'connectCard.failed');
    else if (opened) prompt = t('connectCard.opened');

    return (
        <div className="render-connect" role="group" aria-label={t('connectCard.label')}>
            <div className="render-connect__text">
                <Link2 size={14} className="render-connect__icon" aria-hidden="true" />
                <span role={ended ? 'alert' : undefined}>{prompt}</span>
            </div>
            {opened && <SignInWatcher providers={data.providers} onEnded={onEnded} />}
            <div className="render-connect__providers">
                {data.providers.map((id) => (
                    <ProviderRow
                        key={id}
                        connectorId={id}
                        name={displayName(id)}
                        connected={isConnected(id)}
                        saysConnected={saysConnected(id)}
                        added={isFixed(id) ? addedBesideDefault(rowOf(connectors, id), rowOf(before, id)) : null}
                        offer={reconnectOffer(rowOf(connectors, id), data.account?.[id])}
                        needsSetup={needsSetup(id)}
                        grantAgents={grantIdsFor(agents, data.agent_id, id)}
                        scopes={requestScopes(
                            data.scopes?.[id],
                            heldScopes(id),
                        )}
                        onOpened={() => {
                            setEnded(null);
                            setOpened(true);
                        }}
                    />
                ))}
            </div>
            <button
                type="button"
                className="render-connect__settings"
                onClick={() => openSettings('connectors')}
            >
                {t('connectCard.settings')}
            </button>
        </div>
    );
}
