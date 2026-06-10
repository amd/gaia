// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * EmailConnectCta
 *
 * Inline "Connect Google" button rendered next to an assistant message
 * when the email agent surfaces a connectors auth-required error
 * (``NOT_CONNECTED:`` or ``AGENT_NOT_GRANTED:`` from
 * ``gaia.connectors.formatting.format_connector_error``). The CTA
 * triggers the same OAuth flow the user would otherwise reach via
 * Settings → Connectors → Google → Connect — without forcing them to
 * navigate away from the chat.
 *
 * Detection lives in ``isAuthRequiredMessage`` so MessageBubble can
 * mount this component conditionally on assistant content.
 */

import { useCallback, useState } from 'react';
import { AlertCircle, ExternalLink, Loader2 } from 'lucide-react';
import * as api from '../../services/api';
import './EmailConnectCta.css';

// ── Detection ────────────────────────────────────────────────────────────────

/** Match the canonical prefixes the connectors framework emits. The
 *  prefixes are stable (see ``connectors/formatting.py``); fuzzy
 *  fallbacks like "Open Settings → Connectors → Google" handle the
 *  agent-specific override message for ``installed:email``.
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
    return false;
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

// ── Component ────────────────────────────────────────────────────────────────

export function EmailConnectCta({
    connectorId = 'google',
}: {
    connectorId?: string;
}) {
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    const [done, setDone] = useState(false);

    const handleConnect = useCallback(async () => {
        setBusy(true);
        setErr(null);
        try {
            const connector = await api.getConnector(connectorId);
            const scopes =
                connector.available_scopes && connector.available_scopes.length > 0
                    ? connector.available_scopes
                    : connector.default_scopes;
            const r = await api.authorizeConnector(connectorId, scopes);
            openAuthUrl(r.authorization_url);
            setDone(true);
        } catch (e) {
            setErr(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(false);
        }
    }, [connectorId]);

    return (
        <div className="email-connect-cta" role="region" aria-label="Connect Google">
            <div className="email-connect-cta__text">
                <AlertCircle size={14} className="email-connect-cta__icon" />
                <span>
                    {done
                        ? 'A browser tab opened for Google sign-in. Return here when finished.'
                        : 'Connect your Google account to use Email Triage.'}
                </span>
            </div>
            <button
                className="email-connect-cta__button"
                onClick={() => void handleConnect()}
                disabled={busy}
            >
                {busy ? (
                    <Loader2 size={12} className="email-connect-cta__spinner" />
                ) : (
                    <ExternalLink size={12} />
                )}
                <span>{done ? 'Reopen Google sign-in' : 'Connect Google'}</span>
            </button>
            {err && (
                <div className="email-connect-cta__error" role="alert">
                    {err}
                </div>
            )}
        </div>
    );
}
