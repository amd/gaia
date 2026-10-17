// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Outputs (#5277): everything this chat produced, newest first, each tied to its turn. */

import { useCallback, useEffect, useState } from 'react';
import {
    Brain, CalendarClock, CornerDownRight, FileOutput, FileText, FolderOpen, Image as ImageIcon, Library,
} from 'lucide-react';
import * as api from '../../services/api';
import { expectShape } from '../../utils/responseShape';
import { log } from '../../utils/logger';
import { useTurnEnd } from '../../hooks/useTurnEnd';
import { useUndoEvents } from '../../utils/undoEvents';
import { PanelSection } from './PanelSection';
import type { OutputItem, OutputKind } from '../../types/sessionPanel';

/** Rows shown before "Show more". */
export const OUTPUTS_SHOWN = 5;

const KIND: Record<OutputKind, { label: string; Icon: typeof FileText }> = {
    file: { label: 'File', Icon: FileText },
    export: { label: 'Export', Icon: FileOutput },
    image: { label: 'Image', Icon: ImageIcon },
    document: { label: 'Indexed document', Icon: Library },
    memory: { label: 'Memory saved', Icon: Brain },
    schedule: { label: 'Scheduled task', Icon: CalendarClock },
};

const CHANGE_LABEL = { created: 'created', edited: 'edited', deleted: 'deleted' } as const;

function errorText(err: unknown): string {
    if (err instanceof api.ApiError) return err.detail || err.message;
    return err instanceof Error ? err.message : String(err);
}

/** "3 min ago", from a UTC `YYYY-MM-DD HH:MM:SS`. */
export function whenText(utc: string | null, now = Date.now()): string {
    if (!utc) return '';
    const t = Date.parse(`${utc.replace(' ', 'T')}Z`);
    if (Number.isNaN(t)) return '';
    const s = Math.max(0, Math.round((now - t) / 1000));
    if (s < 60) return 'just now';
    if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
    return new Date(t).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function stateText(item: OutputItem): string | null {
    if (item.state === 'undone') return 'Undone';
    if (item.state === 'gone') return 'No longer on disk';
    return null;
}

interface OutputsSectionProps {
    sessionId: string;
    busy: boolean;
    onJumpToMessage: (messageId: number) => boolean;
}

export function OutputsSection({ sessionId, busy, onJumpToMessage }: OutputsSectionProps) {
    return (
        <PanelSection id="outputs" title="Outputs">
            <OutputsList sessionId={sessionId} busy={busy} onJumpToMessage={onJumpToMessage} />
        </PanelSection>
    );
}

function OutputsList({ sessionId, busy, onJumpToMessage }: OutputsSectionProps) {
    const [items, setItems] = useState<OutputItem[] | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [actionError, setActionError] = useState<string | null>(null);
    const [all, setAll] = useState(false);

    const load = useCallback(() => {
        let live = true;
        Promise.resolve()
            .then(() => api.getOutputs(sessionId))
            .then((res) => {
                if (!live) return;
                setItems(expectShape(res, (r) => Array.isArray(r.items), 'list of outputs').items);
                setError(null);
            })
            .catch((err) => {
                if (!live) return;
                log.ui.warn(`Could not list what session=${sessionId} produced`, err);
                setError(errorText(err));
            });
        return () => { live = false; };
    }, [sessionId]);
    useEffect(load, [load]);
    useTurnEnd(busy, load);
    // Undo elsewhere (an answer's row, Undo all) changes what is on disk.
    useUndoEvents(sessionId, load);

    const act = (op: Promise<unknown>, name: string) => {
        setActionError(null);
        op.catch((err) => setActionError(`Couldn't open ${name}: ${errorText(err)}`));
    };

    if (error) return <p className="sp-error">Couldn't list what this chat produced: {error}</p>;
    if (!items) return <p className="sp-meta">Checking…</p>;
    if (items.length === 0) return <p className="sp-meta">Nothing yet. Files, documents, images and memories this chat makes appear here.</p>;

    const shown = all ? items : items.slice(0, OUTPUTS_SHOWN);
    return (
        <>
            <ul className="sp-list" aria-label="What this chat produced">
                {shown.map((item) => {
                    const { label, Icon } = KIND[item.kind];
                    const state = stateText(item);
                    const changed = item.change && item.kind !== 'document' ? CHANGE_LABEL[item.change] : undefined;
                    const kindText = changed ? `${label} ${changed}` : label;
                    return (
                        <li key={item.id} className={`sp-output${item.state !== 'present' ? ' is-inactive' : ''}`}>
                            <Icon size={13} className="sp-item-icon" aria-hidden="true" />
                            <div className="sp-item-text">
                                <span className="sp-item-name" title={item.path ?? item.name}>{item.name}</span>
                                <span className="sp-meta">
                                    {[kindText, whenText(item.created_at), state].filter(Boolean).join(' · ')}
                                </span>
                                <span className="sp-item-actions">
                                    {item.message_id != null && (
                                        <button
                                            type="button"
                                            className="sp-action"
                                            onClick={() => {
                                                if (!onJumpToMessage(item.message_id as number)) {
                                                    setActionError("That turn isn't loaded in the chat right now.");
                                                }
                                            }}
                                            aria-label={`Go to the turn that made ${item.name}`}
                                        >
                                            <CornerDownRight size={11} aria-hidden="true" /> Go to turn
                                        </button>
                                    )}
                                    {item.openable && item.path && (
                                        <button type="button" className="sp-action" onClick={() => act(api.openFileInApp(item.path as string), item.name)}>
                                            Open
                                        </button>
                                    )}
                                    {item.revealable && item.path && (
                                        <button type="button" className="sp-action" onClick={() => act(api.openFileOrFolder(item.path as string, true), item.name)}>
                                            <FolderOpen size={11} aria-hidden="true" /> Show in folder
                                        </button>
                                    )}
                                </span>
                            </div>
                        </li>
                    );
                })}
            </ul>
            {items.length > OUTPUTS_SHOWN && (
                <button type="button" className="link-btn sp-more" onClick={() => setAll((v) => !v)}>
                    {all ? 'Show fewer' : `Show ${items.length - OUTPUTS_SHOWN} more`}
                </button>
            )}
            {actionError && <p className="sp-error" role="alert">{actionError}</p>}
        </>
    );
}
