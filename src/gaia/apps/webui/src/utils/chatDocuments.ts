// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Documents joining a chat: ones the agent indexed during a turn (the `done`
 * event's `documents_added`) and files dropped onto the chat view. Either way
 * the document lands in the library and is attached to the chat.
 */

import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import type { Document } from '../types';
import { log } from './logger';

/** Attach *ids* to the chat's entry in the session list, without repeats. */
function attachInList(sessionId: string, ids: string[]): void {
    const store = useChatStore.getState();
    const existing = store.sessions.find((s) => s.id === sessionId)?.document_ids ?? [];
    const fresh = ids.filter((id) => !existing.includes(id));
    if (fresh.length > 0) store.updateSessionInList(sessionId, { document_ids: [...existing, ...fresh] });
}

/** Show library documents the backend already attached to the chat. */
export function showAddedDocuments(sessionId: string, added: Document[]): void {
    if (added.length === 0) return;
    const store = useChatStore.getState();
    const addedIds = new Set(added.map((d) => d.id));
    store.setDocuments([...added, ...store.documents.filter((d) => !addedIds.has(d.id))]);
    attachInList(sessionId, added.map((d) => d.id));
}

/**
 * Upload dropped files, attach each to the chat, then reload the library list
 * (the document bar lists only documents the store knows about).
 *
 * Uploads the blob rather than relying on File.path, which browsers leave
 * undefined and Electron 32+ removed. The Document Library is not opened, so a
 * drop doesn't interrupt the chat; it still shows a large file's progress (#728).
 */
export async function addDroppedFiles(sessionId: string | null, files: File[]): Promise<void> {
    for (const file of files) {
        log.doc.info(`Dropped on chat view: ${file.name}`);
        try {
            const doc = await api.uploadDocumentBlob(file);
            if (sessionId && doc?.id) {
                try {
                    await api.attachDocument(sessionId, doc.id);
                    attachInList(sessionId, [doc.id]);
                    log.doc.info(`Auto-attached "${file.name}" to session ${sessionId}`);
                } catch (attachErr) {
                    log.doc.warn(`Could not attach dropped document to session: ${attachErr}`);
                }
            }
        } catch (err) {
            log.doc.error(`Upload failed: ${file.name}`, err);
        }
    }
    try {
        const data = await api.listDocuments();
        useChatStore.getState().setDocuments(data.documents || []);
    } catch (err) {
        log.doc.error('Could not refresh the document list after a drop', err);
    }
}
