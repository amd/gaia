// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect } from 'react';
import { AlertCircle, CheckCircle2, Loader, X } from 'lucide-react';
import * as api from '../services/api';
import { refreshIndexing } from '../services/indexingPoll';
import { useChatStore } from '../stores/chatStore';
import { pluralKey, type PluralKey } from '../i18n/registry';
import { useT, type Translate } from '../i18n/useT';
import type { Document } from '../types';
import { log } from '../utils/logger';
import { fmt, joinNames, timeLeftLabel } from '../utils/indexingLabels';
import { ProgressBar, progressLabel } from './IndexingProgress';
import './IndexingOverview.css';

/** How long a batch with nothing wrong stays on screen after it ends. */
export const FINISHED_NOTICE_MS = 6_000;

const isIndexing = (d: Document) => d.indexing_status === 'indexing' || d.indexing_status === 'pending';
const notIndexed = (d: Document) => d.indexing_status === 'failed' || d.indexing_status === 'cancelled';

/** "5 indexed · 2 couldn't be indexed · 1 cancelled" for a batch that has ended. */
function outcomeLine(o: api.IndexingOverview, t: Translate): string {
    return t.compose((say) => {
        const count = (base: PluralKey, n: number) => say(pluralKey(base, n), { n });
        const parts: string[] = [];
        if (o.done || !(o.failed || o.cancelled)) parts.push(count('docs.overall.done', o.done ?? 0));
        if (o.failed) parts.push(count('docs.overall.failed', o.failed));
        if (o.cancelled) parts.push(count('docs.overall.cancelled', o.cancelled));
        return parts.join(' · ');
    });
}

const countLine = (o: api.IndexingOverview, t: Translate) =>
    t('docs.overall.count', { done: fmt(o.done ?? 0), total: fmt(o.total ?? 0) });

function useDismiss(batchId: string | null | undefined) {
    return useCallback(async () => {
        if (!batchId) return;
        try {
            await api.dismissIndexingBatch(batchId);
        } catch (err) {
            log.doc.warn('Could not clear the finished indexing batch', err);
        }
        await refreshIndexing();
    }, [batchId]);
}

const hasProblem = (o: api.IndexingOverview) => (o.failed ?? 0) + (o.cancelled ?? 0) > 0;

function imagesLine(images: NonNullable<api.IndexingOverview['images']>, t: Translate): string {
    const count = images.documents ?? 0;
    return t.plural('docs.images.overall', count, { name: images.names?.[0] ?? '' });
}

/** Documents you can already ask about, with their images still being read. */
function ImagesReadingLine({ onOpen }: { onOpen?: () => void }) {
    const t = useT();
    const images = useChatStore((s) => s.indexing?.images);
    if (!images?.documents) return null;
    const line = imagesLine(images, t);
    const eta = images.blocked ? null : timeLeftLabel(images.seconds_left, images.seconds_left ? 'ready' : 'estimating', t);
    const body = (
        <>
            {images.blocked
                ? <AlertCircle size={14} className="index-indicator-icon is-problem" aria-hidden="true" />
                : <Loader size={14} className="index-indicator-icon doc-spin" aria-hidden="true" />}
            <span className="index-indicator-text">
                <span>{line}</span>
                {eta && <span className="index-indicator-eta">{eta}</span>}
            </span>
        </>
    );
    return (
        <div className={`${onOpen ? 'index-indicator' : 'index-summary'} index-images`}>
            {onOpen
                ? <button type="button" className="index-indicator-open" onClick={onOpen} title={t('docs.overall.open')}>{body}</button>
                : <p className="index-images-line">{body}</p>}
            {images.blocked && <p className="index-images-blocked">{t('docs.images.blocked')}</p>}
        </div>
    );
}

/**
 * Stays reachable while the library is closed: one line above the chat that opens
 * the library, and takes nothing away from the chat. A batch that went wrong
 * stays until dismissed; one that went right clears itself.
 */
export function IndexingIndicator() {
    const t = useT();
    const overview = useChatStore((s) => s.indexing);
    const libraryOpen = useChatStore((s) => s.showDocLibrary);
    const setShowDocLibrary = useChatStore((s) => s.setShowDocLibrary);
    const dismiss = useDismiss(overview?.batch_id);
    const finishedWell = !!overview?.batch_id && !overview.active && !hasProblem(overview);

    useEffect(() => {
        if (!finishedWell) return;
        const timer = setTimeout(() => void dismiss(), FINISHED_NOTICE_MS);
        return () => clearTimeout(timer);
    }, [finishedWell, dismiss]);

    if (libraryOpen) return null;
    if (!overview?.batch_id || !overview.total) return <ImagesReadingLine onOpen={() => setShowDocLibrary(true)} />;

    const active = overview.active === true;
    const summary = active ? countLine(overview, t) : outcomeLine(overview, t);
    const eta = active ? timeLeftLabel(overview.seconds_left, overview.estimate, t) : null;
    const Icon = active ? Loader : hasProblem(overview) ? AlertCircle : CheckCircle2;
    const iconClass = active ? 'doc-spin' : hasProblem(overview) ? 'is-problem' : 'is-done';
    return (
        <div className="index-indicator">
            <button
                type="button"
                className="index-indicator-open"
                onClick={() => setShowDocLibrary(true)}
                title={t('docs.overall.open')}
                aria-label={`${summary}${eta ? ` · ${eta}` : ''} · ${t('docs.overall.open')}`}
            >
                <Icon size={14} className={`index-indicator-icon ${iconClass}`} aria-hidden="true" />
                <span className="index-indicator-text">
                    <span>{summary}</span>
                    {eta && <span className="index-indicator-eta">{eta}</span>}
                </span>
            </button>
            {!active && (
                <button type="button" className="app-notice-close" onClick={() => void dismiss()} aria-label={t('docs.overall.dismiss')}>
                    <X size={14} aria-hidden="true" />
                </button>
            )}
            {active && (
                <div className="index-indicator-bar">
                    <ProgressBar percent={overview.percent ?? null} label={summary} />
                </div>
            )}
        </div>
    );
}

/** The whole batch at the top of the library: counts, one bar, the file in hand, the time left. */
export function IndexingSummary() {
    const t = useT();
    const overview = useChatStore((s) => s.indexing);
    const dismiss = useDismiss(overview?.batch_id);
    if (!overview?.batch_id || !overview.total) return <ImagesReadingLine />;

    const active = overview.active === true;
    const running = overview.documents?.find((d) => d.id === overview.current_id);
    const failed = overview.documents?.filter((d) => d.state === 'failed') ?? [];
    return (
        <>
        <section className="index-summary" aria-label={t('docs.overall.title')}>
            <div className="index-summary-head">
                <strong>{active ? countLine(overview, t) : outcomeLine(overview, t)}</strong>
                {active && <span className="index-summary-eta">{timeLeftLabel(overview.seconds_left, overview.estimate, t)}</span>}
                {!active && (
                    <button type="button" className="app-notice-close" onClick={() => void dismiss()} aria-label={t('docs.overall.dismiss')}>
                        <X size={14} aria-hidden="true" />
                    </button>
                )}
            </div>
            {active && (
                <div className="index-progress is-running">
                    <ProgressBar percent={overview.percent ?? null} label={countLine(overview, t)} />
                </div>
            )}
            {active && overview.current && (
                <p className="index-summary-current">
                    <bdi>{overview.current}</bdi>
                    {running?.progress && <span> · {progressLabel(running.progress, t)}</span>}
                </p>
            )}
            {active && hasProblem(overview) && <p className="index-summary-problem">{outcomeLine({ ...overview, done: 0 }, t)}</p>}
            {failed.length > 0 && !active && (
                <p className="index-summary-problem"><bdi>{joinNames(failed.map((d) => d.filename), t)}</bdi></p>
            )}
        </section>
        <ImagesReadingLine />
        </>
    );
}

/**
 * Says why a chat can't search some of its documents, before and during a turn:
 * still indexing (the line above has the time left), or not indexed, with a way to the library.
 */
export function ChatIndexingNotice({ documents }: { documents: Document[] }) {
    const t = useT();
    const setShowDocLibrary = useChatStore((s) => s.setShowDocLibrary);
    const indexing = documents.filter(isIndexing);
    const missing = documents.filter(notIndexed);
    const partial = documents.filter((d) => d.images_reading);
    if (!indexing.length && !missing.length && !partial.length) return null;
    return (
        <div className="index-chat-notice" role="status">
            {partial.length > 0 && (
                <p>
                    <Loader size={13} className="doc-spin index-chat-notice-icon" aria-hidden="true" />
                    <span>{t.plural('chat.partial.notice', partial.length, { name: partial[0].filename })}</span>
                </p>
            )}
            {indexing.length > 0 && (
                <p>
                    <Loader size={13} className="doc-spin index-chat-notice-icon" aria-hidden="true" />
                    <span>{t.plural('chat.indexing.notice', indexing.length, { name: indexing[0].filename })}</span>
                </p>
            )}
            {missing.length > 0 && (
                <p>
                    <AlertCircle size={13} className="index-chat-notice-icon is-problem" aria-hidden="true" />
                    <span>
                        {t.plural('chat.notIndexed.notice', missing.length, { name: missing[0].filename })}{' '}
                        <button type="button" className="link-btn" onClick={() => setShowDocLibrary(true)}>
                            {t('docs.overall.open')}
                        </button>
                    </span>
                </p>
            )}
        </div>
    );
}
