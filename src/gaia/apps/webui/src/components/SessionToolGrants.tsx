// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Session tool grants — every tool the user allowed "for the rest of this
 * chat", and the place to revoke them. Grants live in `notificationStore`
 * only, so the list is empty again after a reload or restart.
 */

import { ShieldCheck, RotateCcw } from 'lucide-react';
import { useNotificationStore, selectAlwaysAllowGrants } from '../stores/notificationStore';
import { useChatStore } from '../stores/chatStore';
import './SessionToolGrants.css';

export function SessionToolGrants() {
    const grants = useNotificationStore(selectAlwaysAllowGrants);
    const revokeAlwaysAllow = useNotificationStore((s) => s.revokeAlwaysAllow);
    const revokeAllAlwaysAllow = useNotificationStore((s) => s.revokeAllAlwaysAllow);
    const sessions = useChatStore((s) => s.sessions);
    const chatTitle = (sessionId: string) =>
        sessions.find((s) => s.id === sessionId)?.title || sessionId;

    return (
        <section className="perm-session-grants" aria-labelledby="perm-session-grants-title">
            <div className="perm-session-grants-head">
                <ShieldCheck size={14} className="perm-session-grants-icon" />
                <h4 id="perm-session-grants-title" className="perm-session-grants-title">
                    Tools allowed without asking
                </h4>
                {grants.length > 0 && (
                    <button
                        className="btn-secondary perm-session-revoke-all"
                        onClick={revokeAllAlwaysAllow}
                    >
                        <RotateCcw size={13} />
                        Revoke All
                    </button>
                )}
            </div>
            {grants.length === 0 ? (
                <p className="perm-session-grants-empty">
                    No tools are auto-approved. Ticking &ldquo;Allow this tool for the rest of
                    this chat&rdquo; on a permission prompt adds one here. A grant covers only that
                    chat and ends when you reload or restart GAIA.
                </p>
            ) : (
                <ul className="perm-session-grant-list">
                    {grants.map(({ sessionId, tool }) => {
                        const title = chatTitle(sessionId);
                        return (
                            <li key={`${sessionId}:${tool}`} className="perm-session-grant">
                                <code className="perm-tool-name">{tool}</code>
                                <span className="perm-session-grant-note">
                                    in &ldquo;{title}&rdquo; until reload or restart
                                </span>
                                <button
                                    className="perm-session-grant-revoke"
                                    onClick={() => revokeAlwaysAllow(sessionId, tool)}
                                    aria-label={`Revoke ${tool} in ${title}`}
                                >
                                    Revoke
                                </button>
                            </li>
                        );
                    })}
                </ul>
            )}
        </section>
    );
}
