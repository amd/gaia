// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * EmailConnectCta
 *
 * Inline "Connect" button(s) rendered next to an assistant message when a
 * reply carries a connectors auth-required error (``NOT_CONNECTED:``,
 * ``AGENT_NOT_GRANTED:``, or ``AUTH_REQUIRED:`` from
 * ``gaia.connectors.formatting.format_connector_error``). The CTA triggers
 * the same OAuth flow the user would otherwise reach via
 * Settings → Connectors → (Google|Microsoft) → Connect — without forcing
 * them to navigate away from the chat.
 *
 * Detection lives in ``isAuthRequiredMessage`` so MessageBubble can
 * mount this component conditionally on assistant content.
 *
 * Provider detection: a message that names Microsoft or Outlook gets the
 * Microsoft button, one that names Google or Gmail gets the Google button, and
 * an ambiguous one gets both. Those are the two mailboxes GAIA reads; a work
 * Microsoft 365 account is connected from Settings → Connectors.
 */

import { useCallback, useEffect, useState } from 'react';
import { AlertCircle, ExternalLink, Loader2 } from 'lucide-react';
import * as api from '../../services/api';
import { useChatStore } from '../../stores/chatStore';
import { FLAGSHIP_AGENT_ID } from '../../utils/newTask';
import { useT } from '../../i18n/useT';
import { log } from '../../utils/logger';
import { accountRef, middleCut, reconnectOffer } from '../../utils/connectorAccounts';
import { useConnectorsSSE } from '../../hooks/useConnectorsSSE';
import type { ConnectorListEntry } from '../../types';
import { ltrIn } from '../setup/format';
import './EmailConnectCta.css';

/**
 * #2117 — the agent this CTA grants when sign-in completes: GAIA, and only when
 * it declares ``connectorId``. Another installed agent that declares the same
 * connector is never granted here, since that would hand it mailbox scopes
 * with no consent surface; those are granted from Settings → Connectors.
 * Returns ``[]`` (connect only) rather than over-grant.
 */
export function mailGrantIds(
    agents: Array<{
        id?: string;
        namespaced_agent_id?: string;
        required_connections?: Array<{ connector_id: string }>;
    }>,
    connectorId: string,
): string[] {
    return agents
        .filter(
            (a) =>
                a.id === FLAGSHIP_AGENT_ID &&
                !!a.namespaced_agent_id &&
                (a.required_connections?.some((rc) => rc.connector_id === connectorId) ??
                    false),
        )
        .map((a) => a.namespaced_agent_id!);
}

// ── Detection ────────────────────────────────────────────────────────────────

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

// ── OAuth helpers (mirror ConnectorsSection.openAuthUrl) ─────────────────────

function openAuthUrl(url: string): void {
    const anyWindow = window as unknown as {
        gaia?: { openExternal?: (url: string) => void };
    };
    if (anyWindow.gaia?.openExternal) {
        anyWindow.gaia.openExternal(url);
    } else {
        window.open(url, '_blank', 'noopener');
    }
}

// ── Single-provider connect button ────────────────────────────────────────────

function ProviderButton({
    connectorId,
    label,
    grantAgents,
    onDone,
    target,
    title,
    ready,
}: {
    connectorId: string;
    /** The button's whole text. */
    label: string;
    /** Namespaced agent ids to grant this connector when OAuth completes (#2117). */
    grantAgents: string[];
    onDone: () => void;
    /** Sign in to one stored account again, or to another account. */
    target?: api.AuthorizeTarget;
    title?: string;
    /** The accounts are known; until then a click could sign in to the wrong one. */
    ready: boolean;
}) {
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);

    const handleConnect = useCallback(async () => {
        setBusy(true);
        setErr(null);
        try {
            // No scopes: the backend asks for what the granted agent declares,
            // never the connector's whole catalog.
            const r = target
                ? await api.authorizeConnector(connectorId, [], grantAgents, target)
                : await api.authorizeConnector(connectorId, [], grantAgents);
            openAuthUrl(r.authorization_url);
            onDone();
        } catch (e) {
            setErr(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(false);
        }
    }, [connectorId, grantAgents, onDone, target]);

    return (
        <div className="email-connect-cta__provider-slot">
            <button
                className="email-connect-cta__button"
                onClick={() => void handleConnect()}
                disabled={busy || !ready}
                title={title}
            >
                {busy ? (
                    <Loader2 size={12} className="email-connect-cta__spinner" />
                ) : (
                    <ExternalLink size={12} />
                )}
                <span>{label}</span>
            </button>
            {err && (
                <div className="email-connect-cta__error" role="alert">
                    {err}
                </div>
            )}
        </div>
    );
}

// ── Component ────────────────────────────────────────────────────────────────

// Brand names, the same in every language.
const PROVIDERS: ReadonlyArray<[EmailProvider, string]> = [
    ['google', 'Google'],
    ['microsoft', 'Microsoft'],
];

const ADD_ACCOUNT: api.AuthorizeTarget = { addAccount: true };

/** Re-reads the accounts on every connector change. Mounted only after a sign-in
 *  opened: every chat reply with this button would otherwise hold a connection. */
function ConnectorWatcher({ onChange }: { onChange: () => void }) {
    useConnectorsSSE(onChange);
    return null;
}

export function EmailConnectCta({
    content = '',
    connectorId,
}: {
    /** The assistant message content — used to detect which provider to surface. */
    content?: string;
    /**
     * Optional explicit connector override. When provided, only this connector's
     * button is shown regardless of content detection. Kept for back-compat with
     * callers that hardcode ``connectorId="google"``.
     */
    connectorId?: string;
}) {
    const t = useT();
    const [opened, setOpened] = useState<ReadonlySet<string>>(new Set());
    const markOpened = useCallback((id: string) => setOpened((prev) => new Set([...prev, id])), []);
    const [connectors, setConnectors] = useState<ConnectorListEntry[]>([]);
    const [ready, setReady] = useState(false);
    const refresh = useCallback(() => {
        api.listConnectors()
            .then((res) => setConnectors(res.connectors || []))
            .catch((err) => log.ui.warn('EmailConnectCta: connector fetch failed', err))
            .finally(() => setReady(true));
    }, []);
    // Re-read on mount and when the window comes back from a sign-in tab.
    useEffect(() => {
        refresh();
        window.addEventListener('focus', refresh);
        return () => window.removeEventListener('focus', refresh);
    }, [refresh]);

    const { agents } = useChatStore();
    const grantAgentsFor = useCallback(
        (cid: string): string[] => mailGrantIds(agents, cid),
        [agents],
    );

    // Resolve which provider(s) to surface. Honor an explicit connector
    // override; any other value (or none) falls back to content detection so we
    // never render zero connect buttons (no silent dead-end).
    const provider: EmailProvider | 'all' =
        connectorId === 'google' || connectorId === 'microsoft'
            ? connectorId
            : detectProvider(content);

    const shown = PROVIDERS.filter(([id]) => provider === id || provider === 'all');

    return (
        <div
            className="email-connect-cta"
            role="region"
            aria-label={t('emailCta.label')}
        >
            <div className="email-connect-cta__text">
                <AlertCircle size={14} className="email-connect-cta__icon" />
                <span>{t(opened.size > 0 ? 'connectCard.opened' : 'emailCta.prompt')}</span>
            </div>
            {opened.size > 0 && <ConnectorWatcher onChange={refresh} />}
            <div className="email-connect-cta__buttons">
                {shown.map(([id, name]) => {
                    const offer = reconnectOffer(connectors.find((c) => c.id === id));
                    const done = opened.has(id);
                    const common = { connectorId: id, grantAgents: grantAgentsFor(id), onDone: () => markOpened(id), ready };
                    return [
                        offer.account ? (
                            <ProviderButton
                                key={`${id}:${offer.account.account_id}`}
                                {...common}
                                label={t('connectCard.reconnectAccount', { address: ltrIn(t.locale, middleCut(offer.account.email || offer.account.account_id)) })}
                                title={offer.account.email || undefined}
                                target={{ account: accountRef(offer.account) }}
                            />
                        ) : (
                            <ProviderButton
                                key={id}
                                {...common}
                                label={t(done ? 'emailCta.reopen' : 'connectCard.connect', { name })}
                            />
                        ),
                        offer.otherAccount && (
                            <ProviderButton key={`${id}:other`} {...common} label={t('connectCard.otherAccount')} target={ADD_ACCOUNT} />
                        ),
                    ];
                })}
            </div>
        </div>
    );
}
