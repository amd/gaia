// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The rail beside the answer (#5222): the pages and document passages the
 * latest turn used, the files it wrote, and where its data went — the model's
 * location, the sites contacted, and the cost. Built from the backend's
 * `turn_summary`, so a cloud turn names its provider and a local one says so.
 */

import { useEffect, useState, type ReactNode } from 'react';
import {
    AlertTriangle, ChevronRight, Cloud, ExternalLink, FileText, FolderOpen,
    Globe, Monitor, PanelRightClose, PanelRightOpen, X,
} from 'lucide-react';
import * as api from '../services/api';
import { log } from '../utils/logger';
import { safeUrlTransform } from '../utils/markdown';
import type { Message, TurnSummary } from '../types';
import './SideRail.css';

/** Below this window width the rail folds into a button. */
export const SIDE_RAIL_FOLD_QUERY = '(max-width: 1099px)';
const COLLAPSED_KEY = 'gaia.sideRail.collapsed';

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

function useFolded(): boolean {
    const query = () => typeof window.matchMedia === 'function'
        && window.matchMedia(SIDE_RAIL_FOLD_QUERY).matches;
    const [folded, setFolded] = useState(query);
    useEffect(() => {
        if (typeof window.matchMedia !== 'function') return;
        const mq = window.matchMedia(SIDE_RAIL_FOLD_QUERY);
        const sync = () => setFolded(mq.matches);
        sync();
        mq.addEventListener('change', sync);
        return () => mq.removeEventListener('change', sync);
    }, []);
    return folded;
}

function readCollapsed(): boolean {
    try {
        return window.localStorage.getItem(COLLAPSED_KEY) === '1';
    } catch (err) {
        log.ui.warn('Side rail state unavailable; starting open', err);
        return false;
    }
}

function writeCollapsed(collapsed: boolean): void {
    try {
        window.localStorage.setItem(COLLAPSED_KEY, collapsed ? '1' : '0');
    } catch (err) {
        log.ui.warn('Could not remember the side rail state', err);
    }
}

function hostOf(url: string): string {
    try {
        return new URL(url).hostname.replace(/^www\./, '');
    } catch {
        return url;
    }
}

function pagesLabel(pages: number[] | undefined): string | null {
    if (!pages || pages.length === 0) return null;
    return `${pages.length === 1 ? 'p.' : 'pp.'} ${pages.join(', ')}`;
}

function costText(summary: TurnSummary): string {
    const { cost } = summary;
    if (cost.kind === 'local') return 'No cloud charges';
    if (cost.kind === 'estimate' && typeof cost.usd === 'number') {
        const usd = cost.usd < 0.01 ? cost.usd.toFixed(4) : cost.usd.toFixed(2);
        return `About $${usd} at list price`;
    }
    return summary.model ? 'Not known for this model' : 'Not known';
}

function errorText(err: unknown): string {
    if (err instanceof api.ApiError) return err.detail || err.message;
    return err instanceof Error ? err.message : String(err);
}

interface SideRailProps {
    messages: Message[];
    /** A turn is running, so the latest answer is not the one being written. */
    busy?: boolean;
}

export function SideRail({ messages, busy = false }: SideRailProps) {
    const state = railState(messages, busy);
    const turn = state?.kind === 'turn' ? state.turn : null;
    const summary = turn?.turnSummary ?? null;
    const folded = useFolded();
    const [collapsed, setCollapsed] = useState(readCollapsed);
    const [overlayOpen, setOverlayOpen] = useState(false);

    // A new turn closes the overlay so it never shows a stale turn on top.
    useEffect(() => { setOverlayOpen(false); }, [turn?.id, busy]);

    if (!state) return null;
    const noLiveData = summary ? liveDataFailed(summary) : false;

    if (folded ? !overlayOpen : collapsed) {
        const open = () => {
            if (folded) {
                setOverlayOpen(true);
            } else {
                setCollapsed(false);
                writeCollapsed(false);
            }
        };
        return (
            <button
                type="button"
                className={`side-rail-tab${noLiveData ? ' side-rail-tab-warn' : ''}`}
                onClick={open}
                aria-label="Show sources and turn details"
            >
                {noLiveData ? <AlertTriangle size={13} aria-hidden="true" /> : <PanelRightOpen size={13} aria-hidden="true" />}
                <span>{noLiveData ? "Couldn't get live data" : 'Sources & details'}</span>
            </button>
        );
    }

    const close = () => {
        if (folded) {
            setOverlayOpen(false);
        } else {
            setCollapsed(true);
            writeCollapsed(true);
        }
    };

    return (
        <aside
            className={`side-rail ${folded ? 'side-rail-overlay' : 'side-rail-docked'}`}
            aria-label="Sources and turn details"
        >
            <div className="side-rail-head">
                <span className="side-rail-title">This answer</span>
                <button type="button" className="btn-icon-sm" onClick={close} aria-label="Hide sources and turn details">
                    {folded ? <X size={14} /> : <PanelRightClose size={14} />}
                </button>
            </div>
            <div className="side-rail-body">
                {!summary && (
                    <p className="side-rail-pending" role="status">
                        Working on this answer. Its sources, files and where its data went appear here when it finishes.
                    </p>
                )}
                {noLiveData && (
                    <div className="side-rail-warn" role="status">
                        <AlertTriangle size={14} aria-hidden="true" />
                        <div>
                            <strong>Couldn't get live data</strong>
                            <span>Every web lookup this turn failed, so the answer isn't based on current information.</span>
                        </div>
                    </div>
                )}
                {summary && (summary.error ? (
                    <p className="side-rail-error" role="alert">
                        Couldn't put together this turn's sources and details: {summary.error}
                    </p>
                ) : (
                    <>
                        <SourcesSection summary={summary} />
                        <FilesSection summary={summary} />
                        <ThisTurnSection summary={summary} />
                    </>
                ))}
            </div>
        </aside>
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
    const pages = summary.pages ?? [];
    const documents = summary.documents ?? [];
    if (pages.length === 0 && documents.length === 0) return null;
    return (
        <Section title="Sources">
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
                                {hostOf(p.url)}{p.kind === 'search_result' ? ' · search result' : ''}
                            </span>
                        </div>
                    </li>
                ))}
                {documents.map((d) => {
                    const where = pagesLabel(d.pages);
                    const count = d.passages ?? 0;
                    return (
                        <li key={d.path} className="side-rail-item">
                            <FileText size={13} className="side-rail-item-icon" aria-hidden="true" />
                            <div className="side-rail-item-text">
                                <span className="side-rail-name" title={d.name}>{d.name}</span>
                                <span className="side-rail-meta">
                                    {[where, `${count} passage${count === 1 ? '' : 's'}`].filter(Boolean).join(' · ')}
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
    const files = summary.files ?? [];
    const [error, setError] = useState<string | null>(null);
    const { status, error: checkError } = useFileStatus(files.map((f) => f.path));
    if (files.length === 0) return null;

    const run = (action: Promise<unknown>, name: string) => {
        setError(null);
        action.catch((err) => {
            log.ui.error(`Failed to open ${name}`, err);
            setError(`Couldn't open ${name}: ${errorText(err)}`);
        });
    };

    return (
        <Section title="Files">
            <ul className="side-rail-list">
                {files.map((f) => {
                    const now = status.get(f.path);
                    if (now?.exists === false) {
                        return (
                            <li key={f.path} className="side-rail-item side-rail-item-gone">
                                <FileText size={13} className="side-rail-item-icon" aria-hidden="true" />
                                <div className="side-rail-item-text">
                                    <span className="side-rail-name" title={f.path}>{f.name}</span>
                                    <span className="side-rail-meta">No longer on disk</span>
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
                                {changed && <span className="side-rail-meta">Changed since this turn</span>}
                                <span className="side-rail-actions">
                                    {f.openable && (
                                        <button type="button" className="side-rail-action" onClick={() => run(api.openFileInApp(f.path), f.name)}>
                                            Open
                                        </button>
                                    )}
                                    <button type="button" className="side-rail-action" onClick={() => run(api.openFileOrFolder(f.path, true), f.name)}>
                                        <FolderOpen size={11} aria-hidden="true" />
                                        Show in folder
                                    </button>
                                </span>
                            </div>
                        </li>
                    );
                })}
            </ul>
            {error && <p className="side-rail-error" role="alert">{error}</p>}
            {checkError && (
                <p className="side-rail-error">Couldn't check whether these files changed: {checkError}</p>
            )}
        </Section>
    );
}

function ThisTurnSection({ summary }: { summary: TurnSummary }) {
    const { model } = summary;
    const sites = summary.sites ?? [];
    const usedDocs = (summary.documents ?? []).length > 0;
    return (
        <Section title="This turn">
            <dl className="side-rail-facts">
                <dt>Model</dt>
                <dd>
                    {model ? (
                        <span className={`side-rail-where${model.remote ? ' remote' : ''}`}>
                            {model.remote ? <Cloud size={12} aria-hidden="true" /> : <Monitor size={12} aria-hidden="true" />}
                            {model.remote ? `${model.provider_name} (cloud)` : 'This PC'}
                        </span>
                    ) : (
                        <span className="side-rail-where">Not reported</span>
                    )}
                    {model?.name && <span className="side-rail-meta">{model.name}</span>}
                </dd>
                {usedDocs && (
                    <>
                        <dt>Documents</dt>
                        <dd>
                            {model?.remote
                                ? `Searched on this PC. The passages used were sent to ${model.provider_name}.`
                                : model
                                    ? 'Read on this PC and stayed here.'
                                    : 'Searched on this PC.'}
                        </dd>
                    </>
                )}
                <dt>Websites</dt>
                <dd>{sites.length > 0 ? sites.join(', ') : "None from GAIA's web tools"}</dd>
                <dt>Cost</dt>
                <dd>{costText(summary)}</dd>
            </dl>
        </Section>
    );
}
