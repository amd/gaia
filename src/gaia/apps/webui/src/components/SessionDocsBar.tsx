// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useState } from 'react';
import { CheckCircle2, FileText, X } from 'lucide-react';
import { useChatStore } from '../stores/chatStore';
import * as api from '../services/api';
import { log } from '../utils/logger';

/** The documents indexed for this chat, shown under the header; renders nothing when there are none. */
export function SessionDocsBar({ sessionId, onDetachFailed }: {
    sessionId: string;
    /** Called with the document's name when removing it from the chat fails. */
    onDetachFailed: (name: string, err: unknown) => void;
}) {
    const session = useChatStore((s) => s.sessions.find((x) => x.id === sessionId));
    const documents = useChatStore((s) => s.documents);
    const setShowDocLibrary = useChatStore((s) => s.setShowDocLibrary);
    const updateSessionInList = useChatStore((s) => s.updateSessionInList);
    const [docsExpanded, setDocsExpanded] = useState(false);

    const sessionDocIds = new Set(session?.document_ids ?? []);
    const sessionDocs = documents.filter(d => sessionDocIds.has(d.id));

    /** Detach a document from this session via the context bar (does not delete from library). */
    const handleRemoveDocument = useCallback(async (e: React.MouseEvent, docId: string) => {
        e.stopPropagation(); // Don't trigger the context bar click
        const doc = documents.find((d) => d.id === docId);
        log.doc.info(`Detaching document from session: ${doc?.filename || docId}`);
        try {
            await api.detachDocument(sessionId, docId);
            // Read fresh state after await to avoid stale-closure bugs when
            // the user removes multiple documents in quick succession.
            const freshSession = useChatStore.getState().sessions.find(s => s.id === sessionId);
            const remaining = (freshSession?.document_ids ?? []).filter(id => id !== docId);
            updateSessionInList(sessionId, { document_ids: remaining });
            // Auto-collapse when 3 or fewer session docs remain after removal
            if (remaining.length <= 3) setDocsExpanded(false);
            log.doc.info(`Detached document from session: ${doc?.filename || docId}`);
        } catch (err) {
            log.doc.error(`Failed to detach document: ${doc?.filename || docId}`, err);
            onDetachFailed(doc?.filename || docId, err);
        }
    }, [documents, sessionId, updateSessionInList, onDetachFailed]);

    if (sessionDocs.length === 0) return null;

    const sorted = [...sessionDocs].sort((a, b) => {
        const aTime = a.last_accessed_at || a.indexed_at || '';
        const bTime = b.last_accessed_at || b.indexed_at || '';
        return bTime.localeCompare(aTime);
    });
    const visibleDocs = docsExpanded ? sorted : sorted.slice(0, 3);
    const hiddenCount = sorted.length - 3;
    return (
        <div
            className={`doc-context-bar${docsExpanded ? ' doc-context-expanded' : ''}`}
            aria-label={`${sessionDocs.length} indexed document${sessionDocs.length !== 1 ? 's' : ''}`}
        >
            <CheckCircle2 size={12} className="doc-context-icon" aria-hidden="true" />
            <button type="button" className="doc-context-label" onClick={() => setShowDocLibrary(true)} title="Manage documents">
                {sessionDocs.length} indexed
            </button>
            <div className={`doc-context-pills${docsExpanded ? ' doc-context-pills-expanded' : ''}`}>
                {visibleDocs.map((d) => (
                    <span key={d.id} className="doc-pill" title={d.filepath || d.filename}>
                        <FileText size={9} className="doc-pill-icon" aria-hidden="true" />
                        <span className="doc-pill-name" dir="auto">{d.filename}</span>
                        <button
                            className="doc-pill-remove"
                            onClick={(e) => handleRemoveDocument(e, d.id)}
                            aria-label={`Remove ${d.filename} from this chat`}
                        >
                            <X size={10} />
                        </button>
                    </span>
                ))}
                {!docsExpanded && hiddenCount > 0 && (
                    <button className="doc-pill-more" onClick={(e) => { e.stopPropagation(); setDocsExpanded(true); }}>
                        +{hiddenCount} more
                    </button>
                )}
                {docsExpanded && hiddenCount > 0 && (
                    <button className="doc-pill-collapse" onClick={(e) => { e.stopPropagation(); setDocsExpanded(false); }}>
                        show less
                    </button>
                )}
            </div>
        </div>
    );
}
