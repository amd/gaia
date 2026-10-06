// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { Check, ChevronDown, ShieldAlert, ShieldCheck } from 'lucide-react';
import { Popover } from './Popover';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import type { PermissionMode } from '../types';

const MODES: Array<{ mode: PermissionMode; label: string; note: string }> = [
    { mode: 'ask', label: 'Ask before acting', note: 'GAIA asks before it edits files, runs commands or uses tools that change things.' },
    { mode: 'full_access', label: 'Full access', note: 'Runs every tool and command without asking, in this chat only.' },
];

/**
 * This chat's permission mode — the TUI's ask / full access. With no chat yet
 * (the new-chat screen) the choice is held and applied when the chat is made.
 */
export function PermissionModeChip({ sessionId, disabled }: { sessionId: string | null; disabled?: boolean }) {
    const draftMode = useChatStore((s) => s.draftPermissionMode);
    const setDraftMode = useChatStore((s) => s.setDraftPermissionMode);
    const [mode, setMode] = useState<PermissionMode>(sessionId ? 'ask' : draftMode);
    const [open, setOpen] = useState(false);
    const [confirming, setConfirming] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const anchor = useRef<HTMLButtonElement>(null);

    useEffect(() => {
        if (!sessionId) {
            setMode(draftMode);
            return;
        }
        let cancelled = false;
        api.getPermissions(sessionId)
            .then((p) => { if (!cancelled) setMode(p.mode); })
            .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : String(err)); });
        return () => { cancelled = true; };
    }, [sessionId, draftMode]);

    const apply = useCallback(async (next: PermissionMode) => {
        if (next === 'full_access' && !confirming) {
            setConfirming(true);
            return;
        }
        setError(null);
        try {
            if (sessionId) {
                setMode((await api.setPermissionMode(sessionId, next)).mode);
            } else {
                setDraftMode(next);
                setMode(next);
            }
            setOpen(false);
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setConfirming(false);
        }
    }, [confirming, sessionId, setDraftMode]);

    const full = mode === 'full_access';
    const Icon = full ? ShieldAlert : ShieldCheck;
    return (
        <div className="composer-chip-wrap">
            <button
                ref={anchor}
                type="button"
                className={`composer-chip${full ? ' is-warning' : ''}`}
                onClick={() => { setConfirming(false); setOpen((v) => !v); }}
                disabled={disabled}
                aria-haspopup="dialog"
                aria-expanded={open}
                aria-label={`Permission mode: ${full ? 'full access' : 'ask before acting'}`}
            >
                <Icon size={13} aria-hidden="true" />
                <span className="composer-chip-label">{full ? 'Full access' : 'Ask'}</span>
                <ChevronDown size={12} aria-hidden="true" />
            </button>
            <Popover open={open} onClose={() => setOpen(false)} anchor={anchor} label="Permission mode">
                {MODES.map((m) => (
                    <button
                        key={m.mode}
                        type="button"
                        className="popover-item"
                        onClick={() => apply(m.mode)}
                        aria-pressed={mode === m.mode}
                    >
                        <span className="popover-item-check" aria-hidden="true">{mode === m.mode && <Check size={13} />}</span>
                        <span className="popover-item-body">
                            <span className="popover-item-title">{m.label}</span>
                            <span className="popover-item-note">{m.note}</span>
                        </span>
                    </button>
                ))}
                {confirming && (
                    <div className="popover-message" role="alert">
                        Full access lets GAIA change files and run commands on this PC without asking.{' '}
                        <button type="button" className="link-btn" onClick={() => apply('full_access')}>Turn it on</button>
                    </div>
                )}
                {error && <div className="popover-message is-error" role="alert">{error}</div>}
            </Popover>
        </div>
    );
}
