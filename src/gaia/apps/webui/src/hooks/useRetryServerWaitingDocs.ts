// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef } from 'react';
import { useChatStore } from '../stores/chatStore';
import * as api from '../services/api';
import { isServerFailure } from '../utils/documentFailure';
import { log } from '../utils/logger';

/**
 * Re-indexes documents that failed only because the model server was down,
 * once each time the server comes back, so "waiting for the model server" holds.
 */
export function useRetryServerWaitingDocs(): void {
    const serverUp = useChatStore((s) => s.systemStatus?.lemonade_running === true);
    const setDocuments = useChatStore((s) => s.setDocuments);
    const running = useRef(false);

    useEffect(() => {
        if (!serverUp || running.current) return;
        running.current = true;
        void (async () => {
            try {
                const { documents } = await api.listDocuments();
                const waiting = (documents || []).filter(
                    (d) => d.indexing_status === 'failed' && isServerFailure(d.error_kind),
                );
                if (waiting.length === 0) return;
                log.doc.info(`Model server is back; retrying ${waiting.length} document(s) that were waiting for it`);
                // One at a time: they all go through the same embedder.
                for (const doc of waiting) {
                    try {
                        await api.reindexDocument(doc.id);
                        log.doc.info(`Indexed "${doc.filename}" now the model server is back`);
                    } catch (err) {
                        log.doc.warn(`"${doc.filename}" still couldn't be indexed after the model server came back`, err);
                    }
                }
                const fresh = await api.listDocuments();
                setDocuments(fresh.documents || []);
            } catch (err) {
                log.doc.warn('Could not retry the documents waiting for the model server', err);
            } finally {
                running.current = false;
            }
        })();
    }, [serverUp, setDocuments]);
}
