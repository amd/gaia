// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { CheckCircle2, ExternalLink, Link2, Loader2 } from 'lucide-react';
import * as api from '../../services/api';
import { useChatStore } from '../../stores/chatStore';
import { useT } from '../../i18n/useT';
import { useConnectorsSSE } from '../../hooks/useConnectorsSSE';
import { log } from '../../utils/logger';
import type { ConnectorListEntry, Message } from '../../types';
import { UnsupportedCard } from './UnsupportedCard';

/**
 * ConnectAccountCard — the `connect_account` surface.
 *
 * A mail or calendar tool that finds no usable account sends this card with
 * its result, so the Connect button is on screen whatever the model writes.
 * Pressing it starts sign-in for the access the asking agent declares, and
 * gives that agent access in the same step.
 */

export const CONNECT_ACCOUNT_RENDER = 'connect_account';

interface ConnectAccountPayload {
    kind: typeof CONNECT_ACCOUNT_RENDER;
    providers: string[];
    agent_id?: string;
    /** Providers connected already, whose fault is something signing in again fixes. */
    reconnect?: string[];
}

export function isConnectAccountPayload(value: unknown): value is ConnectAccountPayload {
    if (!value || typeof value !== 'object') return false;
    const v = value as Record<string, unknown>;
    return (
        v.kind === CONNECT_ACCOUNT_RENDER &&
        Array.isArray(v.providers) &&
        v.providers.length > 0 &&
        v.providers.every((p) => typeof p === 'string' && p.length > 0) &&
        (v.agent_id === undefined || typeof v.agent_id === 'string') &&
        (v.reconnect === undefined
            || (Array.isArray(v.reconnect) && v.reconnect.every((p) => typeof p === 'string')))
    );
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

const POLL_MS = 4000;

/** Sign-in refused because this PC has no OAuth client for the provider yet. */
function isUnconfigured(e: unknown): boolean {
    const err = e as { httpStatus?: number; body?: Record<string, unknown> | null };
    return err?.httpStatus === 503 && err.body?.error === 'not_configured';
}

function ProviderRow({
    connectorId,
    name,
    connected,
    needsSetup,
    grantAgents,
    onOpened,
}: {
    connectorId: string;
    name: string;
    connected: boolean;
    /** No OAuth client on this PC yet, so sign-in cannot start. */
    needsSetup: boolean;
    grantAgents: string[];
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

    const connect = useCallback(async () => {
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
            // No scopes: the backend asks for what the granted agent declares,
            // never the connector's whole list.
            const flow = await api.authorizeConnector(connectorId, [], grantAgents);
            openAuthUrl(flow.authorization_url);
            onOpened();
        } catch (e) {
            if (isUnconfigured(e)) setSetup(true);
            else setError(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(false);
        }
    }, [connectorId, needsSetup, grantAgents, onOpened, openSettings]);

    const label = t(connected ? 'connectCard.reconnect' : 'connectCard.connect', { name });
    return (
        <div className="render-connect__provider">
            <button
                type="button"
                className={`render-connect__button${connected ? ' render-connect__button--quiet' : ''}`}
                onClick={() => void connect()}
                disabled={busy}
            >
                {busy
                    ? <Loader2 size={12} className="render-connect__spinner" aria-hidden="true" />
                    : <ExternalLink size={12} aria-hidden="true" />}
                <span>{label}</span>
            </button>
            {connected && (
                <span className="render-connect__connected" role="status">
                    <CheckCircle2 size={13} aria-hidden="true" />
                    {t('connectCard.connected', { name })}
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
    const [opened, setOpened] = useState(false);
    const [ended, setEnded] = useState<'cancelled' | 'failed' | null>(null);
    const valid = isConnectAccountPayload(data);

    const refresh = useCallback(() => {
        api.listConnectors()
            .then((res) => setConnectors(res.connectors || []))
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

    // While a sign-in tab is open, watch for the account to appear.
    useEffect(() => {
        if (!opened) return undefined;
        const timer = setInterval(refresh, POLL_MS);
        return () => clearInterval(timer);
    }, [opened, refresh]);

    const onEnded = useCallback((why: 'cancelled' | 'failed') => {
        setOpened(false);
        setEnded(why);
    }, []);

    if (!isConnectAccountPayload(data)) {
        return <UnsupportedCard variant="invalid" render={CONNECT_ACCOUNT_RENDER} data={data} />;
    }

    const isConnected = (id: string) =>
        connectors.some((c) => c.id === id && !('error' in c) && c.configured);
    const needsSetup = (id: string) =>
        connectors.some(
            (c) => c.id === id && !('error' in c) && !c.configured && c.configurable === false,
        );
    const displayName = (id: string) =>
        PROVIDER_NAMES[id]
        ?? connectors.find((c) => c.id === id && !('error' in c))?.display_name
        ?? id;
    const anyConnected = awaited(data).some(isConnected);

    let prompt = t('connectCard.prompt');
    if (anyConnected) prompt = t('connectCard.askAgain');
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
                        needsSetup={needsSetup(id)}
                        grantAgents={grantIdsFor(agents, data.agent_id, id)}
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
