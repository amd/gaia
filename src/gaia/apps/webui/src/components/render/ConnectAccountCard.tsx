// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import { CheckCircle2, ExternalLink, Link2, Loader2 } from 'lucide-react';
import * as api from '../../services/api';
import { useChatStore } from '../../stores/chatStore';
import { useT } from '../../i18n/useT';
import { log } from '../../utils/logger';
import type { ConnectorListEntry, Message } from '../../types';
import { UnsupportedCard } from './UnsupportedCard';

/**
 * ConnectAccountCard — the `connect_account` surface.
 *
 * A mail or calendar tool that finds no usable account sends this card with
 * its result, so the Connect button is on screen whatever the model writes.
 * Pressing it starts the same sign-in as Settings › Connectors and gives the
 * asking agent access in the same step.
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

function ProviderRow({
    connectorId,
    name,
    connected,
    grantAgents,
    onOpened,
}: {
    connectorId: string;
    name: string;
    connected: boolean;
    grantAgents: string[];
    onOpened: () => void;
}) {
    const t = useT();
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const connect = useCallback(async () => {
        setBusy(true);
        setError(null);
        try {
            const connector = await api.getConnector(connectorId);
            const scopes = connector.available_scopes?.length
                ? connector.available_scopes
                : connector.default_scopes;
            const flow = await api.authorizeConnector(connectorId, scopes, grantAgents);
            openAuthUrl(flow.authorization_url);
            onOpened();
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(false);
        }
    }, [connectorId, grantAgents, onOpened]);

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
        </div>
    );
}

export function ConnectAccountCard({ data }: { data: unknown }) {
    const t = useT();
    const agents = useChatStore((s) => s.agents);
    const openSettings = useChatStore((s) => s.openSettings);
    const [connectors, setConnectors] = useState<ConnectorListEntry[]>([]);
    const [opened, setOpened] = useState(false);
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

    // While a sign-in tab is open, watch for the account to appear.
    useEffect(() => {
        if (!opened) return undefined;
        const timer = setInterval(refresh, POLL_MS);
        return () => clearInterval(timer);
    }, [opened, refresh]);

    if (!isConnectAccountPayload(data)) {
        return <UnsupportedCard variant="invalid" render={CONNECT_ACCOUNT_RENDER} data={data} />;
    }

    const isConnected = (id: string) =>
        connectors.some((c) => c.id === id && !('error' in c) && c.configured);
    const displayName = (id: string) =>
        PROVIDER_NAMES[id]
        ?? connectors.find((c) => c.id === id && !('error' in c))?.display_name
        ?? id;
    const anyConnected = awaited(data).some(isConnected);

    let prompt = t('connectCard.prompt');
    if (anyConnected) prompt = t('connectCard.askAgain');
    else if (opened) prompt = t('connectCard.opened');

    return (
        <div className="render-connect" role="group" aria-label={t('connectCard.label')}>
            <div className="render-connect__text">
                <Link2 size={14} className="render-connect__icon" aria-hidden="true" />
                <span>{prompt}</span>
            </div>
            <div className="render-connect__providers">
                {data.providers.map((id) => (
                    <ProviderRow
                        key={id}
                        connectorId={id}
                        name={displayName(id)}
                        connected={isConnected(id)}
                        grantAgents={grantIdsFor(agents, data.agent_id, id)}
                        onOpened={() => setOpened(true)}
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
