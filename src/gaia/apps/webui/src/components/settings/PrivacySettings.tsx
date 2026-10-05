// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import * as api from '../../services/api';
import { useChatStore } from '../../stores/chatStore';
import { useModelStore, locationLabel } from '../../stores/modelStore';

/** Where data lives and goes, and how to delete it. */
export function PrivacySettings() {
    const sessions = useChatStore((s) => s.sessions);
    const removeSession = useChatStore((s) => s.removeSession);
    const active = useModelStore((s) => s.active);
    const [confirm, setConfirm] = useState(false);
    const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
    const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
    useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

    const clearAll = useCallback(async () => {
        if (!confirm) {
            setConfirm(true);
            timer.current = setTimeout(() => setConfirm(false), 4000);
            return;
        }
        setConfirm(false);
        const failed: string[] = [];
        for (const s of sessions) {
            try {
                await api.deleteSession(s.id);
                removeSession(s.id);
            } catch (err) {
                failed.push(`${s.title}: ${err instanceof Error ? err.message : err}`);
            }
        }
        setResult(failed.length
            ? { ok: false, text: `Could not delete ${failed.length} chat(s): ${failed.join('; ')}` }
            : { ok: true, text: 'All chats deleted.' });
    }, [confirm, removeSession, sessions]);

    return (
        <div className="settings-pane">
            <h2 className="settings-pane-title">Privacy</h2>
            <p className="settings-pane-lede">
                Chats, documents and memory stay on this PC. {!active
                    ? "GAIA can't tell yet where answers are generated."
                    : active.remote
                        ? `Right now answers come from ${locationLabel(active)}, so each message and the chat history are sent there.`
                        : 'Right now answers are generated on this PC too.'}
            </p>
            <div className="setting-row">
                <span>Data folder</span>
                <code className="setting-path">~/.gaia/chat/</code>
            </div>
            <div className="danger-divider" />
            <p className="danger-warning">Deletes every chat and its messages. Documents and memory are kept.</p>
            <button type="button" className="btn-danger" onClick={() => void clearAll()} disabled={sessions.length === 0}>
                {confirm ? 'Click again to delete all chats' : 'Delete all chats'}
            </button>
            {result && <p className={result.ok ? 'settings-ok' : 'settings-error'} role={result.ok ? 'status' : 'alert'}>{result.text}</p>}
        </div>
    );
}
