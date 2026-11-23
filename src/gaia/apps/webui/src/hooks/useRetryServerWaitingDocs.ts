// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef } from 'react';
import { useChatStore } from '../stores/chatStore';
import * as api from '../services/api';
import type { Document } from '../types';
import { waitsForServerReturn } from '../utils/documentFailure';
import { log } from '../utils/logger';

/** How long a document waits between retries while the server reads as up. */
export const RETRY_GAP_MS = 20_000;
/** Retries per document while the server reads as up; the next outage starts the count again. */
export const MAX_TRIES_WHILE_UP = 3;

function waitsOnServer(d: Document): boolean {
    return d.indexing_status === 'failed' && waitsForServerReturn(d.error_kind);
}

/**
 * Re-indexes documents that failed only because the model server was down:
 * all of them each time the server comes back, and one that fails while the
 * server still reads as up (an outage too short for the status poll to see).
 * A document the server was too busy for stays on its row's Retry.
 */
export function useRetryServerWaitingDocs(): void {
    // A new object on every status poll, so a waiting document is looked at again each poll.
    const status = useChatStore((s) => s.systemStatus);
    const serverUp = status?.lemonade_running === true;
    const waitingKey = useChatStore((s) => s.documents.filter(waitsOnServer).map((d) => d.id).sort().join('\n'));
    const setDocuments = useChatStore((s) => s.setDocuments);
    const running = useRef(false);
    // Work seen mid-pass (a comeback, or a newly waiting document) that needs another pass.
    const again = useRef(false);
    const wasUp = useRef(false);
    const tries = useRef(new Map<string, { count: number; at: number }>());

    useEffect(() => {
        if (!serverUp) {
            wasUp.current = false;
            tries.current.clear();
            return;
        }
        const cameBack = !wasUp.current;
        wasUp.current = true;
        const due = (id: string): boolean => {
            const t = tries.current.get(id);
            return !t || (t.count < MAX_TRIES_WHILE_UP && Date.now() - t.at >= RETRY_GAP_MS);
        };
        if (!cameBack && !waitingKey.split('\n').some((id) => id && due(id))) return;
        if (running.current) {
            again.current = true;
            return;
        }
        const pass = async (): Promise<void> => {
            const { documents } = await api.listDocuments();
            const ids = (documents || []).filter((d) => waitsOnServer(d) && due(d.id)).map((d) => d.id);
            if (ids.length === 0) return;
            log.doc.info(`Model server is up; retrying ${ids.length} document(s) that were waiting for it`);
            // One at a time: they all go through the same embedder.
            for (const id of ids) {
                // Re-read each time: a manual Retry or a delete may have got there first.
                const now = (await api.listDocuments()).documents || [];
                const doc = now.find((d) => d.id === id);
                if (!doc || !waitsOnServer(doc)) continue;
                const prior = tries.current.get(id);
                tries.current.set(id, { count: (prior?.count ?? 0) + 1, at: Date.now() });
                setDocuments(now.map((d) => (d.id === id ? { ...d, indexing_status: 'indexing' } : d)));
                try {
                    await api.reindexDocument(id);
                    log.doc.info(`Indexed "${doc.filename}" now the model server is up`);
                } catch (err) {
                    log.doc.warn(`"${doc.filename}" still couldn't be indexed with the model server up`, err);
                }
            }
            const fresh = await api.listDocuments();
            setDocuments(fresh.documents || []);
        };
        running.current = true;
        void (async () => {
            try {
                do {
                    again.current = false;
                    try {
                        await pass();
                    } catch (err) {
                        log.doc.warn('Could not retry the documents waiting for the model server', err);
                    }
                } while (again.current && useChatStore.getState().systemStatus?.lemonade_running === true);
            } finally {
                running.current = false;
            }
        })();
    }, [status, serverUp, waitingKey, setDocuments]);
}
