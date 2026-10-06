// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import * as api from '../../services/api';
import { useChatStore } from '../../stores/chatStore';
import type { SessionPermissions } from '../../types';

/** Every chat with full access on or "always allow" grants, and the way to take them back. */
export function PermissionsSettings() {
    const sessions = useChatStore((s) => s.sessions);
    const [rows, setRows] = useState<SessionPermissions[] | null>(null);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        setError(null);
        try {
            setRows((await api.listAllPermissions()).sessions);
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

    const title = (id: string) => sessions.find((s) => s.id === id)?.title ?? id;

    return (
        <div className="settings-pane">
            <h2 className="settings-pane-title">Permissions</h2>
            <p className="settings-pane-lede">
                GAIA asks before it changes files, runs commands, or uses tools that act on your behalf.
                Pick a mode per chat from the shield next to the message box. &ldquo;Always allow&rdquo;
                covers one exact action in one chat and ends when GAIA restarts.
            </p>
            <p className="settings-hint">
                New chats start in full access only when <code>full_access</code> is set in
                <code> ~/.gaia/config.json</code> (the TUI&rsquo;s <code>/full-access always</code>).
            </p>

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
