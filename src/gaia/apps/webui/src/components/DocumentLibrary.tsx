// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState, useCallback, useRef } from 'react';
import { X, Upload, Trash2, FileText, FolderOpen, Search, StopCircle, CheckCircle, AlertCircle, Loader, Clock } from 'lucide-react';
import { useChatStore } from '../stores/chatStore';
import * as api from '../services/api';
import { log } from '../utils/logger';
import { UploadErrorToast, getUnsupportedCategory, isExtensionSupported } from './UnsupportedFeature';
import type { Document } from '../types';
import './DocumentLibrary.css';

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
    const { documents, setDocuments, setShowDocLibrary, setShowFileBrowser } = useChatStore();
    const [isDragOver, setIsDragOver] = useState(false);
    const [isUploading, setIsUploading] = useState(false);
    const [uploadStatus, setUploadStatus] = useState('');
    const [folderPath, setFolderPath] = useState('');
    // Upload error state for rich error toasts
    const [uploadError, setUploadError] = useState<{ filename: string; error: string } | null>(null);
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

    const formatSize = (bytes: number) => {
        if (bytes <= 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB'];
        const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), sizes.length - 1);
        return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
    };

    const uploadFile = useCallback(async (filepath: string, filename: string) => {
        // Pre-check: warn about unsupported file types before sending to server
        const ext = filename.includes('.') ? '.' + filename.split('.').pop()?.toLowerCase() : '';
        if (ext && !isExtensionSupported(ext)) {
            const category = getUnsupportedCategory(ext);
            const errMsg = category
                ? category.message
                : `The file type "${ext}" is not supported for indexing.`;
            log.doc.warn(`Unsupported file type: ${filename} (${ext})`);
            setUploadError({ filename, error: errMsg });
            return;
        }

        log.doc.info(`Indexing document: ${filename} (${filepath})`);
        const t = log.doc.time();
        setIsUploading(true);
        setUploadStatus(`Indexing ${filename}...`);
        setUploadError(null);
        try {
            const doc = await api.uploadDocumentByPath(filepath);
            log.doc.timed(`Indexed "${filename}": ${doc?.chunk_count || '?'} chunks`, t);
            // Refresh document list
            const data = await api.listDocuments();
            setDocuments(data.documents || []);
            setUploadStatus('');
        } catch (err) {
            log.doc.error(`Failed to index "${filename}"`, err);
            // Extract error message from API response
            const errMsg = err instanceof Error ? err.message : 'Upload failed';
            setUploadError({ filename, error: errMsg });
            setUploadStatus('');
        } finally {
            setIsUploading(false);
        }
    }, [setDocuments]);

    const handleDrop = useCallback(async (e: React.DragEvent) => {
        e.preventDefault();
        setIsDragOver(false);
        const files = Array.from(e.dataTransfer.files);
        log.doc.info(`Dropped ${files.length} file(s) into Document Library`);
        for (const file of files) {
            const path = (file as any).path || file.name;
            await uploadFile(path, file.name);
        }
    }, [uploadFile]);

    const handleDeleteDoc = useCallback(async (id: string) => {
        const doc = documents.find((d) => d.id === id);
        log.doc.info(`Deleting document: ${doc?.filename || id}`);
        try {
            await api.deleteDocument(id);
            indexingStartTimes.current.delete(id);
            setDocuments(documents.filter((d) => d.id !== id));
            log.doc.info(`Deleted document: ${doc?.filename || id}`);
        } catch (err) {
            log.doc.error(`Failed to delete document: ${doc?.filename || id}`, err);
        }
    }, [documents, setDocuments]);

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

    const handleFolderSubmit = useCallback(async (e: React.FormEvent) => {
        e.preventDefault();
        if (!folderPath.trim()) return;
        log.doc.info(`Indexing from path input: ${folderPath.trim()}`);
        await uploadFile(folderPath.trim(), folderPath.trim().split(/[\\/]/).pop() || 'file');
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
            case 'failed':
                return (
                    <span className="doc-status-badge doc-status-failed">
                        <AlertCircle size={12} /> Failed
                    </span>
                );
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
                        {formatSize(doc.file_size)} &middot; {doc.chunk_count} chunks
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
        <div className="modal-overlay" onClick={() => setShowDocLibrary(false)} role="dialog" aria-modal="true" aria-label="Document Library">
            <div className="modal-panel doc-modal" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <h3>Document Library</h3>
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
                            placeholder="Or enter file path: C:\docs\manual.pdf"
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
                            Browse Files
                        </button>
                    </form>

                    {/* Upload error toast */}
                    {uploadError && (
                        <UploadErrorToast
                            filename={uploadError.filename}
                            error={uploadError.error}
                            onDismiss={() => setUploadError(null)}
                        />
                    )}

                    {/* Stats */}
                    {documents.length > 0 && (
                        <div className="doc-stats">
                            <span>{documents.length} document{documents.length !== 1 ? 's' : ''}</span>
                            <span>&middot;</span>
                            <span>{totalChunks} chunks</span>
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
