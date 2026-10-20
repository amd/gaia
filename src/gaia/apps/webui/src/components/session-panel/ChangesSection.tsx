// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Changes (#5278): a running total of what this chat changed on disk, a
 * review of every file across turns, Undo all, and — in a git folder —
 * Commit and Create PR, each behind a dialog where the drafted message can be
 * edited and nothing runs until the user confirms.
 */

import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, CornerDownRight, FileDiff as FileDiffIcon, GitBranch, GitCommit, GitPullRequest, Loader2, Undo2, X } from 'lucide-react';
import * as api from '../../services/api';
import { expectShape } from '../../utils/responseShape';
import { log } from '../../utils/logger';
import { useTurnEnd } from '../../hooks/useTurnEnd';
import { useEscapeToClose } from '../../hooks/useEscapeToClose';
import { refreshTurnChanges } from '../../hooks/useTurnChanges';
import { announceUndo, useUndoEvents } from '../../utils/undoEvents';
import { untrackedWarning } from '../../utils/untrackedWork';
import { DiffCard } from '../render/DiffCard';
import { PanelSection } from './PanelSection';
import type { ChatFileChange, GitRepo, SessionChanges, SessionGit } from '../../types/sessionPanel';

function plural(n: number, one: string, many = `${one}s`): string {
    return `${n} ${n === 1 ? one : many}`;
}

function errorText(err: unknown): string {
    if (err instanceof api.ApiError) return err.detail || err.message;
    return err instanceof Error ? err.message : String(err);
}

function baseName(path: string): string {
    const parts = path.split(/[\\/]/).filter(Boolean);
    return parts[parts.length - 1] ?? path;
}

/** "4 files changed, +120 −8". */
export function totalText(changes: SessionChanges): string {
    const { files, additions, deletions } = changes.totals;
    return `${plural(files, 'file')} changed, +${additions} −${deletions}`;
}

const CHANGE_LABEL: Record<ChatFileChange['change'], string> = {
    edited: 'Edited',
    created: 'Created',
    deleted: 'Deleted',
};

interface ChangesSectionProps {
    sessionId: string;
    busy: boolean;
    onJumpToMessage: (messageId: number) => boolean;
}

export function ChangesSection({ sessionId, busy, onJumpToMessage }: ChangesSectionProps) {
    const [changes, setChanges] = useState<SessionChanges | null>(null);
    const [error, setError] = useState<string | null>(null);
    // Kept here: after Undo all the list empties, and the result must stay readable.
    const [undone, setUndone] = useState<string | null>(null);

    const load = useCallback(() => {
        Promise.resolve()
            .then(() => api.getSessionChanges(sessionId))
            .then((res) => {
                setChanges(expectShape(res, (r) => Array.isArray(r.files), "list of this chat's changes"));
                setError(null);
            })
            .catch((err) => {
                log.ui.warn(`Could not total what session=${sessionId} changed`, err);
                setError(errorText(err));
            });
    }, [sessionId]);
    useEffect(load, [load]);
    useTurnEnd(busy, () => { setUndone(null); load(); });
    useUndoEvents(sessionId, load);

    const empty = !changes || changes.files.length === 0;
    // Nothing is shown for chats that changed no files.
    if (!error && empty && !undone) return null;
    return (
        <PanelSection
            id="changes"
            title="Changes"
            summary={changes && !empty ? `${plural(changes.totals.files, 'file')} · +${changes.totals.additions} −${changes.totals.deletions}` : null}
        >
            {error && <p className="sp-error">Couldn't total this chat's changes: {error}</p>}
            {changes && !empty && (
                <ChangesBody
                    sessionId={sessionId}
                    busy={busy}
                    changes={changes}
                    onChanged={load}
                    onUndone={setUndone}
                    onJumpToMessage={onJumpToMessage}
                />
            )}
            {empty && undone && <p className="sp-ok" role="status">{undone}</p>}
        </PanelSection>
    );
}

type UndoPhase = 'idle' | 'confirm' | 'working' | 'conflict' | 'done';

function ChangesBody({ sessionId, busy, changes, onChanged, onUndone, onJumpToMessage }: {
    sessionId: string;
    busy: boolean;
    changes: SessionChanges;
    onChanged: () => void;
    onUndone: (text: string) => void;
    onJumpToMessage: (messageId: number) => boolean;
}) {
    const [reviewing, setReviewing] = useState(false);
    const [phase, setPhase] = useState<UndoPhase>('idle');
    const [message, setMessage] = useState<string | null>(null);
    const count = changes.files.length;

    const undoAll = async (force: boolean) => {
        setPhase('working');
        setMessage(null);
        try {
            const result = await api.undoAllChanges(sessionId, force);
            const parts = [];
            if (result.restored.length) parts.push(`restored ${plural(result.restored.length, 'file')}`);
            if (result.removed.length) parts.push(`removed ${plural(result.removed.length, 'new file')}`);
            let text = `Undone: ${parts.join(', ') || 'no files needed changing'}.`;
            if (result.kept_folders.length) {
                text += ` Kept ${plural(result.kept_folders.length, 'folder')} the chat created, because they now hold other files.`;
            }
            setMessage(text);
            setPhase('done');
            onUndone(text);
            announceUndo(sessionId);
            await refreshTurnChanges(sessionId);
        } catch (err) {
            const conflict = (err as { reason?: unknown } | null)?.reason === 'conflict';
            setMessage(errorText(err));
            setPhase(conflict ? 'conflict' : 'idle');
        }
    };

    return (
        <div className="sp-changes">
            <p className="sp-changes-total" aria-live="polite">
                <strong>{totalText(changes)}</strong>
                {changes.totals.uncounted > 0 && (
                    <span className="sp-meta"> · {plural(changes.totals.uncounted, 'binary or large file')} not line-counted</span>
                )}
            </p>
            {changes.pending && <p className="sp-meta">A reply is still running; its changes are added when it ends.</p>}
            <ul className="sp-list">
                {changes.files.map((f) => {
                    const latest = f.message_ids[f.message_ids.length - 1];
                    return (
                        <li key={f.path} className="sp-change">
                            <div className="sp-item-text">
                                <span className="sp-item-name" title={f.path}>{baseName(f.path)}</span>
                                <span className="sp-meta">
                                    {CHANGE_LABEL[f.change]}
                                    {f.additions != null && <> · <span className="sp-add">+{f.additions}</span> <span className="sp-del">−{f.deletions}</span></>}
                                    {f.message_ids.length > 1 && ` · ${plural(f.message_ids.length, 'turn')}`}
                                </span>
                                {f.changed_since && (
                                    <span className="sp-warn-text"><AlertTriangle size={11} aria-hidden="true" /> Changed outside GAIA: {f.changed_since}</span>
                                )}
                            </div>
                            {latest != null && (
                                <button
                                    type="button"
                                    className="btn-icon-sm"
                                    onClick={() => onJumpToMessage(latest)}
                                    aria-label={`Go to the last turn that changed ${baseName(f.path)}`}
                                    title="Go to turn"
                                >
                                    <CornerDownRight size={12} />
                                </button>
                            )}
                        </li>
                    );
                })}
            </ul>
            {changes.incomplete && (
                <p className="sp-warn-text" role="note">
                    <AlertTriangle size={11} aria-hidden="true" /> {untrackedWarning(changes.untracked, { inChat: true, undo: 'Undo all' })}
                </p>
            )}
            <div className="sp-actions-row">
                <button type="button" className="sp-action" onClick={() => setReviewing(true)}>
                    <FileDiffIcon size={12} aria-hidden="true" /> Review changes
                </button>
                {phase !== 'confirm' && phase !== 'conflict' && (
                    <button
                        type="button"
                        className="sp-action"
                        onClick={() => setPhase('confirm')}
                        disabled={phase === 'working' || busy}
                        title={busy ? 'Wait for the reply to finish' : undefined}
                    >
                        <Undo2 size={12} aria-hidden="true" /> {phase === 'working' ? 'Undoing…' : 'Undo all'}
                    </button>
                )}
            </div>
            {phase === 'confirm' && (
                <div className="sp-confirm" role="group" aria-label="Confirm undo all">
                    Put {plural(count, 'file')} back the way {count === 1 ? 'it was' : 'they were'} before this chat? Every turn's file changes are undone.
                    <span className="sp-actions-row">
                        <button type="button" className="sp-action is-danger" onClick={() => void undoAll(false)}>Undo all</button>
                        <button type="button" className="sp-action" onClick={() => setPhase('idle')}>Cancel</button>
                    </span>
                </div>
            )}
            {message && (
                <div className={phase === 'done' ? 'sp-ok' : 'sp-error'} role={phase === 'done' ? 'status' : 'alert'}>
                    {message}
                    {phase === 'conflict' && (
                        <span className="sp-actions-row">
                            <button type="button" className="sp-action is-danger" onClick={() => void undoAll(true)}>
                                Undo anyway and lose those changes
                            </button>
                            <button type="button" className="sp-action" onClick={() => { setPhase('idle'); setMessage(null); }}>Keep them</button>
                        </span>
                    )}
                </div>
            )}
            <GitActions sessionId={sessionId} busy={busy} onCommitted={onChanged} />
            {reviewing && <ChatChangesReview changes={changes} onClose={() => setReviewing(false)} />}
        </div>
    );
}

function note(file: ChatFileChange): string | undefined {
    if (file.too_large) return `Too large to show: ${file.before_size} → ${file.after_size} bytes.`;
    if (file.binary) return `Binary file: ${file.before_size} → ${file.after_size} bytes. No text diff.`;
    if (file.truncated) return 'Diff shortened: the change is too long to show in full.';
    return undefined;
}

/** Every file the chat changed, from before its first turn to after its last. */
function ChatChangesReview({ changes, onClose }: { changes: SessionChanges; onClose: () => void }) {
    useEscapeToClose(true, onClose);
    return (
        <div className="modal-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-label="Review this chat's changes">
            <div className="modal-panel changes-review" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <h3>Changes in this chat · {totalText(changes)}</h3>
                    <button type="button" className="btn-icon" onClick={onClose} aria-label="Close">
                        <X size={18} />
                    </button>
                </div>
                <div className="modal-body changes-review-body">
                    {changes.files.map((file) => (
                        <section key={file.path} className="changes-review-file">
                            <DiffCard
                                data={{
                                    title: file.path,
                                    badge: CHANGE_LABEL[file.change],
                                    note: note(file),
                                    unified: file.binary || file.too_large ? '' : file.unified,
                                }}
                            />
                        </section>
                    ))}
                </div>
            </div>
        </div>
    );
}

function GitActions({ sessionId, busy, onCommitted }: { sessionId: string; busy: boolean; onCommitted: () => void }) {
    const [git, setGit] = useState<SessionGit | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [dialog, setDialog] = useState<{ kind: 'commit' | 'pr'; repo: GitRepo } | null>(null);
    const [done, setDone] = useState<string | null>(null);

    const load = useCallback(() => {
        Promise.resolve()
            .then(() => api.getSessionGit(sessionId))
            .then((res) => {
                setGit(expectShape(res, (r) => Array.isArray(r.repos), 'list of git repositories'));
                setError(null);
            })
            .catch((err) => setError(errorText(err)));
    }, [sessionId]);
    useEffect(load, [load]);
    useTurnEnd(busy, load);
    useUndoEvents(sessionId, load);

    if (error) return <p className="sp-error">Couldn't check for a git repository: {error}</p>;
    if (!git || !git.available || git.repos.length === 0) return null;

    return (
        <div className="sp-git">
            {git.repos.map((repo) => (
                <div key={repo.root} className="sp-git-repo">
                    <span className="sp-meta" title={repo.root}>
                        <GitBranch size={12} aria-hidden="true" /> {baseName(repo.root)} · {repo.branch}
                        {repo.uncommitted.length === 0 && repo.files.length > 0 && ' · committed'}
                    </span>
                    <span className="sp-actions-row">
                        <button
                            type="button"
                            className="sp-action"
                            disabled={busy || repo.uncommitted.length === 0}
                            onClick={() => { setDone(null); setDialog({ kind: 'commit', repo }); }}
                        >
                            <GitCommit size={12} aria-hidden="true" /> Commit…
                        </button>
                        <button
                            type="button"
                            className="sp-action"
                            disabled={busy || repo.pr_blocked != null}
                            title={repo.pr_blocked ?? undefined}
                            onClick={() => { setDone(null); setDialog({ kind: 'pr', repo }); }}
                        >
                            <GitPullRequest size={12} aria-hidden="true" /> Create PR…
                        </button>
                    </span>
                    {repo.pr_blocked && <span className="sp-meta">{repo.pr_blocked}</span>}
                </div>
            ))}
            {done && <p className="sp-ok" role="status">{done}</p>}
            {dialog && (
                <GitDialog
                    sessionId={sessionId}
                    kind={dialog.kind}
                    repo={dialog.repo}
                    onClose={() => setDialog(null)}
                    onDone={(text) => { setDialog(null); setDone(text); load(); onCommitted(); }}
                />
            )}
        </div>
    );
}

/** The message drafted from the chat's turns, editable; nothing runs until the user confirms. */
function GitDialog({ sessionId, kind, repo, onClose, onDone }: {
    sessionId: string;
    kind: 'commit' | 'pr';
    repo: GitRepo;
    onClose: () => void;
    onDone: (text: string) => void;
}) {
    const [title, setTitle] = useState(repo.draft.title);
    const [body, setBody] = useState(repo.draft.body);
    const [running, setRunning] = useState(false);
    const [error, setError] = useState<string | null>(null);
    useEscapeToClose(!running, onClose);

    const run = async () => {
        setRunning(true);
        setError(null);
        try {
            if (kind === 'commit') {
                const res = await api.commitChanges(sessionId, repo.root, `${title.trim()}\n\n${body.trim()}`.trim());
                onDone(`Committed ${plural(res.files.length, 'file')} as ${res.commit}.`);
            } else {
                const res = await api.createPullRequest(sessionId, repo.root, title, body);
                onDone(`Opened ${res.url}`);
            }
        } catch (err) {
            setError(errorText(err));
            setRunning(false);
        }
    };

    const files = kind === 'commit' ? repo.uncommitted : repo.files;
    return (
        <div className="modal-overlay" onClick={running ? undefined : onClose}>
            <div
                className="modal-panel sp-git-dialog"
                role="dialog"
                aria-modal="true"
                aria-label={kind === 'commit' ? 'Commit this chat’s changes' : 'Create a pull request'}
                onClick={(e) => e.stopPropagation()}
            >
                <div className="modal-header">
                    <h3>{kind === 'commit' ? 'Commit this chat’s changes' : 'Create a pull request'}</h3>
                    <button type="button" className="btn-icon" onClick={onClose} disabled={running} aria-label="Close">
                        <X size={18} />
                    </button>
                </div>
                <div className="modal-body sp-git-body">
                    <p className="sp-meta">
                        {kind === 'commit'
                            ? `In ${repo.root} on ${repo.branch}. Only the files below are committed; anything else you changed stays as it is.`
                            : `Pushes ${repo.branch} to ${repo.remote} (never forced), then opens a pull request with GitHub's gh tool.`}
                    </p>
                    <label className="sp-field">
                        <span>{kind === 'commit' ? 'Summary' : 'Title'}</span>
                        <input value={title} onChange={(e) => setTitle(e.target.value)} disabled={running} />
                    </label>
                    <label className="sp-field">
                        <span>{kind === 'commit' ? 'Details' : 'Description'}</span>
                        <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={7} disabled={running} />
                    </label>
                    <div className="sp-meta">{plural(files.length, 'file')}: {files.join(', ')}</div>
                    {kind === 'commit' && repo.ignored.length > 0 && (
                        <div className="sp-meta">
                            Left out, because git ignores {repo.ignored.length === 1 ? 'it' : 'them'}: {repo.ignored.join(', ')}
                        </div>
                    )}
                    {error && <p className="sp-error" role="alert">{error}</p>}
                </div>
                <div className="sp-picker-actions">
                    <button type="button" className="btn-secondary" onClick={onClose} disabled={running}>Cancel</button>
                    <button type="button" className="btn-primary" onClick={() => void run()} disabled={running || !title.trim()}>
                        {running && <Loader2 size={13} className="spin" aria-hidden="true" />}
                        {kind === 'commit' ? `Commit ${plural(files.length, 'file')}` : 'Push and open PR'}
                    </button>
                </div>
            </div>
        </div>
    );
}
