// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useId, useMemo, useState, type ReactNode } from 'react';
import { AlertTriangle, HardDrive, HelpCircle, Loader2 } from 'lucide-react';
import { useT, type Translate } from '../../i18n/useT';
import type { Say } from '../../i18n/registry';
import type { SetupCatalogEntry, SetupModelProgress, SetupPlan } from '../../types';
import { formatBytes, formatMemoryGiB, formatSize } from './format';
import { useDebounced, useTweened } from './motion';
import { Reveal } from './Reveal';
import {
    catalogOf, chatCandidates, installedOnlyRows, isEmbedding, isNpu, isVision, mainRows, moreRows,
    shortfall, totals, type Selection,
} from './selection';

const SEARCH_ABOVE = 6;

/** "Chat", "Vision", "Documents": what a model is for, in fixed words. */
function purposeChips(e: SetupCatalogEntry, t: Translate): string[] {
    const chips: string[] = [];
    if (e.chat_choice && !isEmbedding(e)) chips.push(t('setup.models.purpose.chat'));
    if (isVision(e)) chips.push(t('setup.models.purpose.vision'));
    if (isEmbedding(e)) chips.push(t('setup.models.purpose.documents'));
    return chips;
}

/** One line on what the model gives the user. */
function purposeLine(e: SetupCatalogEntry, t: Say): string {
    if (isEmbedding(e)) return t('setup.models.line.documents');
    if (isVision(e)) return t('setup.models.line.vision');
    return t('setup.models.line.chat');
}

/** "On this PC", "Download 5.1 GB", "Partly downloaded, 1.2 GB left". */
export function statusText(e: SetupCatalogEntry, t: Translate): string {
    if (e.downloaded) return t('setup.models.status.onPc');
    if (e.size_gb == null) return t('setup.models.status.sizeUnknown');
    if (e.partial) return t('setup.models.status.partial', { size: formatSize(e.size_gb) });
    return t('setup.models.status.download', { size: formatSize(e.size_gb) });
}

/** The memory note, with the backend's numbers and nothing invented. */
function fitLine(e: SetupCatalogEntry, t: Say): string | null {
    if (e.fits !== true || e.need_gb == null || e.capacity_gb == null) return null;
    return t('setup.models.fit.ok', { need: formatSize(e.need_gb), have: formatMemoryGiB(e.capacity_gb) });
}

export function tooBigText(e: SetupCatalogEntry, t: Translate): string {
    return e.need_gb != null && e.capacity_gb != null
        ? t('setup.models.warn.tooBig', { need: formatSize(e.need_gb), have: formatMemoryGiB(e.capacity_gb) })
        : t('setup.models.warn.tooBigNoNumbers', { reason: e.fit_reason ?? '' });
}

/** The caveat under an unticked model that turns a feature off. */
function skipCaveat(e: SetupCatalogEntry, t: Translate): string | null {
    if (isVision(e)) return t('setup.models.warn.vision', { name: e.name });
    if (isEmbedding(e)) return t('setup.models.warn.documents', { name: e.name });
    return null;
}

interface RowProps {
    entry: SetupCatalogEntry;
    ticked: boolean;
    onToggle: (on: boolean) => void;
    disabled?: boolean;
    /** Live download state, in Settings. */
    progress?: SetupModelProgress;
    /** Extra controls at the row's end, e.g. Remove in Settings. */
    actions?: ReactNode;
}

function ModelRow({ entry: e, ticked, onToggle, disabled, progress, actions }: RowProps) {
    const t = useT();
    const id = useId();
    const big = e.fits === false;
    const caveat = !ticked ? skipCaveat(e, t) : null;
    const downloading = progress?.state === 'downloading' && progress.bytes_total > 0;
    const warning = big ? tooBigText(e, t) : caveat;
    const describedBy = [`${id}-status`, `${id}-note`, warning ? `${id}-warn` : null].filter(Boolean).join(' ');
    return (
        <li className={`mc-row${ticked ? ' is-ticked' : ''}${warning ? ' has-warning' : ''}`}>
            <div className="mc-row-top">
                <label className="mc-row-main">
                    <input
                        type="checkbox"
                        className="mc-input"
                        checked={ticked}
                        disabled={disabled}
                        onChange={(ev) => onToggle(ev.target.checked)}
                        aria-describedby={describedBy}
                    />
                    <span className="mc-box" aria-hidden="true">
                        <svg viewBox="0 0 16 16" width="14" height="14"><path className="mc-tick" d="M3.5 8.5l3 3 6-7" /></svg>
                    </span>
                    <span className="mc-text">
                        <span className="mc-name-line">
                            <bdi className="mc-name" title={e.id}>{e.name}</bdi>
                            <span className="mc-chips">
                                {purposeChips(e, t).map((c) => <span key={c} className="mc-chip">{c}</span>)}
                                {isNpu(e) && <span className="mc-chip is-npu">{t('setup.models.npu')}</span>}
                            </span>
                        </span>
                        <span className="mc-status" id={`${id}-status`}>
                            {downloading ? t('setup.models.status.downloading', { done: formatBytes(progress.bytes_done), total: formatBytes(progress.bytes_total) }) : statusText(e, t)}
                            {e.recommended && <span className="mc-pill">{t('setup.models.recommended')}</span>}
                            {e.fits === null && (
                                <span className="mc-chip is-unknown"><HelpCircle size={11} aria-hidden="true" /> {t('setup.models.fitUnknown')}</span>
                            )}
                        </span>
                        <span className="mc-note" id={`${id}-note`}>
                            {t.compose((say) => [purposeLine(e, say), fitLine(e, say), e.fits === null ? say('setup.models.fit.unknown') : null].filter(Boolean).join(' '))}
                        </span>
                    </span>
                </label>
                {actions && <span className="mc-actions">{actions}</span>}
            </div>
            {downloading && (
                <progress className="mc-mini-progress" max={progress.bytes_total} value={progress.bytes_done} aria-label={t('setup.progress.rowLabel', { name: e.name, state: t('setup.state.downloading') })} />
            )}
            <Reveal open={!!warning}>
                <p className={`mc-warn${big ? ' is-big' : ''}`} role="note" id={`${id}-warn`}>
                    <AlertTriangle size={13} aria-hidden="true" />
                    <span>{warning}</span>
                </p>
            </Reveal>
        </li>
    );
}

interface SummaryProps {
    plan: SetupPlan;
    selection: Selection;
}

/** Download total against the free space on the model drive, live as the user ticks. */
export function SelectionSummary({ plan, selection }: SummaryProps) {
    const t = useT();
    const { downloadGb, freeGb, afterGb } = totals(plan, selection);
    const short = shortfall(plan, selection);
    const shownDownload = useTweened(downloadGb ?? 0);
    const shownAfter = useTweened(afterGb ?? 0);
    const drive = driveName(plan.disk?.path ?? null);
    const fill = downloadGb != null && freeGb ? Math.min(1, downloadGb / freeGb) : 0;
    const spoken = downloadGb == null
        ? t('setup.summary.spokenUnknown')
        : afterGb != null
            ? t('setup.summary.spoken', { download: formatSize(downloadGb), after: formatSize(Math.max(0, afterGb)) })
            : t('setup.summary.spokenNoDisk', { download: formatSize(downloadGb) });
    const announced = useDebounced(spoken, 600);
    return (
        <div className={`mc-summary${short ? ' is-warn' : ''}`} role="group" aria-label={t('setup.summary.label')}>
            <div className="mc-summary-stats">
                <span className="mc-stat">
                    <span className="mc-stat-label">{t('setup.summary.download')}</span>
                    <bdi className="mc-stat-value">{downloadGb == null ? t('setup.summary.sizeUnknown') : formatSize(round1(shownDownload))}</bdi>
                </span>
                <span className="mc-stat">
                    <span className="mc-stat-label"><HardDrive size={12} aria-hidden="true" /> {drive ? t('setup.summary.freeOn', { drive }) : t('setup.summary.free')}</span>
                    <bdi className="mc-stat-value">{freeGb == null ? t('setup.summary.freeUnknown') : formatSize(freeGb)}</bdi>
                </span>
                {afterGb != null && !short && (
                    <span className="mc-stat">
                        <span className="mc-stat-label">{t('setup.summary.after')}</span>
                        <bdi className="mc-stat-value">{formatSize(Math.max(0, round1(shownAfter)))}</bdi>
                    </span>
                )}
            </div>
            {freeGb != null && (
                <div className="mc-disk" aria-hidden="true">
                    <span className="mc-disk-fill" style={{ inlineSize: `${(fill * 100).toFixed(1)}%` }} />
                </div>
            )}
            <Reveal open={!!short}>
                {short && (
                    <p className="mc-disk-warn" role="note">
                        <AlertTriangle size={13} aria-hidden="true" />
                        <span>
                            {short.drop
                                ? t('setup.summary.shortDrop', { need: formatSize(short.needGb), free: formatSize(short.freeGb), drive: drive ?? t('setup.summary.theDrive'), freeUp: formatSize(short.freeUpGb), name: short.drop.name, saves: formatSize(short.drop.size_gb ?? 0) })
                                : t('setup.summary.short', { need: formatSize(short.needGb), free: formatSize(short.freeGb), drive: drive ?? t('setup.summary.theDrive'), freeUp: formatSize(short.freeUpGb) })}
                        </span>
                    </p>
                )}
            </Reveal>
            <span className="visually-hidden" aria-live="polite">{announced}</span>
        </div>
    );
}

/** "C:" from "C:\Users\me\.cache\huggingface\hub"; the folder itself elsewhere. */
export function driveName(path: string | null): string | null {
    if (!path) return null;
    const win = /^([A-Za-z]:)[\\/]/.exec(path);
    return win ? win[1] : path;
}

function round1(n: number): number {
    return Math.round(n * 10) / 10;
}

export interface ModelChecklistProps {
    plan: SetupPlan;
    selection: Selection;
    onToggle: (id: string, on: boolean) => void;
    onDefaultChat: (id: string) => void;
    /** Labels the list for assistive tech: the step or section heading. */
    labelledBy: string;
    /** In setup, a chat model is required; with a cloud provider only the embedder is listed. */
    cloud?: boolean;
    disabled?: boolean;
    progress?: Record<string, SetupModelProgress>;
    /** Per-row extra controls (Settings' Remove). */
    rowActions?: (entry: SetupCatalogEntry) => ReactNode;
}

/**
 * The model checklist: GAIA's models with size, purpose, fit and status; the
 * chat models already in Lemonade; the default chat model; and the totals.
 * Setup's "Choose your models" and Settings > Models on this PC both use it.
 */
export function ModelChecklist({ plan, selection, onToggle, onDefaultChat, labelledBy, cloud, disabled, progress, rowActions }: ModelChecklistProps) {
    const t = useT();
    const catalog = catalogOf(plan);
    const [query, setQuery] = useState('');
    const selectId = useId();
    const main = cloud ? catalog.filter(isEmbedding) : mainRows(catalog);
    const more = cloud ? [] : moreRows(catalog);
    const installed = cloud ? [] : installedOnlyRows(catalog);
    const shownInstalled = useMemo(() => {
        const q = query.trim().toLowerCase();
        return q ? installed.filter((e) => e.id.toLowerCase().includes(q) || e.name.toLowerCase().includes(q)) : installed;
    }, [installed, query]);
    const candidates = chatCandidates(catalog, selection.ticked);

    const row = (e: SetupCatalogEntry) => (
        <ModelRow
            key={e.id}
            entry={e}
            ticked={selection.ticked.has(e.id)}
            onToggle={(on) => onToggle(e.id, on)}
            disabled={disabled}
            progress={progress?.[e.id]}
            actions={rowActions?.(e)}
        />
    );

    return (
        <div className="mc">
            <ul className="mc-list" role="group" aria-labelledby={labelledBy}>
                {main.map(row)}
            </ul>
            {cloud ? <p className="setup-hint">{t('setup.models.cloudNote')}</p> : <p className="mc-foot">{t('setup.models.neverDeletes')}</p>}

            {more.length > 0 && (
                <details className="mc-more">
                    <summary>{t('setup.models.more', { n: String(more.length) })}</summary>
                    <ul className="mc-list" role="group" aria-label={t('setup.models.moreLabel')}>{more.map(row)}</ul>
                </details>
            )}

            {installed.length > 0 && (
                <details className="mc-more">
                    <summary>{t('setup.models.installed', { n: String(installed.length) })}</summary>
                    {installed.length > SEARCH_ABOVE && (
                        <input
                            className="settings-input mc-search"
                            type="search"
                            value={query}
                            onChange={(ev) => setQuery(ev.target.value)}
                            placeholder={t('setup.models.search')}
                            aria-label={t('setup.models.search')}
                        />
                    )}
                    <ul className="mc-list" role="radiogroup" aria-label={t('setup.models.installedLabel')}>
                        {shownInstalled.map((e) => (
                            <li key={e.id} className={`mc-row is-installed${selection.defaultChat === e.id ? ' is-ticked' : ''}`}>
                                <label className="mc-row-main">
                                    <input
                                        type="radio"
                                        name={`${selectId}-installed`}
                                        className="mc-radio"
                                        checked={selection.defaultChat === e.id}
                                        disabled={disabled}
                                        onChange={() => onDefaultChat(e.id)}
                                    />
                                    <span className="mc-text">
                                        <span className="mc-name-line">
                                            <bdi className="mc-name" title={e.id}>{e.name}</bdi>
                                            <span className="mc-chips">
                                                <span className="mc-chip">{t('setup.models.purpose.chat')}</span>
                                                {isNpu(e) && <span className="mc-chip is-npu">{t('setup.models.npu')}</span>}
                                            </span>
                                        </span>
                                        <span className="mc-status">{t('setup.models.status.onPc')} · {t('setup.models.useAsDefault')}</span>
                                    </span>
                                </label>
                            </li>
                        ))}
                    </ul>
                </details>
            )}

            {!cloud && (
                <div className="mc-default">
                    <label htmlFor={selectId} className="mc-default-label">{t('setup.models.defaultChat')}</label>
                    <select
                        id={selectId}
                        className="settings-input mc-select"
                        value={selection.defaultChat ?? ''}
                        disabled={disabled || candidates.length === 0}
                        onChange={(ev) => onDefaultChat(ev.target.value)}
                    >
                        {candidates.length === 0 && <option value="">{t('setup.models.noChat')}</option>}
                        {candidates.map((e) => (
                            <option key={e.id} value={e.id}>
                                {e.name}{isNpu(e) ? ` (${t('setup.models.npu')})` : ''}
                            </option>
                        ))}
                    </select>
                </div>
            )}
        </div>
    );
}

/** Skeleton rows while the model list loads; text says what is happening. */
export function ChecklistLoading() {
    const t = useT();
    return (
        <div className="mc" aria-busy="true">
            <p className="setup-hint" role="status"><Loader2 size={13} className="spin" aria-hidden="true" /> {t('setup.models.loading')}</p>
            <ul className="mc-list" aria-hidden="true">
                {[0, 1, 2].map((i) => <li key={i} className="mc-row is-skeleton"><span className="mc-skeleton" /></li>)}
            </ul>
        </div>
    );
}
