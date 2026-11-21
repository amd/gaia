// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef } from 'react';
import { useChatStore } from '../stores/chatStore';
import * as api from '../services/api';
import type { Document } from '../types';
import { waitsForServerReturn } from '../utils/documentFailure';
import { log } from '../utils/logger';

function waitsOnServer(d: Document): boolean {
    return d.indexing_status === 'failed' && waitsForServerReturn(d.error_kind);
}

/**
 * Re-indexes documents that failed only because the model server was down,
 * once each time the server comes back, so "waiting for the model server" holds.
 * A document the server was too busy for stays on its row's Retry.
 */
export function useRetryServerWaitingDocs(): void {
    const serverUp = useChatStore((s) => s.systemStatus?.lemonade_running === true);
    const setDocuments = useChatStore((s) => s.setDocuments);
    const running = useRef(false);
    // A comeback seen mid-pass; docs that pass retried during the blip need another go.
    const cameBackAgain = useRef(false);

    useEffect(() => {
        if (!serverUp) return;
        if (running.current) {
            cameBackAgain.current = true;
            return;
        }
        const pass = async (): Promise<void> => {
            const { documents } = await api.listDocuments();
            const ids = (documents || []).filter(waitsOnServer).map((d) => d.id);
            if (ids.length === 0) return;
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
            const fresh = await api.listDocuments();
            setDocuments(fresh.documents || []);
        };
        running.current = true;
        void (async () => {
            try {
                do {
                    cameBackAgain.current = false;
                    try {
                        await pass();
                    } catch (err) {
                        log.doc.warn('Could not retry the documents waiting for the model server', err);
                    }
                } while (cameBackAgain.current && useChatStore.getState().systemStatus?.lemonade_running === true);
            } finally {
                running.current = false;
            }
        })();
    }, [serverUp, setDocuments]);
}
