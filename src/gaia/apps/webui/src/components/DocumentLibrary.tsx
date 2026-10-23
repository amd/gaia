// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState, useCallback, useRef } from 'react';
import { X, Upload, Trash2, FileText, FolderOpen, Search, StopCircle, CheckCircle, AlertCircle, Loader, Clock, Link2, RefreshCcw } from 'lucide-react';
import { useChatStore } from '../stores/chatStore';
import { useEscapeToClose } from '../hooks/useEscapeToClose';
import * as api from '../services/api';
import { log } from '../utils/logger';
import { formatSize } from '../utils/format';
import { PERMANENT_FAILURE_HINTS, UploadErrorToast, getUnsupportedCategory, isExtensionSupported } from './UnsupportedFeature';
import type { Document, IndexFolderResponse } from '../types';
import './DocumentLibrary.css';

/** Split a stored indexing error into a one-line summary and the rest. */
function splitFailureReason(lastError: string): { summary: string; more: string } {
    const text = lastError.replace(/^RAG indexing failed for [^:]+:\s*/, '').trim();
    const [first, ...rest] = text.split('\n');
    return { summary: first.trim(), more: rest.join('\n').trim() };
}

/** One sentence on what folder indexing added, skipped and couldn't read. */
function describeFolderResult(folderName: string, result: IndexFolderResponse): string {
    const skipped = result.skipped ?? 0;
    const parts: string[] = [];
    if (result.indexed > 0) {
        parts.push(`Indexed ${result.indexed} file${result.indexed !== 1 ? 's' : ''} from "${folderName}".`);
    } else if (result.failed === 0) {
        parts.push(`"${folderName}" has no documents GAIA can index.`);
    }
    if (skipped > 0) {
        parts.push(`Skipped ${skipped} file${skipped !== 1 ? 's' : ''} of a type GAIA doesn't index.`);
    }
    if (result.failed > 0) {
        const first = result.errors[0] ? ` ${result.errors[0].split('\n')[0]}` : '';
        parts.push(`${result.failed} file${result.failed !== 1 ? 's' : ''} couldn't be indexed.${first}`);
    }
    parts.push('Subfolders aren\'t included.');
    return parts.join(' ');
}

/** Compute elapsed time as human-readable string. */
function elapsed(startMs: number): string {
    const sec = Math.floor((Date.now() - startMs) / 1000);
    if (sec < 60) return `${sec}s`;
    const min = Math.floor(sec / 60);
    const rem = sec % 60;
    return `${min}m ${rem}s`;
}

/** Format an ISO timestamp as a relative or absolute local time string. */
function formatTimestamp(isoString: string): string {
    try {
        const date = new Date(isoString);
        if (isNaN(date.getTime())) return '';
        const now = new Date();
        const diffMs = now.getTime() - date.getTime();
        const diffSec = Math.floor(diffMs / 1000);
        const diffMin = Math.floor(diffSec / 60);
        const diffHour = Math.floor(diffMin / 60);
        const diffDay = Math.floor(diffHour / 24);

        if (diffSec < 60) return 'just now';
        if (diffMin < 60) return `${diffMin}m ago`;
        if (diffHour < 24) return `${diffHour}h ago`;
        if (diffDay === 1) return 'yesterday';
        if (diffDay < 7) return `${diffDay}d ago`;

        // Older than a week: show date + time
        return date.toLocaleDateString(undefined, {
            month: 'short',
            day: 'numeric',
            hour: 'numeric',
            minute: '2-digit',
        });
    } catch {
        return '';
    }
}

/** Format an ISO timestamp as a full local date/time for tooltips. */
function formatFullTimestamp(isoString: string): string {
    try {
        const date = new Date(isoString);
        if (isNaN(date.getTime())) return '';
        return date.toLocaleString(undefined, {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: 'numeric',
            minute: '2-digit',
            second: '2-digit',
        });
    } catch {
        return '';
    }
}

export function DocumentLibrary() {
    const { documents, setDocuments, setShowDocLibrary, setShowFileBrowser,
            currentSessionId, sessions, updateSessionInList,
            draftDocumentIds, addDraftDocuments, removeDraftDocument } = useChatStore();
    const currentSession = sessions.find(s => s.id === currentSessionId) ?? null;
    const closeLibrary = useCallback(() => setShowDocLibrary(false), [setShowDocLibrary]);
    useEscapeToClose(true, closeLibrary);
    const [isDragOver, setIsDragOver] = useState(false);
    const [isUploading, setIsUploading] = useState(false);
    const [uploadStatus, setUploadStatus] = useState('');
    const [folderPath, setFolderPath] = useState('');
    // Upload error state for rich error toasts
    const [uploadError, setUploadError] = useState<{ filename: string; error: string; status?: number; kind?: string | null } | null>(null);
    // Outcome of indexing a folder typed into the path box
    const [folderNotice, setFolderNotice] = useState<string | null>(null);
    // Documents with a reindex in flight — guards double-click and shows progress
    const [reindexingIds, setReindexingIds] = useState<Set<string>>(new Set());
    // Track indexing start times for elapsed display
    const indexingStartTimes = useRef<Map<string, number>>(new Map());
    // Force re-render for elapsed time updates
    const [, setTick] = useState(0);

    // Load documents on mount
    useEffect(() => {
        log.doc.info('Loading document library...');
        const t = log.doc.time();
        api.listDocuments()
            .then((data) => {
                const docs = data.documents || [];
                setDocuments(docs);
                log.doc.timed(`Loaded ${docs.length} document(s), ${data.total_chunks || 0} chunks, ${data.total_size_bytes || 0} bytes total`, t);
            })
            .catch((err) => {
                log.doc.error('Failed to load documents', err);
                setDocuments([]);
            });
    }, [setDocuments]);

    // Poll for indexing status on documents that are still 'indexing'
    const hasIndexingDocs = documents.some(
        (d) => d.indexing_status === 'indexing' || d.indexing_status === 'pending'
    );

    useEffect(() => {
        if (!hasIndexingDocs) return;

        // Set start times for new indexing docs (use fresh state)
        const currentDocs = useChatStore.getState().documents;
        for (const doc of currentDocs) {
            if ((doc.indexing_status === 'indexing' || doc.indexing_status === 'pending')
                && !indexingStartTimes.current.has(doc.id)) {
                indexingStartTimes.current.set(doc.id, Date.now());
            }
        }

        let isPolling = false;
        const interval = setInterval(async () => {
            // Tick to update elapsed time display
            setTick((n) => n + 1);

            if (isPolling) return; // Skip if previous poll still in-flight
            isPolling = true;

            try {
                // Read fresh state inside callback to avoid stale closure
                const freshDocs = useChatStore.getState().documents;
                const indexingDocs = freshDocs.filter(
                    (d) => d.indexing_status === 'indexing' || d.indexing_status === 'pending'
                );

                for (const doc of indexingDocs) {
                    try {
                        const status = await api.getDocumentStatus(doc.id);
                        if (status.indexing_status !== 'indexing' && status.indexing_status !== 'pending') {
                            indexingStartTimes.current.delete(doc.id);
                            const data = await api.listDocuments();
                            setDocuments(data.documents || []);
                            break;
                        }
                    } catch {
                        // ignore poll errors
                    }
                }
            } finally {
                isPolling = false;
            }
        }, 2000);

        return () => clearInterval(interval);
    }, [hasIndexingDocs, setDocuments]);

    // Clean up start times for docs that are no longer indexing
    useEffect(() => {
        const activeIds = new Set(
            documents
                .filter((d) => d.indexing_status === 'indexing' || d.indexing_status === 'pending')
                .map((d) => d.id)
        );
        for (const id of indexingStartTimes.current.keys()) {
            if (!activeIds.has(id)) {
                indexingStartTimes.current.delete(id);
            }
        }
    }, [documents]);

    // Periodic refresh to pick up server-side re-indexing and update timestamps
    useEffect(() => {
        const refreshInterval = setInterval(async () => {
            try {
                const data = await api.listDocuments();
                const freshDocs = data.documents || [];
                // Only update if something changed (avoid unnecessary re-renders)
                const currentDocs = useChatStore.getState().documents;
                const changed = freshDocs.length !== currentDocs.length ||
                    freshDocs.some((d, i) => {
                        const cur = currentDocs.find((c) => c.id === d.id);
                        return !cur ||
                            cur.indexing_status !== d.indexing_status ||
                            cur.indexed_at !== d.indexed_at ||
                            cur.chunk_count !== d.chunk_count;
                    });
                if (changed) {
                    setDocuments(freshDocs);
                    log.doc.debug('Document list refreshed (server-side changes detected)');
                }
            } catch {
                // Ignore refresh errors
            }
        }, 15000); // Refresh every 15 seconds

        return () => clearInterval(refreshInterval);
    }, [setDocuments]);

    const totalSize = documents.reduce((sum, d) => sum + d.file_size, 0);
    const totalChunks = documents.reduce((sum, d) => sum + d.chunk_count, 0);

    // Shared upload state-machine: preflight extension check, set UI state,
    // run the caller-provided uploader, auto-attach the result to the current
    // session, and refresh the document list. Used by both path-based and
    // blob-based upload flows.
    // Make newly indexed documents usable: attach to the open chat, or hold them for the next one.
    const attachIndexed = useCallback(async (docIds: string[], label: string) => {
        if (docIds.length === 0) return;
        if (!currentSessionId) {
            // No chat is open: the agent only sees attached documents, so hold them for the next one.
            addDraftDocuments(docIds);
            log.doc.info(`"${label}" will be attached to the next new chat`);
            return;
        }
        const attached: string[] = [];
        for (const id of docIds) {
            try {
                await api.attachDocument(currentSessionId, id);
                attached.push(id);
            } catch (attachErr) {
                log.doc.warn(`Could not attach document to session: ${attachErr}`);
            }
        }
        const freshSession = useChatStore.getState().sessions.find(s => s.id === currentSessionId);
        const existing = freshSession?.document_ids ?? [];
        const added = attached.filter((id) => !existing.includes(id));
        if (added.length > 0) {
            updateSessionInList(currentSessionId, { document_ids: [...existing, ...added] });
        }
        log.doc.info(`Auto-attached "${label}" to session ${currentSessionId}`);
    }, [currentSessionId, updateSessionInList, addDraftDocuments]);

    const runUpload = useCallback(async (
        filename: string,
        uploader: () => Promise<Document>,
        onFolder?: () => Promise<void>,
    ) => {
        const ext = filename.includes('.') ? '.' + filename.split('.').pop()?.toLowerCase() : '';
        const unsupportedMessage = ext && !isExtensionSupported(ext)
            ? (getUnsupportedCategory(ext)?.message ?? `The file type "${ext}" is not supported for indexing.`)
            : null;
        // A typed path may be a dotted folder name (notes.v2), so only the server can rule it out.
        if (unsupportedMessage && !onFolder) {
            log.doc.warn(`Unsupported file type: ${filename} (${ext})`);
            setUploadError({ filename, error: unsupportedMessage });
            return;
        }

        log.doc.info(`Indexing document: ${filename}`);
        const t = log.doc.time();
        setIsUploading(true);
        setUploadStatus(`Indexing ${filename}...`);
        setUploadError(null);
        setFolderNotice(null);
        try {
            const doc = await uploader();
            log.doc.timed(`Indexed "${filename}": ${doc?.chunk_count || '?'} chunks`, t);
            // Auto-attach so the agent can immediately use it
            if (doc?.id) await attachIndexed([doc.id], filename);
            // Refresh document list
            const data = await api.listDocuments();
            setDocuments(data.documents || []);
            setUploadStatus('');
        } catch (err) {
            // ApiError's fields, read by shape: the class itself is mocked in tests.
            const { httpStatus, reason } = (err ?? {}) as { httpStatus?: number; reason?: string | null };
            if (reason === 'is_folder' && onFolder) {
                await onFolder();
                return;
            }
            log.doc.error(`Failed to index "${filename}"`, err);
            const errMsg = httpStatus === 400 && unsupportedMessage
                ? unsupportedMessage
                : err instanceof Error ? err.message : 'Upload failed';
            setUploadError({ filename, error: errMsg, status: httpStatus, kind: reason });
            setUploadStatus('');
        } finally {
            setIsUploading(false);
        }
    }, [attachIndexed, setDocuments]);

    /** Index the documents directly inside a folder typed into the path box. */
    const indexFolderPath = useCallback(async (folder: string, folderName: string) => {
        log.doc.info(`"${folder}" is a folder; indexing its documents`);
        setUploadStatus(`Indexing the documents in ${folderName}...`);
        try {
            // Top level only: a typed path like a home folder would otherwise index everything below it.
            const result = await api.indexFolder(folder, false);
            await attachIndexed(result.documents.map((d) => d.id).filter(Boolean), folderName);
            setFolderNotice(describeFolderResult(folderName, result));
            const data = await api.listDocuments();
            setDocuments(data.documents || []);
        } catch (err) {
            log.doc.error(`Failed to index folder "${folder}"`, err);
            const errMsg = err instanceof Error ? err.message : 'Folder indexing failed';
            const { httpStatus } = (err ?? {}) as { httpStatus?: number };
            setUploadError({ filename: folderName, error: errMsg, status: httpStatus });
        } finally {
            setUploadStatus('');
        }
    }, [attachIndexed, setDocuments]);

    /** Index a document by a server-side filesystem path (used by the file browser and path input). */
    const uploadFile = useCallback(async (filepath: string, filename: string) => {
        await runUpload(
            filename,
            () => api.uploadDocumentByPath(filepath),
            () => indexFolderPath(filepath, filename),
        );
    }, [runUpload, indexFolderPath]);

    /**
     * Index a document by uploading its bytes directly (used by drag-and-drop).
     *
     * We can't use the path-based endpoint for drag-drop because browser File
     * objects don't expose an absolute filesystem path — and in Electron 32+
     * the File.path augmentation was removed as well. Streaming the blob is
     * the only reliable cross-platform approach. See issue #728.
     */
    const uploadFileBlob = useCallback(async (file: File) => {
        await runUpload(file.name, () => api.uploadDocumentBlob(file));
    }, [runUpload]);

    const handleDrop = useCallback(async (e: React.DragEvent) => {
        e.preventDefault();
        setIsDragOver(false);
        const files = Array.from(e.dataTransfer.files);
        log.doc.info(`Dropped ${files.length} file(s) into Document Library`);
        for (const file of files) {
            await uploadFileBlob(file);
        }
    }, [uploadFileBlob]);

    const handleDeleteDoc = useCallback(async (id: string) => {
        const doc = documents.find((d) => d.id === id);
        log.doc.info(`Deleting document: ${doc?.filename || id}`);
        try {
            await api.deleteDocument(id);
            indexingStartTimes.current.delete(id);
            removeDraftDocument(id);
            setDocuments(documents.filter((d) => d.id !== id));
            log.doc.info(`Deleted document: ${doc?.filename || id}`);
        } catch (err) {
            log.doc.error(`Failed to delete document: ${doc?.filename || id}`, err);
        }
    }, [documents, setDocuments, removeDraftDocument]);

    const handleOpenFolder = useCallback(async (filepath: string, filename: string) => {
        log.doc.info(`Opening folder for: ${filepath}`);
        try {
            await api.openFileOrFolder(filepath, true);
        } catch (err) {
            log.doc.error(`Failed to open folder for: ${filename}`, err);
        }
    }, []);

    const handleCancelIndexing = useCallback(async (id: string) => {
        const doc = documents.find((d) => d.id === id);
        log.doc.info(`Cancelling indexing for: ${doc?.filename || id}`);
        try {
            await api.cancelIndexing(id);
            indexingStartTimes.current.delete(id);
            // Refresh
            const data = await api.listDocuments();
            setDocuments(data.documents || []);
            log.doc.info(`Cancelled indexing for: ${doc?.filename || id}`);
        } catch (err) {
            log.doc.error(`Failed to cancel indexing: ${doc?.filename || id}`, err);
        }
    }, [documents, setDocuments]);

    const handleReindex = useCallback(async (id: string) => {
        if (reindexingIds.has(id)) return; // guard against double-click
        const doc = documents.find((d) => d.id === id);
        log.doc.info(`Reindexing document: ${doc?.filename || id}`);
        setReindexingIds((s) => new Set(s).add(id));
        try {
            await api.reindexDocument(id);
            log.doc.info(`Reindex complete for: ${doc?.filename || id}`);
        } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            log.doc.error(`Reindex failed for: ${doc?.filename || id}`, err);
            setUploadError({ filename: doc?.filename || id, error: msg });
        } finally {
            // Refresh regardless of outcome so the updated status / last_error
            // shows even when the retry failed again.
            try {
                const data = await api.listDocuments();
                setDocuments(data.documents || []);
            } catch (e) {
                log.doc.error('Failed to refresh documents after reindex', e);
            }
            setReindexingIds((s) => {
                const next = new Set(s);
                next.delete(id);
                return next;
            });
        }
    }, [documents, setDocuments, reindexingIds]);

    const handleAttachDoc = useCallback(async (docId: string) => {
        const doc = documents.find((d) => d.id === docId);
        if (!currentSessionId) {
            addDraftDocuments([docId]);
            log.doc.info(`"${doc?.filename || docId}" will be attached to the next new chat`);
            return;
        }
        log.doc.info(`Attaching document to session: ${doc?.filename || docId}`);
        try {
            await api.attachDocument(currentSessionId, docId);
            const freshSession = useChatStore.getState().sessions.find(s => s.id === currentSessionId);
            const existing = freshSession?.document_ids ?? [];
            if (!existing.includes(docId)) {
                updateSessionInList(currentSessionId, {
                    document_ids: [...existing, docId],
                });
            }
            log.doc.info(`Attached "${doc?.filename || docId}" to session ${currentSessionId}`);
        } catch (err) {
            log.doc.error(`Failed to attach document to session: ${doc?.filename || docId}`, err);
        }
    }, [currentSessionId, documents, updateSessionInList, addDraftDocuments]);

    const handleFolderSubmit = useCallback(async (e: React.FormEvent) => {
        e.preventDefault();
        if (!folderPath.trim()) return;
        const typed = folderPath.trim();
        log.doc.info(`Indexing from path input: ${typed}`);
        await uploadFile(typed, typed.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || 'file');
        setFolderPath('');
    }, [folderPath, uploadFile]);

    const renderDocStatus = (doc: Document) => {
        const status = doc.indexing_status || 'complete';
        const startTime = indexingStartTimes.current.get(doc.id);
        const indexedTime = doc.indexed_at ? formatTimestamp(doc.indexed_at) : '';
        const indexedTimeFull = doc.indexed_at ? formatFullTimestamp(doc.indexed_at) : '';

        switch (status) {
            case 'indexing':
            case 'pending':
                return (
                    <div className="doc-indexing-status">
                        <div className="doc-indexing-bar">
                            <div className="doc-indexing-bar-track">
                                <div className="doc-indexing-bar-fill" />
                            </div>
                            <span className="doc-indexing-label">
                                <Loader size={12} className="doc-spin" />
                                Indexing{startTime ? ` (${elapsed(startTime)})` : '...'}
                            </span>
                        </div>
                        <button
                            className="btn-cancel"
                            onClick={(e) => { e.stopPropagation(); handleCancelIndexing(doc.id); }}
                            title="Cancel indexing"
                            aria-label={`Cancel indexing ${doc.filename}`}
                        >
                            <StopCircle size={14} />
                            Cancel
                        </button>
                    </div>
                );
            case 'failed': {
                const hint = doc.error_kind ? PERMANENT_FAILURE_HINTS[doc.error_kind] : undefined;
                const reason = doc.last_error ? splitFailureReason(doc.last_error) : null;
                return (
                    <div className="doc-failure">
                        <div className="doc-indexing-status">
                            <span
                                className="doc-status-badge doc-status-failed"
                                title={doc.last_error || undefined}
                                aria-label={doc.last_error ? `Failed: ${doc.last_error}` : 'Indexing failed'}
                            >
                                <AlertCircle size={12} /> {hint ? "Can't be read" : 'Failed'}
                            </span>
                            <button
                                className={hint ? 'btn-retry btn-retry-quiet' : 'btn-retry'}
                                onClick={(e) => { e.stopPropagation(); handleReindex(doc.id); }}
                                title={hint ? 'Retry indexing (fix the file first, or this fails again)' : 'Retry indexing'}
                                aria-label={`Retry indexing ${doc.filename}`}
                                disabled={reindexingIds.has(doc.id)}
                            >
                                <RefreshCcw size={hint ? 12 : 14} />
                                {reindexingIds.has(doc.id) ? 'Retrying…' : 'Retry'}
                            </button>
                        </div>
                        {hint && <p className="doc-failure-hint">{hint}</p>}
                        {reason && (reason.more ? (
                            <details className="doc-failure-reason">
                                <summary>{reason.summary}</summary>
                                <p>{reason.more}</p>
                            </details>
                        ) : (
                            <p className="doc-failure-reason">{reason.summary}</p>
                        ))}
                        {!reason && !hint && (
                            <p className="doc-failure-reason">GAIA didn't record why. Retry to see the error.</p>
                        )}
                    </div>
                );
            }
            case 'cancelled':
                return (
                    <span className="doc-status-badge doc-status-cancelled">
                        <StopCircle size={12} /> Cancelled
                    </span>
                );
            case 'missing':
                return (
                    <span className="doc-status-badge doc-status-missing">
                        <AlertCircle size={12} /> File missing
                    </span>
                );
            default:
                return (
                    <span className="doc-meta">
                        {formatSize(doc.file_size)} &middot; {doc.chunk_count} chunk{doc.chunk_count !== 1 ? 's' : ''}
                        {indexedTime && (
                            <span className="doc-timestamp" title={`Indexed: ${indexedTimeFull}`}>
                                <Clock size={10} />
                                {indexedTime}
                            </span>
                        )}
                    </span>
                );
        }
    };

    return (
        <div className="modal-overlay" onClick={() => setShowDocLibrary(false)} role="dialog" aria-modal="true" aria-label="Document library">
            <div className="modal-panel doc-modal" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <h3>Document library</h3>
                    <button className="btn-icon" onClick={() => setShowDocLibrary(false)} aria-label="Close document library">
                        <X size={18} />
                    </button>
                </div>

                <div className="modal-body">
                    {/* Drop zone */}
                    <div
                        className={`drop-zone ${isDragOver ? 'drag-over' : ''} ${isUploading ? 'uploading' : ''}`}
                        onDragOver={(e) => { e.preventDefault(); setIsDragOver(true); }}
                        onDragLeave={() => setIsDragOver(false)}
                        onDrop={handleDrop}
                    >
                        {isUploading ? (
                            <>
                                <div className="upload-spinner" />
                                <p>{uploadStatus}</p>
                            </>
                        ) : (
                            <>
                                <Upload size={32} strokeWidth={1.5} />
                                <p>Drop files here to index</p>
                                <span className="drop-hint">PDF, TXT, MD, code files, and 50+ formats</span>
                            </>
                        )}
                    </div>

                    {/* Path input for folder/file */}
                    <form className="path-input-form" onSubmit={handleFolderSubmit}>
                        <FolderOpen size={16} className="path-icon" />
                        <input
                            type="text"
                            className="path-input"
                            value={folderPath}
                            onChange={(e) => setFolderPath(e.target.value)}
                            placeholder="Or enter a file or folder path: C:\docs\manual.pdf"
                            aria-label="File path to index"
                        />
                        <button type="submit" className="btn-secondary" disabled={!folderPath.trim() || isUploading}>
                            Index
                        </button>
                        <button
                            type="button"
                            className="btn-secondary"
                            onClick={() => { setShowDocLibrary(false); setShowFileBrowser(true); }}
                            title="Browse files on this computer"
                        >
                            <Search size={14} />
                            Browse files
                        </button>
                    </form>

                    {/* Upload error toast */}
                    {uploadError && (
                        <UploadErrorToast
                            filename={uploadError.filename}
                            error={uploadError.error}
                            status={uploadError.status}
                            kind={uploadError.kind}
                            onDismiss={() => setUploadError(null)}
                        />
                    )}

                    {folderNotice && (
                        <div className="doc-folder-notice" role="status">
                            <CheckCircle size={14} />
                            <span>{folderNotice}</span>
                            <button className="btn-icon-sm" onClick={() => setFolderNotice(null)} aria-label="Dismiss folder indexing result">
                                <X size={12} />
                            </button>
                        </div>
                    )}

                    {/* Stats */}
                    {documents.length > 0 && (
                        <div className="doc-stats">
                            <span>{documents.length} document{documents.length !== 1 ? 's' : ''}</span>
                            <span>&middot;</span>
                            <span>{totalChunks} chunk{totalChunks !== 1 ? 's' : ''}</span>
                            <span>&middot;</span>
                            <span>{formatSize(totalSize)}</span>
                        </div>
                    )}

                    {/* Document list */}
                    <div className="doc-list">
                        {documents.length === 0 && !isUploading && (
                            <div className="empty-docs">
                                <FileText size={32} strokeWidth={1} />
                                <p>No documents indexed yet</p>
                                <span>Drop files above or enter a file path</span>
                            </div>
                        )}
                        {documents.map((doc) => (
                            <div key={doc.id} className={`doc-row ${doc.indexing_status === 'indexing' ? 'doc-row-indexing' : ''}`}>
                                <div className="doc-info">
                                    <span className="doc-name">{doc.filename}</span>
                                    {renderDocStatus(doc)}
                                </div>
                                <div className="doc-row-actions">
                                    {doc.filepath && (
                                        <button
                                            className="btn-icon-sm doc-open-folder"
                                            onClick={() => handleOpenFolder(doc.filepath, doc.filename)}
                                            title={`Open folder: ${doc.filepath}`}
                                            aria-label={`Open containing folder for ${doc.filename}`}
                                        >
                                            <FolderOpen size={14} />
                                        </button>
                                    )}
                                    {doc.indexing_status === 'complete' &&
                                     !(currentSessionId
                                        ? currentSession?.document_ids?.includes(doc.id)
                                        : draftDocumentIds.includes(doc.id)) && (
                                        <button
                                            className="btn-icon-sm doc-attach"
                                            onClick={() => handleAttachDoc(doc.id)}
                                            title={currentSessionId ? 'Use in current session' : 'Use in your next chat'}
                                            aria-label={currentSessionId
                                                ? `Attach ${doc.filename} to current session`
                                                : `Attach ${doc.filename} to your next chat`}
                                        >
                                            <Link2 size={14} />
                                        </button>
                                    )}
                                    {doc.indexing_status !== 'indexing' && doc.indexing_status !== 'pending' && (
                                        <button
                                            className="btn-icon-sm doc-delete"
                                            onClick={() => handleDeleteDoc(doc.id)}
                                            title="Remove"
                                            aria-label={`Remove ${doc.filename}`}
                                        >
                                            <Trash2 size={14} />
                                        </button>
                                    )}
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            </div>
        </div>
    );
}
