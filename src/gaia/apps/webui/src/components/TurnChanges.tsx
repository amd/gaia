// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useState } from 'react';
import { AlertTriangle, FileDiff as FileDiffIcon, Undo2 } from 'lucide-react';
import * as api from '../services/api';
import { useTurnChanges } from '../hooks/useTurnChanges';
import { announceUndo } from '../utils/undoEvents';
import type { UntrackedWork } from '../types/changes';
import { ChangesReview } from './ChangesReview';
import './TurnChanges.css';

function plural(n: number, one: string, many = `${one}s`): string {
    return `${n} ${n === 1 ? one : many}`;
}

function untrackedLabel(u: UntrackedWork): string {
    switch (u.kind) {
        case 'shell': return `Command: ${u.detail}`;
        case 'run_python': return 'A Python snippet';
        case 'execute_python_file': return `Python script: ${u.detail}`;
        case 'mcp': return `Connected tool: ${u.detail}`;
        default: return `${u.kind}: ${u.detail}`;
    }
}

type Phase = 'idle' | 'confirm' | 'working' | 'conflict' | 'done';

/** "Undo this turn" and "Review changes" under an answer whose turn changed files. */
export function TurnChanges({ sessionId, messageId }: { sessionId: string; messageId: number }) {
    const { turn, error: loadError, refresh } = useTurnChanges(sessionId, messageId);
    const [phase, setPhase] = useState<Phase>('idle');
    const [message, setMessage] = useState<string | null>(null);
    const [reviewing, setReviewing] = useState(false);

    const undo = useCallback(async (force: boolean) => {
        if (!turn) return;
        setPhase('working');
        setMessage(null);
        try {
            const result = await api.undoTurn(sessionId, turn.turn_id, force);
            const parts = [];
            if (result.restored.length) parts.push(`restored ${plural(result.restored.length, 'file')}`);
            if (result.removed.length) parts.push(`removed ${plural(result.removed.length, 'new file')}`);
            let text = `Undone: ${parts.join(', ') || 'no files needed changing'}.`;
            if (result.kept_folders.length) {
                text += ` Kept ${plural(result.kept_folders.length, 'folder')} it created, because they now hold other files.`;
            }
            setMessage(text);
            setPhase('done');
            announceUndo(sessionId);
            await refresh();
        } catch (err) {
            const conflict = err instanceof api.ApiError && err.reason === 'conflict';
            setMessage(err instanceof api.ApiError ? err.detail : String(err));
            setPhase(conflict ? 'conflict' : 'idle');
        }
    }, [refresh, sessionId, turn]);

    if (loadError) {
        return (
            <div className="turn-changes is-error" role="note">
                <AlertTriangle size={12} aria-hidden="true" />
                <span>Could not check this turn's file changes: {loadError}</span>
            </div>
        );
    }
    if (!turn || (turn.files.length === 0 && !turn.incomplete)) return null;

    const count = turn.files.length;
    const undone = Boolean(turn.restored_at);

    return (
        <div className="turn-changes" aria-label="File changes from this turn">
            <div className="turn-changes-row">
                <span className="turn-changes-summary">
                    {count > 0 ? `Changed ${plural(count, 'file')}` : 'Ran commands that may have changed files'}
                    {undone && ' · undone'}
                </span>
                {count > 0 && (
                    <button type="button" className="turn-changes-btn" onClick={() => setReviewing(true)}>
                        <FileDiffIcon size={12} aria-hidden="true" /> Review changes
                    </button>
                )}
                {count > 0 && !undone && phase !== 'confirm' && phase !== 'conflict' && (
                    <button
                        type="button"
                        className="turn-changes-btn"
                        onClick={() => setPhase('confirm')}
                        disabled={phase === 'working'}
                    >
                        <Undo2 size={12} aria-hidden="true" /> {phase === 'working' ? 'Undoing…' : 'Undo this turn'}
                    </button>
                )}
                {phase === 'confirm' && (
                    <span className="turn-changes-confirm" role="group" aria-label="Confirm undo">
                        Put {plural(count, 'file')} back the way {count === 1 ? 'it was' : 'they were'} before this turn?
                        <button type="button" className="turn-changes-btn is-primary" onClick={() => void undo(false)}>Undo</button>
                        <button type="button" className="turn-changes-btn" onClick={() => setPhase('idle')}>Cancel</button>
                    </span>
                )}
            </div>
            {turn.incomplete && (
                <div className="turn-changes-warning" role="note">
                    <AlertTriangle size={12} aria-hidden="true" />
                    <span>
                        Undo may be incomplete: GAIA can't see or undo what these did.
                        <ul>
                            {turn.untracked.map((u, i) => <li key={i}>{untrackedLabel(u)}</li>)}
                        </ul>
                    </span>
                </div>
            )}
            {message && (
                <div className={`turn-changes-message${phase === 'done' ? ' is-ok' : ' is-error'}`} role={phase === 'done' ? 'status' : 'alert'}>
                    {message}
                    {phase === 'conflict' && (
                        <span className="turn-changes-confirm">
                            <button type="button" className="turn-changes-btn is-danger" onClick={() => void undo(true)}>
                                Undo anyway and lose those changes
                            </button>
                            <button type="button" className="turn-changes-btn" onClick={() => { setPhase('idle'); setMessage(null); }}>Keep them</button>
                        </span>
                    )}
                </div>
            )}
            {reviewing && (
                <ChangesReview sessionId={sessionId} turn={turn} onClose={() => setReviewing(false)} />
            )}
        </div>
    );
}
