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
import { ref, type MessageKey, type Params } from '../../i18n/registry';
import { tEn, useT, type Translate } from '../../i18n/useT';

/** Rows shown before "Show more". */
export const OUTPUTS_SHOWN = 5;

const KIND: Record<OutputKind, { label: MessageKey; Icon: typeof FileText }> = {
    file: { label: 'out.kind.file', Icon: FileText },
    export: { label: 'out.kind.export', Icon: FileOutput },
    image: { label: 'out.kind.image', Icon: ImageIcon },
    document: { label: 'out.kind.document', Icon: Library },
    memory: { label: 'out.kind.memory', Icon: Brain },
    schedule: { label: 'out.kind.schedule', Icon: CalendarClock },
};

const CHANGE_LABEL = { created: 'out.change.created', edited: 'out.change.edited', deleted: 'out.change.deleted' } as const;

function errorText(err: unknown): string {
    if (err instanceof api.ApiError) return err.detail || err.message;
    return err instanceof Error ? err.message : String(err);
}

/** "3 min ago", from a UTC `YYYY-MM-DD HH:MM:SS`. */
export function whenText(utc: string | null, now = Date.now(), t: Translate = tEn): string {
    const when = whenMessage(utc, now);
    return when == null ? '' : typeof when === 'string' ? when : t(when.key, when.params);
}

/** "3 min ago" as a message, a plain date once older than a day, or null. */
function whenMessage(utc: string | null, now: number): { key: MessageKey; params?: Params } | string | null {
    if (!utc) return null;
    const at = Date.parse(`${utc.replace(' ', 'T')}Z`);
    if (Number.isNaN(at)) return null;
    const s = Math.max(0, Math.round((now - at) / 1000));
    if (s < 60) return { key: 'out.justNow' };
    if (s < 3600) return { key: 'out.minAgo', params: { n: Math.floor(s / 60) } };
    if (s < 86400) return { key: 'out.hAgo', params: { n: Math.floor(s / 3600) } };
    return new Date(at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function stateKey(item: OutputItem): MessageKey | null {
    if (item.state === 'undone') return 'out.undone';
    if (item.state === 'gone') return 'sp.gone';
    return null;
}

interface OutputsSectionProps {
    sessionId: string;
    busy: boolean;
    onJumpToMessage: (messageId: number) => boolean;
}

export function OutputsSection({ sessionId, busy, onJumpToMessage }: OutputsSectionProps) {
    const t = useT();
    return (
        <PanelSection id="outputs" title={t('out.title')}>
            <OutputsList sessionId={sessionId} busy={busy} onJumpToMessage={onJumpToMessage} />
        </PanelSection>
    );
}

function OutputsList({ sessionId, busy, onJumpToMessage }: OutputsSectionProps) {
    const t = useT();
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
        op.catch((err) => setActionError(t('sp.couldntOpen', { name, error: errorText(err) })));
    };

    if (error) return <p className="sp-error">{t('out.listError', { error })}</p>;
    if (!items) return <p className="sp-meta">{t('sp.checking')}</p>;
    if (items.length === 0) return <p className="sp-meta">{t('out.empty')}</p>;

    const shown = all ? items : items.slice(0, OUTPUTS_SHOWN);
    return (
        <>
            <ul className="sp-list" aria-label={t('out.listLabel')}>
                {shown.map((item) => {
                    const { label, Icon } = KIND[item.kind];
                    const state = stateKey(item);
                    const changed = item.change && item.kind !== 'document' ? CHANGE_LABEL[item.change] : undefined;
                    const when = whenMessage(item.created_at, Date.now());
                    // One line per language, bracketed once: kind, time, state.
                    const meta = t.compose((say) => [
                        changed ? say('out.kindChanged', { kind: ref(label), change: ref(changed) }) : say(label),
                        when == null ? '' : typeof when === 'string' ? when : say(when.key, when.params),
                        state ? say(state) : '',
                    ].filter(Boolean).join(' · '));
                    return (
                        <li key={item.id} className={`sp-output${item.state !== 'present' ? ' is-inactive' : ''}`}>
                            <Icon size={13} className="sp-item-icon" aria-hidden="true" />
                            <div className="sp-item-text">
                                <span className="sp-item-name" title={item.path ?? item.name}>{item.name}</span>
                                <span className="sp-meta">
                                    {meta}
                                </span>
                                <span className="sp-item-actions">
                                    {item.message_id != null && (
                                        <button
                                            type="button"
                                            className="sp-action"
                                            onClick={() => {
                                                if (!onJumpToMessage(item.message_id as number)) {
                                                    setActionError(t('out.notLoaded'));
                                                }
                                            }}
                                            aria-label={t('out.goToAria', { name: item.name })}
                                        >
                                            <CornerDownRight size={11} aria-hidden="true" /> {t('sp.goToTurn')}
                                        </button>
                                    )}
                                    {item.openable && item.path && (
                                        <button type="button" className="sp-action" onClick={() => act(api.openFileInApp(item.path as string), item.name)}>
                                            {t('sp.open')}
                                        </button>
                                    )}
                                    {item.revealable && item.path && (
                                        <button type="button" className="sp-action" onClick={() => act(api.openFileOrFolder(item.path as string, true), item.name)}>
                                            <FolderOpen size={11} aria-hidden="true" /> {t('sp.showInFolder')}
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
                    {all ? t('out.fewer') : t('out.more', { n: items.length - OUTPUTS_SHOWN })}
                </button>
            )}
            {actionError && <p className="sp-error" role="alert">{actionError}</p>}
        </>
    );
}
