// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * "This answer" (#5222), the session panel's first section: the pages and
 * document passages the latest turn used, the files it wrote, and where its
 * data went — the model's location, the sites contacted, and the cost. Built
 * from the backend's `turn_summary`, so a cloud turn names its provider and a
 * local one says so.
 */

import { useEffect, useState, type ReactNode } from 'react';
import {
    AlertTriangle, ChevronRight, Cloud, ExternalLink, FileText, FolderOpen,
    Globe, Monitor,
} from 'lucide-react';
import * as api from '../services/api';
import { displayModelName, useModelNames } from '../stores/modelStore';
import { log } from '../utils/logger';
import { safeUrlTransform } from '../utils/markdown';
import type { Message, TurnSummary } from '../types';
import { pluralKey, type Say } from '../i18n/registry';
import { tEn, useT, type Translate } from '../i18n/useT';
import './SideRail.css';

/** What the rail shows: the latest answer's summary, a turn in progress, or nothing. */
export type RailState = { kind: 'turn'; turn: Message } | { kind: 'busy' } | null;

export function railState(messages: Message[], busy: boolean): RailState {
    // A running turn never shows the previous answer's data as its own.
    if (busy) return messages.some((m) => m.turnSummary) ? { kind: 'busy' } : null;
    const last = messages[messages.length - 1];
    if (!last || last.role !== 'assistant' || !last.turnSummary) return null;
    return { kind: 'turn', turn: last };
}

/** Every internet tool this turn failed, so the answer has no live data. */
export function liveDataFailed(summary: TurnSummary): boolean {
    const attempted = summary.live_data?.attempted ?? 0;
    return attempted > 0 && (summary.live_data?.failed ?? 0) >= attempted;
}

function hostOf(url: string): string {
    try {
        return new URL(url).hostname.replace(/^www\./, '');
    } catch {
        return url;
    }
}

/** "pp. 5, 6, 8 · 6 passages", written once per language. */
function documentMeta(say: Say, pages: number[] | undefined, passages: number): string {
    const parts: string[] = [];
    if (pages && pages.length > 0) parts.push(say(pluralKey('rail.pages', pages.length), { list: pages.join(', ') }));
    parts.push(say(pluralKey('rail.passages', passages), { n: passages }));
    return parts.join(' · ');
}

function costText(summary: TurnSummary, t: Translate = tEn): string {
    const { cost } = summary;
    if (cost.kind === 'local') return t('rail.costLocal');
    if (cost.kind === 'estimate' && typeof cost.usd === 'number') {
        const usd = cost.usd < 0.01 ? cost.usd.toFixed(4) : cost.usd.toFixed(2);
        return t('rail.costAbout', { usd });
    }
    return t(summary.model ? 'rail.costUnknownModel' : 'rail.costUnknown');
}

function errorText(err: unknown): string {
    if (err instanceof api.ApiError) return err.detail || err.message;
    return err instanceof Error ? err.message : String(err);
}

interface ThisAnswerProps {
    messages: Message[];
    /** A turn is running, so the latest answer is not the one being written. */
    busy?: boolean;
}

/** The latest answer's sources, files and where its data went: the session panel's first section. */
export function ThisAnswer({ messages, busy = false }: ThisAnswerProps) {
    const t = useT();
    const state = railState(messages, busy);
    if (!state) return null;
    const summary = state.kind === 'turn' ? state.turn.turnSummary ?? null : null;
    const noLiveData = summary ? liveDataFailed(summary) : false;
    return (
        <div className="side-rail-body">
            {!summary && (
                <p className="side-rail-pending" role="status">
                    {t('rail.pending')}
                </p>
            )}
            {noLiveData && (
                <div className="side-rail-warn" role="status">
                    <AlertTriangle size={14} aria-hidden="true" />
                    <div>
                        <strong>{t('rail.noLive')}</strong>
                        <span>{t('rail.noLiveWhy')}</span>
                    </div>
                </div>
            )}
            {summary && (summary.error ? (
                <p className="side-rail-error" role="alert">
                    {t('rail.error', { error: summary.error })}
                </p>
            ) : (
                <>
                    <SourcesSection summary={summary} />
                    <FilesSection summary={summary} />
                    <ThisTurnSection summary={summary} />
                </>
            ))}
        </div>
    );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
    const [open, setOpen] = useState(true);
    return (
        <section className="side-rail-section">
            <button
                type="button"
                className="side-rail-section-head"
                onClick={() => setOpen(!open)}
                aria-expanded={open}
            >
                <ChevronRight size={12} className={`side-rail-chevron${open ? ' open' : ''}`} aria-hidden="true" />
                {title}
            </button>
            {open && <div className="side-rail-section-body">{children}</div>}
        </section>
    );
}

function SourcesSection({ summary }: { summary: TurnSummary }) {
    const t = useT();
    const pages = summary.pages ?? [];
    const documents = summary.documents ?? [];
    if (pages.length === 0 && documents.length === 0) return null;
    return (
        <Section title={t('rail.sources')}>
            <ul className="side-rail-list">
                {pages.map((p) => (
                    <li key={p.url} className="side-rail-item">
                        <Globe size={13} className="side-rail-item-icon" aria-hidden="true" />
                        <div className="side-rail-item-text">
                            <a href={safeUrlTransform(p.url) || undefined} target="_blank" rel="noopener noreferrer" className="side-rail-link" title={p.url}>
                                <span className="side-rail-link-text">{p.title || hostOf(p.url)}</span>
                                <ExternalLink size={10} aria-hidden="true" className="side-rail-link-icon" />
                            </a>
                            <span className="side-rail-meta">
                                {hostOf(p.url)}{p.kind === 'search_result' ? ` · ${t('rail.searchResult')}` : ''}
                            </span>
                        </div>
                    </li>
                ))}
                {documents.map((d) => {
                    const count = d.passages ?? 0;
                    return (
                        <li key={d.path} className="side-rail-item">
                            <FileText size={13} className="side-rail-item-icon" aria-hidden="true" />
                            <div className="side-rail-item-text">
                                <span className="side-rail-name" title={d.name} dir="auto">{d.name}</span>
                                <span className="side-rail-meta">
                                    {t.compose((say) => documentMeta(say, d.pages, count))}
                                </span>
                            </div>
                        </li>
                    );
                })}
            </ul>
        </Section>
    );
}

/** How often listed files are re-checked, so a later delete or edit shows up. */
export const FILE_RECHECK_MS = 4000;

interface FileState {
    exists?: boolean | null;
    mtime?: number | null;
}

/** The current state of *paths*, re-read on show, on focus, and every few seconds. */
function useFileStatus(paths: string[]) {
    const key = paths.join('\n');
    const [status, setStatus] = useState<Map<string, FileState>>(() => new Map());
    const [error, setError] = useState<string | null>(null);
    useEffect(() => {
        if (!key) return;
        const list = key.split('\n');
        let cancelled = false;
        const check = () => {
            if (document.visibilityState === 'hidden') return;
            api.getFileStatus(list).then(
                (res) => {
                    if (cancelled) return;
                    setStatus(new Map(res.files.map((f) => [f.path, f])));
                    setError(null);
                },
                (err) => {
                    if (cancelled) return;
                    log.ui.warn('Could not re-check the files this turn wrote', err);
                    setError(errorText(err));
                },
            );
        };
        check();
        const timer = window.setInterval(check, FILE_RECHECK_MS);
        window.addEventListener('focus', check);
        return () => {
            cancelled = true;
            window.clearInterval(timer);
            window.removeEventListener('focus', check);
        };
    }, [key]);
    return { status, error };
}

function FilesSection({ summary }: { summary: TurnSummary }) {
    const t = useT();
    const files = summary.files ?? [];
    const [error, setError] = useState<string | null>(null);
    const { status, error: checkError } = useFileStatus(files.map((f) => f.path));
    if (files.length === 0) return null;

    const run = (action: Promise<unknown>, name: string) => {
        setError(null);
        action.catch((err) => {
            log.ui.error(`Failed to open ${name}`, err);
            setError(t('sp.couldntOpen', { name, error: errorText(err) }));
        });
    };

    return (
        <Section title={t('rail.files')}>
            <ul className="side-rail-list">
                {files.map((f) => {
                    const now = status.get(f.path);
                    if (now?.exists === false) {
                        return (
                            <li key={f.path} className="side-rail-item side-rail-item-gone">
                                <FileText size={13} className="side-rail-item-icon" aria-hidden="true" />
                                <div className="side-rail-item-text">
                                    <span className="side-rail-name" title={f.path}>{f.name}</span>
                                    <span className="side-rail-meta">{t('sp.gone')}</span>
                                </div>
                            </li>
                        );
                    }
                    const changed = now?.exists === true && typeof now.mtime === 'number'
                        && typeof f.mtime === 'number' && now.mtime !== f.mtime;
                    return (
                        <li key={f.path} className="side-rail-item">
                            <FileText size={13} className="side-rail-item-icon" aria-hidden="true" />
                            <div className="side-rail-item-text">
                                <span className="side-rail-name" title={f.path}>{f.name}</span>
                                {changed && <span className="side-rail-meta">{t('rail.changedSince')}</span>}
                                <span className="side-rail-actions">
                                    {f.openable && (
                                        <button type="button" className="side-rail-action" onClick={() => run(api.openFileInApp(f.path), f.name)}>
                                            {t('sp.open')}
                                        </button>
                                    )}
                                    <button type="button" className="side-rail-action" onClick={() => run(api.openFileOrFolder(f.path, true), f.name)}>
                                        <FolderOpen size={11} aria-hidden="true" />
                                        {t('sp.showInFolder')}
                                    </button>
                                </span>
                            </div>
                        </li>
                    );
                })}
            </ul>
            {error && <p className="side-rail-error" role="alert">{error}</p>}
            {checkError && (
                <p className="side-rail-error">{t('rail.checkFailed', { error: checkError })}</p>
            )}
        </Section>
    );
}

function ThisTurnSection({ summary }: { summary: TurnSummary }) {
    const t = useT();
    useModelNames();
    const { model } = summary;
    const sites = summary.sites ?? [];
    const usedDocs = (summary.documents ?? []).length > 0;
    return (
        <Section title={t('rail.thisTurn')}>
            <dl className="side-rail-facts">
                <dt>{t('rail.model')}</dt>
                <dd>
                    {model ? (
                        <span className={`side-rail-where${model.remote ? ' remote' : ''}`}>
                            {model.remote ? <Cloud size={12} aria-hidden="true" /> : <Monitor size={12} aria-hidden="true" />}
                            {model.remote ? t('rail.cloudModel', { name: model.provider_name }) : t('rail.thisPc')}
                        </span>
                    ) : (
                        <span className="side-rail-where">{t('rail.notReported')}</span>
                    )}
                    {model?.name && <span className="side-rail-meta" title={model.name} dir="ltr">{displayModelName(model.name)}</span>}
                </dd>
                {usedDocs && (
                    <>
                        <dt>{t('rail.documents')}</dt>
                        <dd>
                            {model?.remote
                                ? t('rail.docsSent', { provider: model.provider_name })
                                : t(model ? 'rail.docsLocal' : 'rail.docsSearched')}
                        </dd>
                    </>
                )}
                <dt>{t('rail.websites')}</dt>
                <dd>{sites.length > 0 ? <span dir="ltr">{sites.join(', ')}</span> : t('rail.noSites')}</dd>
                <dt>{t('rail.cost')}</dt>
                <dd>{costText(summary, t)}</dd>
            </dl>
        </Section>
    );
}
