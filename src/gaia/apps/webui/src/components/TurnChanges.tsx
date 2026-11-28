// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useState } from 'react';
import { AlertTriangle, FileDiff as FileDiffIcon, Undo2 } from 'lucide-react';
import * as api from '../services/api';
import { useTurnChanges } from '../hooks/useTurnChanges';
import { useSessionProgram } from '../hooks/useSessionProgram';
import { pluralKey, type PluralKey, type Say } from '../i18n/registry';
import { useT } from '../i18n/useT';
import { announceUndo } from '../utils/undoEvents';
import { groupUntracked, untrackedWarning } from '../utils/untrackedWork';
import { ChangesReview } from './ChangesReview';
import './TurnChanges.css';

const sayCount = (say: Say, base: PluralKey, n: number) => say(pluralKey(base, n), { n });

type Phase = 'idle' | 'confirm' | 'working' | 'conflict' | 'done';

interface TurnChangesProps {
    sessionId: string;
    messageId: number;
    /** The turn ran a tool that writes files or runs commands. */
    ranChangingTool?: boolean;
}

/** "Undo this turn" and "Review changes" under an answer whose turn changed files. */
export function TurnChanges({ sessionId, messageId, ranChangingTool = false }: TurnChangesProps) {
    const t = useT();
    const { turn, error: loadError, checked, refresh } = useTurnChanges(sessionId, messageId);
    const unrecorded = !turn || (turn.files.length === 0 && !turn.incomplete);
    // gaiacpp keeps no record of a turn's file changes, so Undo has nothing to work from.
    const program = useSessionProgram(sessionId, checked && !loadError && unrecorded && ranChangingTool);
    const [phase, setPhase] = useState<Phase>('idle');
    const [message, setMessage] = useState<string | null>(null);
    const [reviewing, setReviewing] = useState(false);

    const undo = useCallback(async (force: boolean) => {
        if (!turn) return;
        setPhase('working');
        setMessage(null);
        try {
            const result = await api.undoTurn(sessionId, turn.turn_id, force);
            setMessage(t.compose((say) => {
                const parts = [];
                if (result.restored.length) parts.push(sayCount(say, 'turnChanges.restored', result.restored.length));
                if (result.removed.length) parts.push(sayCount(say, 'turnChanges.removed', result.removed.length));
                let text = parts.length
                    ? say('turnChanges.undone', { list: parts.join(say('untracked.sep')) })
                    : say('turnChanges.undoneNothing');
                if (result.kept_folders.length) text += ` ${sayCount(say, 'turnChanges.keptFolders', result.kept_folders.length)}`;
                return text;
            }));
            setPhase('done');
            announceUndo(sessionId);
            await refresh();
        } catch (err) {
            const conflict = err instanceof api.ApiError && err.reason === 'conflict';
            setMessage(err instanceof api.ApiError ? err.detail : String(err));
            setPhase(conflict ? 'conflict' : 'idle');
        }
    }, [refresh, sessionId, t, turn]);

    if (loadError) {
        return (
            <div className="turn-changes is-error" role="note">
                <AlertTriangle size={12} aria-hidden="true" />
                <span>{t('turnChanges.loadError', { error: loadError })}</span>
            </div>
        );
    }
    if (!turn || unrecorded) {
        if (!ranChangingTool || program !== 'gaiacpp') return null;
        return (
            <div className="turn-changes is-unavailable" role="note">
                <Undo2 size={12} aria-hidden="true" />
                <span>{t('turnChanges.nativeNoUndo', { name: 'gaiacpp' })}</span>
            </div>
        );
    }

    const count = turn.files.length;
    const undone = Boolean(turn.restored_at);
    const groups = groupUntracked(turn.untracked, t);

    return (
        <div className="turn-changes" aria-label={t('turnChanges.label')}>
            {count > 0 && (
                <div className="turn-changes-row">
                    <span className="turn-changes-summary">
                        {t.plural(undone ? 'turnChanges.changedUndone' : 'turnChanges.changed', count)}
                    </span>
                    <button type="button" className="turn-changes-btn" onClick={() => setReviewing(true)}>
                        <FileDiffIcon size={12} aria-hidden="true" /> {t('chg.review')}
                    </button>
                    {!undone && phase !== 'confirm' && phase !== 'conflict' && (
                        <button
                            type="button"
                            className="turn-changes-btn"
                            onClick={() => setPhase('confirm')}
                            disabled={phase === 'working'}
                        >
                            <Undo2 size={12} aria-hidden="true" /> {phase === 'working' ? t('chg.undoing') : t('turnChanges.undo')}
                        </button>
                    )}
                    {phase === 'confirm' && (
                        <span className="turn-changes-confirm" role="group" aria-label={t('turnChanges.confirmLabel')}>
                            {t.plural('turnChanges.confirm', count)}
                            <button type="button" className="turn-changes-btn is-primary" onClick={() => void undo(false)}>{t('turnChanges.confirmUndo')}</button>
                            <button type="button" className="turn-changes-btn" onClick={() => setPhase('idle')}>{t('turnChanges.cancel')}</button>
                        </span>
                    )}
                </div>
            )}
            {turn.incomplete && (
                <div className="turn-changes-warning" role="note">
                    <AlertTriangle size={12} aria-hidden="true" />
                    <span>
                        {untrackedWarning(turn.untracked, { also: count > 0 }, t)}
                        {groups.length > 0 && (
                            <ul>
                                {groups.map((g) => (
                                    <li key={g.kind}>
                                        {g.entries.length === 1 ? (
                                            <span title={g.entries[0].detail || undefined}>{g.title}</span>
                                        ) : (
                                            <details>
                                                <summary>{g.title}</summary>
                                                <ul>
                                                    {g.entries.map((e) => (
                                                        <li key={e.detail} title={e.detail || undefined}>
                                                            {e.label || t('untracked.noDescription')}
                                                            {e.count > 1 && ` ×${e.count}`}
                                                        </li>
                                                    ))}
                                                </ul>
                                            </details>
                                        )}
                                    </li>
                                ))}
                            </ul>
                        )}
                    </span>
                </div>
            )}
            {message && (
                <div className={`turn-changes-message${phase === 'done' ? ' is-ok' : ' is-error'}`} role={phase === 'done' ? 'status' : 'alert'}>
                    {message}
                    {phase === 'conflict' && (
                        <span className="turn-changes-confirm">
                            <button type="button" className="turn-changes-btn is-danger" onClick={() => void undo(true)}>
                                {t('chg.undoAnyway')}
                            </button>
                            <button type="button" className="turn-changes-btn" onClick={() => { setPhase('idle'); setMessage(null); }}>{t('chg.keep')}</button>
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
