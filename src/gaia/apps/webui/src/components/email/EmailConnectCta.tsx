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

import { useCallback, useState } from 'react';
import { AlertCircle, ExternalLink, Loader2 } from 'lucide-react';
import * as api from '../../services/api';
import { useChatStore } from '../../stores/chatStore';
import { FLAGSHIP_AGENT_ID } from '../../utils/newTask';
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
    done,
    onDone,
}: {
    connectorId: string;
    label: string;
    /** Namespaced agent ids to grant this connector when OAuth completes (#2117). */
    grantAgents: string[];
    done: boolean;
    onDone: () => void;
}) {
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);

    const handleConnect = useCallback(async () => {
        setBusy(true);
        setErr(null);
        try {
            // No scopes: the backend asks for what the granted agent declares,
            // never the connector's whole catalog.
            const r = await api.authorizeConnector(connectorId, [], grantAgents);
            openAuthUrl(r.authorization_url);
            onDone();
        } catch (e) {
            setErr(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(false);
        }
    }, [connectorId, grantAgents, onDone]);

    return (
        <div className="email-connect-cta__provider-slot">
            <button
                className="email-connect-cta__button"
                onClick={() => void handleConnect()}
                disabled={busy}
                aria-label={done ? `Reopen ${label} sign-in` : `Connect ${label}`}
            >
                {busy ? (
                    <Loader2 size={12} className="email-connect-cta__spinner" />
                ) : (
                    <ExternalLink size={12} />
                )}
                <span>{done ? `Reopen ${label} sign-in` : `Connect ${label}`}</span>
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
    const [googleDone, setGoogleDone] = useState(false);
    const [microsoftDone, setMicrosoftDone] = useState(false);

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

    const showGoogle = provider === 'google' || provider === 'all';
    const showMicrosoft = provider === 'microsoft' || provider === 'all';

    const anyDone = googleDone || microsoftDone;

    return (
        <div
            className="email-connect-cta"
            role="region"
            aria-label="Connect email account"
        >
            <div className="email-connect-cta__text">
                <AlertCircle size={14} className="email-connect-cta__icon" />
                <span>
                    {anyDone
                        ? 'A browser tab opened for sign-in. Return here when finished.'
                        : 'Connect your email account to use mail here.'}
                </span>
            </div>
            <div className="email-connect-cta__buttons">
                {showGoogle && (
                    <ProviderButton
                        connectorId="google"
                        label="Google"
                        grantAgents={grantAgentsFor('google')}
                        done={googleDone}
                        onDone={() => setGoogleDone(true)}
                    />
                )}
                {showMicrosoft && (
                    <ProviderButton
                        connectorId="microsoft"
                        label="Microsoft"
                        grantAgents={grantAgentsFor('microsoft')}
                        done={microsoftDone}
                        onDone={() => setMicrosoftDone(true)}
                    />
                )}
            </div>
        </div>
    );
}
