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
 * offers a Microsoft connect button; when it mentions "google" (or the
 * ``installed:email`` upgrade message) it offers Google; when the provider
 * is ambiguous both buttons are shown so the user can pick.
 */

import { useCallback, useState } from 'react';
import { AlertCircle, ExternalLink, Loader2 } from 'lucide-react';
import * as api from '../../services/api';
import { useChatStore } from '../../stores/chatStore';
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
 * (e.g. ``installed:connectors-demo`` for Google) are deliberately excluded:
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
    // Microsoft-side error messages from format_connector_error
    if (
        lower.includes('connectors → microsoft') ||
        lower.includes('connections → microsoft') ||
        lower.includes('microsoft is not currently connected')
    ) {
        return true;
    }
    return false;
}

/**
 * Detect which provider(s) an error message references.
 *
 * Returns:
 * - ``'google'``    — only Google connector mentioned
 * - ``'microsoft'`` — only Microsoft connector mentioned
 * - ``'both'``      — ambiguous / no specific provider found → show both
 */
export function detectProvider(content: string): 'google' | 'microsoft' | 'both' {
    if (!content) return 'both';
    const lower = content.toLowerCase();
    const mentionsGoogle =
        lower.includes('google') ||
        lower.includes('gmail');
    const mentionsMicrosoft =
        lower.includes('microsoft') ||
        lower.includes('outlook');
    if (mentionsGoogle && !mentionsMicrosoft) return 'google';
    if (mentionsMicrosoft && !mentionsGoogle) return 'microsoft';
    return 'both';
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
            const connector = await api.getConnector(connectorId);
            const scopes =
                connector.available_scopes && connector.available_scopes.length > 0
                    ? connector.available_scopes
                    : connector.default_scopes;
            // Grant the connecting agent(s) in the same flow — this CTA is
            // email-initiated, so triage works right after consent with no CLI.
            const r = await api.authorizeConnector(connectorId, scopes, grantAgents);
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

    // Resolve which provider(s) to surface. Honor an explicit google/microsoft
    // override; any other value (or none) falls back to content detection so we
    // never render zero connect buttons (no silent dead-end).
    const provider =
        connectorId === 'google' || connectorId === 'microsoft'
            ? connectorId
            : detectProvider(content);

    const showGoogle = provider === 'google' || provider === 'both';
    const showMicrosoft = provider === 'microsoft' || provider === 'both';

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
                        : 'Connect your email account to use Email Triage.'}
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
