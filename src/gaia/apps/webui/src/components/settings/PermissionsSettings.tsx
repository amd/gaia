// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import * as api from '../../services/api';
import { useChatStore } from '../../stores/chatStore';
import type { PermissionMode, SessionPermissions } from '../../types';

/** Every chat with full access on or "always allow" grants, and the way to take them back. */
export function PermissionsSettings() {
    const sessions = useChatStore((s) => s.sessions);
    const [rows, setRows] = useState<SessionPermissions[] | null>(null);
    const [defaultMode, setDefaultMode] = useState<PermissionMode | null>(null);
    const [confirming, setConfirming] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        setError(null);
        try {
            const all = await api.listAllPermissions();
            setRows(all.sessions);
            setDefaultMode(all.default_mode);
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        }
    }, []);
    useEffect(() => { void load(); }, [load]);

    const act = useCallback(async (fn: () => Promise<unknown>) => {
        setError(null);
        try {
            await fn();
            await load();
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        }
    }, [load]);

    const saveDefault = useCallback(async (mode: PermissionMode) => {
        setConfirming(false);
        await act(() => api.setDefaultPermissionMode(mode));
    }, [act]);

    const onToggle = () => {
        if (defaultMode === 'full_access') void saveDefault('ask');
        else setConfirming((v) => !v);
    };

    const title = (id: string) => sessions.find((s) => s.id === id)?.title ?? id;

    return (
        <div className="settings-pane">
            <h2 className="settings-pane-title">Permissions</h2>
            <p className="settings-pane-lede">
                GAIA asks before it changes files, runs commands, or uses tools that act on your behalf.
                Pick a mode per chat from the shield next to the message box. &ldquo;Always allow&rdquo;
                covers one exact action in one chat and ends when GAIA restarts.
            </p>
            <section className="settings-card" aria-label="New chats">
                <label className="setting-row">
                    <span className="settings-label">Start new chats in full access</span>
                    <span className="toggle-switch">
                        <input
                            type="checkbox"
                            checked={defaultMode === 'full_access'}
                            onChange={onToggle}
                            disabled={defaultMode === null}
                            aria-label="Start new chats in full access"
                        />
                        <span className="toggle-track" />
                    </span>
                </label>
                <p className="settings-hint">
                    {defaultMode === 'full_access'
                        ? 'On. Every new chat runs commands and changes files without asking. Chats already open keep their mode.'
                        : 'Off. New chats ask before they change files or run commands. Chats already open keep their mode.'}
                </p>
                {confirming && (
                    <div className="settings-warning" role="alert">
                        <p>
                            Full access lets GAIA change or delete files and run commands on this PC without asking you
                            first, in every new chat, until you turn this off. A mistake or a bad instruction in a
                            document it reads can do real damage. This is the same setting as the TUI&rsquo;s
                            <code> /full-access always</code>.
                        </p>
                        <div className="settings-row">
                            <button type="button" className="btn-danger" onClick={() => void saveDefault('full_access')}>
                                Turn on full access for new chats
                            </button>
                            <button type="button" className="btn-secondary" onClick={() => setConfirming(false)}>
                                Cancel
                            </button>
                        </div>
                    </div>
                )}
            </section>

            {error && <p className="settings-error" role="alert">{error}</p>}
            {rows && rows.length === 0 && (
                <p className="settings-hint">No chat has full access or anything allowed without asking.</p>
            )}
            {rows?.map((r) => (
                <section key={r.session_id} className="settings-card" aria-label={title(r.session_id)}>
                    <div className="settings-row settings-row-between">
                        <span className="settings-label">{title(r.session_id)}</span>
                        {r.mode === 'full_access' && (
                            <button
                                type="button"
                                className="btn-secondary"
                                onClick={() => act(() => api.setPermissionMode(r.session_id, 'ask'))}
                            >
                                Turn off full access
                            </button>
                        )}
                    </div>
                    {r.grants.length > 0 && (
                        <ul className="grant-list">
                            {r.grants.map((g) => (
                                <li key={g.key} className="grant-row">
                                    <code>{g.label}</code>
                                    <button
                                        type="button"
                                        className="link-btn"
                                        onClick={() => act(() => api.revokeGrants(r.session_id, g.key))}
                                        aria-label={`Revoke ${g.label}`}
                                    >
                                        Revoke
                                    </button>
                                </li>
                            ))}
                        </ul>
                    )}
                    {r.grants.length > 1 && (
                        <button type="button" className="link-btn" onClick={() => act(() => api.revokeGrants(r.session_id))}>
                            Revoke all in this chat
                        </button>
                    )}
                </section>
            ))}
        </div>
    );
}
