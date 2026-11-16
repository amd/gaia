// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * EmailConnectCta
 *
 * Inline "Connect" button(s) rendered next to an assistant message when the
 * email agent surfaces a connectors auth-required error (``NOT_CONNECTED:``,
 * ``AGENT_NOT_GRANTED:``, or ``AUTH_REQUIRED:`` from
 * ``gaia.connectors.formatting.format_connector_error``). The CTA triggers
 * the same OAuth flow the user would otherwise reach via
 * Settings → Connectors → (Google|Microsoft) → Connect — without forcing
 * them to navigate away from the chat.
 *
 * Detection lives in ``isAuthRequiredMessage`` so MessageBubble can
 * mount this component conditionally on assistant content.
 *
 * Provider detection: when the error message mentions "microsoft" the CTA
 * offers a personal Outlook connect button; when it mentions "microsoft
 * 365"/"microsoft_work" it offers a work Microsoft 365 button instead; when
 * it mentions "google" (or the ``installed:email`` upgrade message) it
 * offers Google; when the provider is ambiguous all three buttons are shown
 * so the user can pick.
 */

import { useCallback, useEffect, useState } from 'react';
import { AlertCircle, ExternalLink, Loader2 } from 'lucide-react';
import * as api from '../../services/api';
import { useChatStore } from '../../stores/chatStore';
import { useT } from '../../i18n/useT';
import { log } from '../../utils/logger';
import { accountRef, middleCut, reconnectOffer } from '../../utils/connectorAccounts';
import { useConnectorsSSE } from '../../hooks/useConnectorsSSE';
import type { ConnectorListEntry } from '../../types';
import { ltrIn } from '../setup/format';
import './EmailConnectCta.css';

// The base agent id this CTA belongs to. The connect CTA lives in the email
// chat, so it may only grant the email agent — never co-installed agents that
// happen to declare the same mailbox connector.
const EMAIL_AGENT_ID = 'email';

/**
 * #2117 — resolve the grant target for the email connect CTA.
 *
 * Returns the namespaced id of the EMAIL agent (and only it) when that agent
 * declares ``connectorId``. Co-installed agents that also declare the connector
 * (e.g. a custom ``installed:my-agent`` for Google) are deliberately excluded:
 * granting them here would hand them the mailbox scopes with no consent surface,
 * bypassing the per-agent grant model. Those agents are granted explicitly from
 * Settings → Connectors instead.
 *
 * Filtering by base ``id`` keeps this robust to the namespace prefix
 * (``installed:`` vs ``builtin:``). Returns ``[]`` if the email agent isn't
 * present or doesn't declare the connector — a connect-only flow, never an
 * over-grant.
 */
export function emailAgentGrantIds(
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
                a.id === EMAIL_AGENT_ID &&
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
            const connector = await api.getConnector(connectorId);
            const scopes =
                connector.available_scopes && connector.available_scopes.length > 0
                    ? connector.available_scopes
                    : connector.default_scopes;
            // Grant the connecting agent(s) in the same flow — this CTA is
            // email-initiated, so triage works right after consent with no CLI.
            const r = target
                ? await api.authorizeConnector(connectorId, scopes, grantAgents, target)
                : await api.authorizeConnector(connectorId, scopes, grantAgents);
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
    ['microsoft_work', 'Microsoft 365'],
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

    // #2117 — this CTA is email-initiated, so connecting grants ONLY the email
    // agent the moment consent completes (no follow-up CLI grant, no "no grant
    // for google" dead end). Co-installed agents that declare the same connector
    // are NOT granted here — that would bypass their per-agent consent; the user
    // grants them explicitly from Settings → Connectors.
    const { agents } = useChatStore();
    const grantAgentsFor = useCallback(
        (cid: string): string[] => emailAgentGrantIds(agents, cid),
        [agents],
    );

    // Resolve which provider(s) to surface. Honor an explicit connector
    // override; any other value (or none) falls back to content detection so we
    // never render zero connect buttons (no silent dead-end).
    const provider: EmailProvider | 'all' =
        connectorId === 'google' || connectorId === 'microsoft' || connectorId === 'microsoft_work'
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
