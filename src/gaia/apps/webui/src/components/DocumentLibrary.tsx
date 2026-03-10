// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState, useCallback } from 'react';
import { X, Upload, Trash2, FileText, FolderOpen, Search } from 'lucide-react';
import { useChatStore } from '../stores/chatStore';
import * as api from '../services/api';
import { log } from '../utils/logger';
import type { Document } from '../types';
import './DocumentLibrary.css';

export function DocumentLibrary() {
    const { documents, setDocuments, setShowDocLibrary, setShowFileBrowser } = useChatStore();
    const [isDragOver, setIsDragOver] = useState(false);
    const [isUploading, setIsUploading] = useState(false);
    const [uploadStatus, setUploadStatus] = useState('');
    const [folderPath, setFolderPath] = useState('');

    // Load documents
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
        log.doc.info(`Indexing document: ${filename} (${filepath})`);
        const t = log.doc.time();
        setIsUploading(true);
        setUploadStatus(`Indexing ${filename}...`);
        try {
            const doc = await api.uploadDocumentByPath(filepath);
            log.doc.timed(`Indexed "${filename}": ${doc?.chunk_count || '?'} chunks`, t);
            const data = await api.listDocuments();
            setDocuments(data.documents || []);
            setUploadStatus('');
        } catch (err) {
            log.doc.error(`Failed to index "${filename}"`, err);
            setUploadStatus('Upload failed');
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
            setDocuments(documents.filter((d) => d.id !== id));
            log.doc.info(`Deleted document: ${doc?.filename || id}`);
        } catch (err) {
            log.doc.error(`Failed to delete document: ${doc?.filename || id}`, err);
        }
    }, [documents, setDocuments]);

    const handleFolderSubmit = useCallback(async (e: React.FormEvent) => {
        e.preventDefault();
        if (!folderPath.trim()) return;
        log.doc.info(`Indexing from path input: ${folderPath.trim()}`);
        await uploadFile(folderPath.trim(), folderPath.trim().split(/[\\/]/).pop() || 'file');
        setFolderPath('');
    }, [folderPath, uploadFile]);

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
                            <div key={doc.id} className="doc-row">
                                <div className="doc-info">
                                    <span className="doc-name">{doc.filename}</span>
                                    <span className="doc-meta">
                                        {formatSize(doc.file_size)} &middot; {doc.chunk_count} chunks
                                    </span>
                                </div>
                                <button
                                    className="btn-icon-sm doc-delete"
                                    onClick={() => handleDeleteDoc(doc.id)}
                                    title="Remove"
                                    aria-label={`Remove ${doc.filename}`}
                                >
                                    <Trash2 size={14} />
                                </button>
                            </div>
                        ))}
                    </div>
                </div>
            </div>
        </div>
    );
}
