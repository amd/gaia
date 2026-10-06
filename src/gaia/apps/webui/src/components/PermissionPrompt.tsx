// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronRight, ShieldQuestion } from 'lucide-react';
import {
    useNotificationStore,
    selectSessionPermissionPrompt,
    requiresFreshConsent,
    PATH_ACCESS_TOOL,
    type PermissionDecision,
} from '../stores/notificationStore';
import type { GaiaNotification } from '../types/agent';
import './PermissionPrompt.css';

/** One-line description of a tool call, e.g. `run_shell_command · git status`. */
function summarize(n: GaiaNotification): string {
    const args = n.toolArgs ?? {};
    const primary = ['command', 'file_path', 'path', 'url', 'query', 'name']
        .map((k) => args[k])
        .find((v) => typeof v === 'string' && v.trim());
    return typeof primary === 'string' ? primary : '';
}

/**
 * The permission question for this chat, inline above the composer: allow once,
 * always allow this exact call in this chat, or deny — the TUI's choices.
 *
 * Enter never answers it: focus moves to the card, not a button, so a keystroke
 * meant for the composer can't approve. Esc denies; Tab reaches the buttons.
 */
export function PermissionPrompt({ sessionId }: { sessionId: string }) {
    const selector = useMemo(() => selectSessionPermissionPrompt(sessionId), [sessionId]);
    const prompt = useNotificationStore(selector);
    if (!prompt) return null;
    return <PromptCard key={prompt.id} notification={prompt} />;
}

/** `45` -> "45s", `600` -> "10:00". */
export function formatCountdown(seconds: number): string {
    if (seconds < 60) return `${seconds}s`;
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${String(secs).padStart(2, '0')}`;
}

function PromptCard({ notification }: { notification: GaiaNotification }) {
    const respond = useNotificationStore((s) => s.respondToPermission);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [showArgs, setShowArgs] = useState(false);
    const hasTimeout = notification.timeoutSeconds != null && notification.timeoutSeconds > 0;
    // A deadline, not a tick count: background tabs throttle timers, and the
    // backend denies at its own deadline regardless of how often we ticked.
    const deadline = useRef(hasTimeout ? Date.now() + notification.timeoutSeconds! * 1000 : null);
    const [remaining, setRemaining] = useState<number | null>(hasTimeout ? notification.timeoutSeconds! : null);
    const cardRef = useRef<HTMLElement>(null);
    const freshConsent = requiresFreshConsent(notification.tool);
    const scope = freshConsent ? undefined : notification.alwaysScope;
    const detail = summarize(notification);
    const args = notification.toolArgs ?? {};
    const hasArgs = Object.keys(args).length > 0;

    // Take focus from the composer so its Enter can't land on a button here.
    useEffect(() => { cardRef.current?.focus({ preventScroll: true }); }, []);

    // The backend denies on its own when the wait runs out; this only shows it.
    useEffect(() => {
        if (deadline.current === null) return;
        const t = setInterval(() => {
            const left = Math.max(0, Math.ceil((deadline.current! - Date.now()) / 1000));
            setRemaining(left);
            if (left === 0) clearInterval(t);
        }, 1000);
        return () => clearInterval(t);
    }, []);

    const answer = useCallback(async (decision: PermissionDecision) => {
        setBusy(true);
        setError(null);
        try {
            await respond(notification.id, decision);
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
            setBusy(false);
        }
    }, [notification.id, respond]);

    // Esc denies. Enter is deliberately unbound — only a focused button approves.
    // Capture phase, so this runs before ChatView's Escape-stops-the-reply.
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if (e.key !== 'Escape' || e.defaultPrevented) return;
            if (remaining !== null && remaining <= 0) return;
            // Claimed even mid-answer: a second Esc would otherwise stop the whole reply.
            e.preventDefault();
            if (!busy) void answer('deny');
        };
        window.addEventListener('keydown', onKey, true);
        return () => window.removeEventListener('keydown', onKey, true);
    }, [answer, busy, remaining]);

    const expired = remaining !== null && remaining <= 0;
    return (
        <section
            ref={cardRef}
            tabIndex={-1}
            className="perm-card"
            role="alertdialog"
            aria-labelledby={`perm-${notification.id}`}
        >
            <div className="perm-card-head">
                <ShieldQuestion size={16} className="perm-card-icon" aria-hidden="true" />
                <div className="perm-card-text">
                    <h2 id={`perm-${notification.id}`} className="perm-card-title">
                        {notification.tool === PATH_ACCESS_TOOL
                            ? notification.title
                            : <>Allow GAIA to use <code>{notification.tool}</code>?</>}
                    </h2>
                    {detail && <p className="perm-card-detail"><code>{detail}</code></p>}
                    {freshConsent && notification.message && <p className="perm-card-detail">{notification.message}</p>}
                </div>
                {remaining !== null && !expired && (
                    <span className="perm-card-timer" title="Denied automatically when the time runs out">{formatCountdown(remaining)}</span>
                )}
            </div>
            {hasArgs && (
                <div className="perm-card-args">
                    <button
                        type="button"
                        className="perm-card-args-toggle"
                        onClick={() => setShowArgs((v) => !v)}
                        aria-expanded={showArgs}
                    >
                        <ChevronRight size={13} className={showArgs ? 'is-open' : ''} aria-hidden="true" />
                        Details
                    </button>
                    {showArgs && <pre className="perm-card-pre">{JSON.stringify(args, null, 2)}</pre>}
                </div>
            )}
            {expired ? (
                <p className="perm-card-detail">No answer in time, so GAIA was told no.</p>
            ) : (
                <div className="perm-card-actions">
                    <button type="button" className="perm-btn is-primary" onClick={() => answer('allow')} disabled={busy}>
                        Allow once
                    </button>
                    {scope && (
                        <button
                            type="button"
                            className="perm-btn"
                            onClick={() => answer('always')}
                            disabled={busy}
                            aria-label={`Always allow ${scope} in this chat`}
                        >
                            Always allow <code>{scope}</code> in this chat
                        </button>
                    )}
                    <button type="button" className="perm-btn" onClick={() => answer('deny')} disabled={busy}>
                        Deny
                    </button>
                </div>
            )}
            {error && <p className="perm-card-error" role="alert">{error}</p>}
        </section>
    );
}
