// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useState } from 'react';
import { useChatStore } from '../stores/chatStore';
import * as api from '../services/api';
import type { Document } from '../types';
import { waitsForServerReturn } from '../utils/documentFailure';
import { log } from '../utils/logger';

function waitsOnServer(d: Document): boolean {
    return d.indexing_status === 'failed' && waitsForServerReturn(d.error_kind);
}

/** First wait before trying a still-waiting document again while the server reads as up. */
export const WAITING_RECHECK_MS = 10_000;
/** The wait doubles after each pass that leaves a document waiting, up to this. */
export const WAITING_RECHECK_MAX_MS = 60_000;

/**
 * Re-indexes documents that failed only because the model server was down, so
 * "waiting for the model server" holds: each time the server comes back, and
 * on a timer while one still waits with the server up, since an outage shorter
 * than the status poll is never seen as down. No timer runs with none waiting.
 * A document the server was too busy for stays on its row's Retry.
 */
export function useRetryServerWaitingDocs(): void {
    const serverUp = useChatStore((s) => s.systemStatus?.lemonade_running === true);
    const anyWaiting = useChatStore((s) => s.documents.some(waitsOnServer));
    const setDocuments = useChatStore((s) => s.setDocuments);
    const running = useRef(false);
    const lastPassAt = useRef(0);
    // Passes in a row that left a document waiting; sets the timer's backoff.
    const stuckPasses = useRef(0);
    // Bumped when a run ends, so the timer below is set again.
    const [runs, setRuns] = useState(0);
    const startRef = useRef<() => void>(() => {});
    // A comeback seen mid-pass; docs that pass retried during the blip need another go.
    const cameBackAgain = useRef(false);

    startRef.current = () => {
        const pass = async (): Promise<void> => {
            lastPassAt.current = Date.now();
            const { documents } = await api.listDocuments();
            const ids = (documents || []).filter(waitsOnServer).map((d) => d.id);
            if (ids.length === 0) {
                stuckPasses.current = 0;
                // A stale waiting row in the store would keep the timer going for nothing.
                if (useChatStore.getState().documents.some(waitsOnServer)) setDocuments(documents || []);
                return;
            }
            log.doc.info(`Model server is back; retrying ${ids.length} document(s) that were waiting for it`);
            // One at a time: they all go through the same embedder.
            for (const id of ids) {
                // Re-read each time: a manual Retry or a delete may have got there first.
                const now = (await api.listDocuments()).documents || [];
                const doc = now.find((d) => d.id === id);
                if (!doc || !waitsOnServer(doc)) continue;
                setDocuments(now.map((d) => (d.id === id ? { ...d, indexing_status: 'indexing' } : d)));
                try {
                    await api.reindexDocument(id);
                    log.doc.info(`Indexed "${doc.filename}" now the model server is back`);
                } catch (err) {
                    log.doc.warn(`"${doc.filename}" still couldn't be indexed after the model server came back`, err);
                }
            }
            const fresh = (await api.listDocuments()).documents || [];
            stuckPasses.current = fresh.some(waitsOnServer) ? stuckPasses.current + 1 : 0;
            lastPassAt.current = Date.now();
            setDocuments(fresh);
        };
        running.current = true;
        void (async () => {
            try {
                do {
                    cameBackAgain.current = false;
                    try {
                        await pass();
                    } catch (err) {
                        // Counts as stuck, so the timer backs off instead of retrying at once.
                        stuckPasses.current += 1;
                        lastPassAt.current = Date.now();
                        log.doc.warn('Could not retry the documents waiting for the model server', err);
                    }
                } while (cameBackAgain.current && useChatStore.getState().systemStatus?.lemonade_running === true);
            } finally {
                running.current = false;
                setRuns((n) => n + 1);
            }
        })();
    };

    useEffect(() => {
        if (!serverUp) return;
        stuckPasses.current = 0;
        if (running.current) {
            cameBackAgain.current = true;
            return;
        }
        startRef.current();
    }, [serverUp]);

    useEffect(() => {
        if (!serverUp || !anyWaiting || running.current) return undefined;
        const gap = stuckPasses.current === 0
            ? 0
            : Math.min(WAITING_RECHECK_MS * 2 ** (stuckPasses.current - 1), WAITING_RECHECK_MAX_MS);
        const timer = setTimeout(() => {
            if (!running.current) startRef.current();
        }, Math.max(0, lastPassAt.current + gap - Date.now()));
        return () => clearTimeout(timer);
    }, [serverUp, anyWaiting, runs]);
}
