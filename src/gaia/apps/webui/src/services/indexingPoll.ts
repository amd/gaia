// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The one poll of GET /api/documents/indexing, so indexing stays visible with
 * the Document Library closed. Every consumer reads the result from chatStore.
 *
 * Often while something is indexing; slowly otherwise, because an index can
 * start without this window (a changed file on disk, another client).
 */

import * as api from './api';
import { useChatStore } from '../stores/chatStore';
import { log } from '../utils/logger';

export const BUSY_INTERVAL_MS = 1_500;
export const IDLE_INTERVAL_MS = 10_000;

const isIndexing = (status: string | undefined) => status === 'indexing' || status === 'pending';

let timer: ReturnType<typeof setTimeout> | null = null;
let generation = 0;
let lastFingerprint = '';

function fingerprint(o: api.IndexingOverview): string {
    return [o.batch_id, o.total, o.done, o.failed, o.cancelled].join('|');
}

/** Read the overview once; when documents finished, the library's rows are stale too. */
export async function refreshIndexing(): Promise<void> {
    try {
        const overview = await api.getIndexingOverview();
        if (!overview) return;
        useChatStore.getState().setIndexing(overview);
        const next = fingerprint(overview);
        if (next === lastFingerprint) return;
        const first = lastFingerprint === '';
        lastFingerprint = next;
        if (first) return;
        const data = await api.listDocuments();
        useChatStore.getState().setDocuments(data.documents || []);
    } catch (err) {
        log.doc.warn('Could not read indexing progress', err);
    }
}

function delay(): number {
    const { indexing, documents } = useChatStore.getState();
    const busy = indexing?.active || documents.some((d) => isIndexing(d.indexing_status));
    return busy ? BUSY_INTERVAL_MS : IDLE_INTERVAL_MS;
}

function schedule(gen: number): void {
    void refreshIndexing().then(() => {
        if (gen !== generation) return;
        timer = setTimeout(() => schedule(gen), delay());
    });
}

function wake(): void {
    if (timer) clearTimeout(timer);
    timer = null;
    schedule(++generation);
}

/** Start the app's single poll; returns its stop. Starting again replaces the old one. */
export function startIndexingPoll(): () => void {
    lastFingerprint = '';
    const unsubscribe = useChatStore.subscribe((s, prev) => {
        if (s.documents === prev.documents || s.indexing?.active) return;
        if (s.documents.some((d) => isIndexing(d.indexing_status))) wake();
    });
    wake();
    return () => {
        unsubscribe();
        generation += 1;
        if (timer) clearTimeout(timer);
        timer = null;
    };
}
