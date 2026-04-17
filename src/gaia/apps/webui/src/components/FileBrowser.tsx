// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState, useCallback, useRef } from 'react';
import {
    X, Search, Folder, FileText, Home, Download, Monitor, ChevronRight,
    File, FolderOpen, ArrowUp, Brain, Upload, HardDrive,
    Table, Code
} from 'lucide-react';
import { useChatStore } from '../stores/chatStore';
import * as api from '../services/api';
import { log } from '../utils/logger';
import { UploadErrorToast, isExtensionSupported, getUnsupportedCategory } from './UnsupportedFeature';
import type { FileEntry, BrowseResponse, QuickLink } from '../types';
import './FileBrowser.css';
import './UnsupportedFeature.css';

// File type icons mapping
function getFileIcon(entry: FileEntry) {
    if (entry.type === 'folder') return <Folder size={16} />;
    const ext = (entry.extension || '').toLowerCase();
    if (['.csv', '.xlsx', '.xls', '.tsv'].includes(ext)) return <Table size={16} />;
    if (['.py', '.js', '.ts', '.java', '.c', '.cpp', '.go', '.rs'].includes(ext)) return <Code size={16} />;
    if (['.pdf', '.doc', '.docx', '.txt', '.md'].includes(ext)) return <FileText size={16} />;
    return <File size={16} />;
}

// Format file size
function formatSize(bytes: number): string {
    if (bytes <= 0) return '';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), sizes.length - 1);
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

// Format date
function formatDate(iso: string): string {
    if (!iso) return '';
    const d = new Date(iso);
    const now = new Date();
    const diff = now.getTime() - d.getTime();
    const hours = Math.floor(diff / 3600000);
    if (hours < 1) return 'Just now';
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    if (days < 7) return `${days}d ago`;
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: d.getFullYear() !== now.getFullYear() ? 'numeric' : undefined });
}

// Quick link icon mapping
function getQuickLinkIcon(icon: string) {
    switch (icon) {
        case 'home': return <Home size={14} />;
        case 'desktop': return <Monitor size={14} />;
        case 'documents': return <FileText size={14} />;
        case 'download': return <Download size={14} />;
        default: return <Folder size={14} />;
    }
}

// File type filter options
const FILE_TYPE_FILTERS = [
    { label: 'All Files', value: '' },
    { label: 'Documents', value: 'pdf,doc,docx,txt,md' },
    { label: 'Spreadsheets', value: 'csv,xlsx,xls,tsv' },
    { label: 'Code', value: 'py,js,ts,java,c,cpp,go,rs,rb,sh' },
    { label: 'Data', value: 'json,xml,yaml,yml,csv,tsv' },
];

interface FilePreview {
    name: string;
    path: string;
    size_display: string;
    extension: string;
    is_text: boolean;
    preview_lines: string[];
    total_lines: number | null;
    columns: string[] | null;
    row_count: number | null;
}

/** Result of indexing and attaching files to a session. */
interface IndexResult {
    docIds: string[];
    supported: string[];
    succeededFiles: string[];
    unsupported: string[];
    failed: number;
    lastError: string;
}

/**
 * Core indexing + attaching logic shared by "Index Selected" and "Ask Agent".
 * Validates extensions, indexes supported files, and attaches them to the session.
 */
async function indexAndAttachFiles(
    files: string[],
    entries: FileEntry[],
    sessionId: string | null,
    updateSessionInList: (id: string, patch: Record<string, unknown>) => void,
): Promise<IndexResult> {
    const supported: string[] = [];
    const unsupported: string[] = [];
    for (const filepath of files) {
        const ext = filepath.includes('.') ? '.' + filepath.split('.').pop()?.toLowerCase() : '';
        if (ext && !isExtensionSupported(ext)) {
            unsupported.push(filepath);
        } else {
            supported.push(filepath);
        }
    }

    const docIds: string[] = [];
    const succeededFiles: string[] = [];
    let failed = 0;
    let lastError = '';
    const folderPaths = new Set(entries.filter(e => e.type === 'folder').map(e => e.path));

    for (const filepath of supported) {
        try {
            if (folderPaths.has(filepath)) {
                const result = await api.indexFolder(filepath);
                const newIds: string[] = [];
                result.documents.forEach(d => { if (d.id) newIds.push(d.id); });
                if (newIds.length > 0) {
                    newIds.forEach(id => docIds.push(id));
                    succeededFiles.push(filepath);
                }
            } else {
                const doc = await api.uploadDocumentByPath(filepath);
                if (doc?.id) {
                    docIds.push(doc.id);
                    succeededFiles.push(filepath);
                }
            }
        } catch (err) {
            failed++;
            lastError = err instanceof Error ? err.message : 'Unknown error';
        }
    }

    // Attach indexed documents to the active session
    if (sessionId && docIds.length > 0) {
        for (const docId of docIds) {
            try {
                await api.attachDocument(sessionId, docId);
            } catch (attachErr) {
                log.doc.warn(`Could not attach document to session: ${attachErr}`);
            }
        }
        const freshSession = useChatStore.getState().sessions.find(s => s.id === sessionId);
        const existing = freshSession?.document_ids ?? [];
        const toAdd = docIds.filter(id => !existing.includes(id));
        if (toAdd.length > 0) {
            updateSessionInList(sessionId, { document_ids: [...existing, ...toAdd] });
        }
    }

    return { docIds, supported, succeededFiles, unsupported, failed, lastError };
}

export function FileBrowser() {
    const { setShowFileBrowser, currentSessionId, updateSessionInList, setPendingPrompt } = useChatStore();

    // Browse state
    const [currentPath, setCurrentPath] = useState<string>('');
    const [parentPath, setParentPath] = useState<string | null>(null);
    const [entries, setEntries] = useState<FileEntry[]>([]);
    const [quickLinks, setQuickLinks] = useState<QuickLink[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Search state
    const [searchQuery, setSearchQuery] = useState('');
    const [searchResults, setSearchResults] = useState<any[] | null>(null);
    const [isSearching, setIsSearching] = useState(false);
    const [typeFilter, setTypeFilter] = useState('');

    // Selection state
    const [selectedFiles, setSelectedFiles] = useState<Set<string>>(new Set());

    // Preview state
    const [previewFile, setPreviewFile] = useState<FilePreview | null>(null);
    const [previewLoading, setPreviewLoading] = useState(false);

    // Indexing state
    const [indexingFiles, setIndexingFiles] = useState<Set<string>>(new Set());
    const [indexStatus, setIndexStatus] = useState<string | null>(null);
    const indexStatusTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    // Upload error toast state
    const [indexError, setIndexError] = useState<{ filename: string; error: string } | null>(null);

    const loadDirectory = useCallback(async (path?: string) => {
        setLoading(true);
        setError(null);
        setSearchResults(null);
        setSearchQuery('');
        try {
            const data: BrowseResponse = await api.browseFiles(path);
            setCurrentPath(data.current_path);
            setParentPath(data.parent_path);
            setEntries(data.entries);
            if (data.quick_links.length > 0) setQuickLinks(data.quick_links);
            log.ui.info(`Browsing: ${data.current_path} (${data.entries.length} items)`);
        } catch (err) {
            const msg = err instanceof Error ? err.message : 'Failed to load directory';
            setError(msg);
            log.ui.error('Browse failed', err);
        } finally {
            setLoading(false);
        }
    }, []);

    // Load initial directory
    useEffect(() => {
        loadDirectory();
    }, [loadDirectory]);

    // Clean up index status timer on unmount
    useEffect(() => {
        return () => {
            if (indexStatusTimerRef.current) clearTimeout(indexStatusTimerRef.current);
        };
    }, []);

    const handleSearch = useCallback(async () => {
        if (!searchQuery.trim()) {
            setSearchResults(null);
            return;
        }
        setIsSearching(true);
        setError(null);
        try {
            const data = await api.searchFiles(searchQuery.trim(), typeFilter || undefined, 30);
            setSearchResults(data.results);
            log.ui.info(`Search "${searchQuery}": ${data.total} results`);
        } catch (err) {
            const msg = err instanceof Error ? err.message : 'Search failed';
            setError(msg);
            log.ui.error('Search failed', err);
        } finally {
            setIsSearching(false);
        }
    }, [searchQuery, typeFilter]);

    const handleSearchKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter') handleSearch();
    };

    const handleEntryClick = useCallback((entry: FileEntry) => {
        if (entry.type === 'folder') {
            loadDirectory(entry.path);
            setSelectedFiles(new Set());
            setPreviewFile(null);
        }
    }, [loadDirectory]);

    const handleFilePreview = useCallback(async (path: string) => {
        setPreviewLoading(true);
        try {
            const data = await api.previewFile(path);
            setPreviewFile(data as FilePreview);
        } catch (err) {
            log.ui.error('Preview failed', err);
        } finally {
            setPreviewLoading(false);
        }
    }, []);

    const handleIndexSelected = useCallback(async () => {
        if (selectedFiles.size === 0) return;
        const files = Array.from(selectedFiles);
        setIndexError(null);

        // Quick pre-check for all-unsupported case (show error immediately)
        const hasSupported = files.some(f => {
            const ext = f.includes('.') ? '.' + f.split('.').pop()?.toLowerCase() : '';
            return !ext || isExtensionSupported(ext);
        });
        if (!hasSupported) {
            const firstFile = files[0];
            const ext = '.' + firstFile.split('.').pop()?.toLowerCase();
            const category = getUnsupportedCategory(ext);
            setIndexError({
                filename: files.length === 1
                    ? firstFile.split(/[\\/]/).pop() || firstFile
                    : `${files.length} files`,
                error: category
                    ? category.message
                    : `File type "${ext}" is not supported for indexing.`,
            });
            return;
        }

        setIndexingFiles(new Set(files));
        setIndexStatus(`Indexing ${files.length} file(s)...`);

        try {
            const result = await indexAndAttachFiles(files, entries, currentSessionId, updateSessionInList);

            if (result.failed > 0) {
                setIndexStatus(`Done: ${result.docIds.length} indexed, ${result.failed} failed`);
                setIndexError({
                    filename: `${result.failed} file(s)`,
                    error: result.lastError || 'Indexing failed for some files',
                });
            } else {
                const skippedNote = result.unsupported.length > 0
                    ? ` (${result.unsupported.length} skipped — unsupported type)`
                    : '';
                setIndexStatus(`Successfully indexed ${result.docIds.length} file(s)${skippedNote}`);
            }
            if (indexStatusTimerRef.current) clearTimeout(indexStatusTimerRef.current);
            indexStatusTimerRef.current = setTimeout(() => setIndexStatus(null), 5000);
            setSelectedFiles(new Set());
        } catch (err) {
            log.doc.error('Index selected failed', err);
            setIndexError({
                filename: files.length === 1 ? files[0].split(/[\\/]/).pop() || files[0] : `${files.length} files`,
                error: err instanceof Error ? err.message : 'An unexpected error occurred.',
            });
        } finally {
            setIndexingFiles(new Set());
        }
    }, [selectedFiles, currentSessionId, updateSessionInList, entries]);

    const handleAskAgent = useCallback(async () => {
        if (selectedFiles.size === 0) return;
        const files = Array.from(selectedFiles);
        setIndexError(null);
        setIndexingFiles(new Set(files));
        setIndexStatus(`Indexing ${files.length} file(s) for analysis...`);

        try {
            const result = await indexAndAttachFiles(files, entries, currentSessionId, updateSessionInList);

            if (result.supported.length === 0) {
                const ext = '.' + files[0].split('.').pop()?.toLowerCase();
                const category = getUnsupportedCategory(ext);
                setIndexError({
                    filename: files.length === 1 ? files[0].split(/[\\/]/).pop() || files[0] : `${files.length} files`,
                    error: category?.message ?? `File type "${ext}" is not supported for indexing.`,
                });
                return;
            }

            if (result.docIds.length === 0) {
                setIndexError({
                    filename: result.supported.length === 1
                        ? result.supported[0].split(/[\\/]/).pop() || result.supported[0]
                        : `${result.supported.length} files`,
                    error: 'Failed to index selected file(s). Please try again.',
                });
                return;
            }

            // Surface partial-failure before closing modal so user knows some files were dropped
            if (result.failed > 0) {
                setIndexStatus(`Warning: ${result.failed} file(s) failed to index and will be excluded.`);
            }

            // Build prompt from files that were actually indexed (not just supported)
            const fileNames = (result.succeededFiles.length > 0 ? result.succeededFiles : result.supported)
                .map(f => f.split(/[\\/]/).pop() || f);
            const prompt = fileNames.length === 1
                ? `Please analyze this document for me: ${fileNames[0]}`
                : `Please analyze these documents for me:\n${fileNames.map(n => `- ${n}`).join('\n')}`;

            setPendingPrompt(prompt);
            setShowFileBrowser(false);
            setSelectedFiles(new Set());
        } catch (err) {
            log.doc.error('Ask Agent failed', err);
            setIndexError({
                filename: files.length === 1 ? files[0].split(/[\\/]/).pop() || files[0] : `${files.length} files`,
                error: err instanceof Error ? err.message : 'An unexpected error occurred.',
            });
        } finally {
            setIndexingFiles(new Set());
            setIndexStatus(null);
        }
    }, [selectedFiles, currentSessionId, updateSessionInList, entries, setPendingPrompt, setShowFileBrowser]);

    // Build breadcrumb segments from current path
    const pathSegments = currentPath ? currentPath.replace(/\\/g, '/').split('/').filter(Boolean) : [];
    // On Windows, first segment is drive letter like "C:"
    const breadcrumbs = pathSegments.map((seg, i) => ({
        label: seg,
        path: pathSegments.slice(0, i + 1).join('/') + (i === 0 && seg.includes(':') ? '/' : ''),
    }));

    // Items to display: search results or browse entries
    const displayItems = searchResults
        ? searchResults.map((r: any) => ({
            name: r.name,
            path: r.path,
            type: 'file' as const,
            size: r.size,
            extension: r.extension || '',
            modified: r.modified || '',
        }))
        : entries;

    return (
        <div className="modal-overlay" onClick={() => setShowFileBrowser(false)} role="dialog" aria-modal="true" aria-label="File Browser">
            <div className={`modal-panel file-browser-modal ${previewFile ? 'has-preview' : ''}`} onClick={(e) => e.stopPropagation()}>
                {/* Header */}
                <div className="modal-header">
                    <h3><FolderOpen size={18} /> File Browser</h3>
                    <button className="btn-icon" onClick={() => setShowFileBrowser(false)} aria-label="Close">
                        <X size={18} />
                    </button>
                </div>

                <div className="file-browser-body">
                    {/* Quick Links */}
                    <div className="fb-quick-links">
                        {quickLinks.map((link) => (
                            <button
                                key={link.path}
                                className="fb-quick-link"
                                onClick={() => loadDirectory(link.path)}
                                title={link.path}
                            >
                                {getQuickLinkIcon(link.icon)}
                                <span>{link.name}</span>
                            </button>
                        ))}
                    </div>

                    {/* Search Bar */}
                    <div className="fb-search-bar">
                        <div className="fb-search-input-wrap">
                            <Search size={14} className="fb-search-icon" />
                            <input
                                type="text"
                                className="fb-search-input"
                                placeholder="Search files on your PC..."
                                value={searchQuery}
                                onChange={(e) => setSearchQuery(e.target.value)}
                                onKeyDown={handleSearchKeyDown}
                                aria-label="Search files"
                            />
                            {searchQuery && (
                                <button
                                    className="fb-search-clear"
                                    onClick={() => { setSearchQuery(''); setSearchResults(null); }}
                                    aria-label="Clear search"
                                >
                                    <X size={12} />
                                </button>
                            )}
                        </div>
                        <select
                            className="fb-type-filter"
                            value={typeFilter}
                            onChange={(e) => setTypeFilter(e.target.value)}
                            aria-label="File type filter"
                        >
                            {FILE_TYPE_FILTERS.map(f => (
                                <option key={f.value} value={f.value}>{f.label}</option>
                            ))}
                        </select>
                        <button
                            className="fb-search-btn"
                            onClick={handleSearch}
                            disabled={!searchQuery.trim() || isSearching}
                        >
                            {isSearching ? 'Searching...' : 'Search'}
                        </button>
                    </div>

                    {/* Breadcrumb */}
                    {!searchResults && (
                        <div className="fb-breadcrumb">
                            <button className="fb-crumb" onClick={() => loadDirectory('/')}>
                                <HardDrive size={12} />
                            </button>
                            {breadcrumbs.map((crumb, i) => (
                                <span key={i} className="fb-crumb-item">
                                    <ChevronRight size={12} className="fb-crumb-sep" />
                                    <button className="fb-crumb" onClick={() => loadDirectory(crumb.path)}>
                                        {crumb.label}
                                    </button>
                                </span>
                            ))}
                            {parentPath && (
                                <button
                                    className="fb-up-btn"
                                    onClick={() => loadDirectory(parentPath)}
                                    title="Go up"
                                >
                                    <ArrowUp size={14} />
                                </button>
                            )}
                        </div>
                    )}

                    {/* Search results header */}
                    {searchResults && (
                        <div className="fb-search-header">
                            <span>Found {searchResults.length} result(s) for "{searchQuery}"</span>
                            <button className="fb-back-btn" onClick={() => { setSearchResults(null); setSearchQuery(''); }}>
                                Back to browsing
                            </button>
                        </div>
                    )}

                    {/* Error */}
                    {error && <div className="fb-error">{error}</div>}

                    {/* File list + Preview split */}
                    <div className="fb-content">
                        <div className="fb-file-list">
                            {loading && <div className="fb-loading">Loading...</div>}

                            {!loading && displayItems.length === 0 && (
                                <div className="fb-empty">
                                    {searchResults ? 'No files found' : 'This folder is empty'}
                                </div>
                            )}

                            {!loading && displayItems.map((entry) => {
                                const ext = entry.extension || (entry.name.includes('.') ? '.' + entry.name.split('.').pop()?.toLowerCase() : '');
                                const fileUnsupported = entry.type === 'file' && ext && !isExtensionSupported(ext);
                                const unsupportedCat = fileUnsupported ? getUnsupportedCategory(ext) : null;

                                return (
                                <div
                                    key={entry.path}
                                    className={`fb-entry ${entry.type} ${selectedFiles.has(entry.path) ? 'selected' : ''} ${fileUnsupported ? 'unsupported' : ''}`}
                                    onClick={() => entry.type === 'folder' ? handleEntryClick(entry) : handleFilePreview(entry.path)}
                                    onDoubleClick={() => entry.type === 'folder' ? handleEntryClick(entry) : undefined}
                                    title={fileUnsupported ? `${unsupportedCat?.label || 'This'} file type cannot be indexed` : entry.path}
                                >
                                    <input
                                        type="checkbox"
                                        className="fb-entry-checkbox"
                                        checked={selectedFiles.has(entry.path)}
                                        onChange={() => {
                                            setSelectedFiles(prev => {
                                                const next = new Set(prev);
                                                if (next.has(entry.path)) next.delete(entry.path);
                                                else next.add(entry.path);
                                                return next;
                                            });
                                        }}
                                        onClick={(e) => e.stopPropagation()}
                                        aria-label={`Select ${entry.name}`}
                                    />
                                    <span className="fb-entry-icon">{getFileIcon(entry)}</span>
                                    <span className="fb-entry-name" title={entry.path}>{entry.name}</span>
                                    {fileUnsupported && (
                                        <span className="fb-unsupported-badge">
                                            Not indexable
                                        </span>
                                    )}
                                    <span className="fb-entry-size">{entry.type === 'file' ? formatSize(entry.size) : ''}</span>
                                    <span className="fb-entry-date">{formatDate(entry.modified)}</span>
                                </div>
                                );
                            })}
                        </div>

                        {/* Preview Panel */}
                        {previewFile && (
                            <div className="fb-preview">
                                <div className="fb-preview-header">
                                    <span className="fb-preview-name">{previewFile.name}</span>
                                    <button className="btn-icon-sm" onClick={() => setPreviewFile(null)} aria-label="Close preview">
                                        <X size={14} />
                                    </button>
                                </div>
                                <div className="fb-preview-meta">
                                    <span>{previewFile.size_display}</span>
                                    {previewFile.total_lines && <span>{previewFile.total_lines} lines</span>}
                                    {previewFile.columns && <span>{previewFile.columns.length} columns</span>}
                                    {previewFile.row_count !== null && <span>{previewFile.row_count} rows</span>}
                                </div>
                                {previewFile.columns && (
                                    <div className="fb-preview-columns">
                                        <strong>Columns:</strong> {previewFile.columns.join(', ')}
                                    </div>
                                )}
                                {previewLoading ? (
                                    <div className="fb-preview-loading">Loading preview...</div>
                                ) : previewFile.is_text && previewFile.preview_lines.length > 0 ? (
                                    <pre className="fb-preview-content">
                                        {previewFile.preview_lines.slice(0, 30).join('\n')}
                                    </pre>
                                ) : (
                                    <div className="fb-preview-binary">Binary file - no preview available</div>
                                )}
                            </div>
                        )}
                    </div>

                    {/* Index error toast */}
                    {indexError && (
                        <UploadErrorToast
                            filename={indexError.filename}
                            error={indexError.error}
                            onDismiss={() => setIndexError(null)}
                        />
                    )}

                    {/* Action Bar */}
                    <div className="fb-actions">
                        <div className="fb-selection-info">
                            {selectedFiles.size > 0
                                ? `${selectedFiles.size} item(s) selected`
                                : 'Select files to index or analyze'}
                        </div>
                        {indexStatus && <span className="fb-index-status">{indexStatus}</span>}
                        <div className="fb-action-btns">
                            <button
                                className="fb-action-btn secondary"
                                onClick={handleIndexSelected}
                                disabled={selectedFiles.size === 0 || indexingFiles.size > 0}
                                title="Index selected files for RAG search"
                            >
                                <Upload size={14} />
                                Index Selected
                            </button>
                            <button
                                className="fb-action-btn primary"
                                onClick={handleAskAgent}
                                disabled={selectedFiles.size === 0 || indexingFiles.size > 0}
                                title="Index and send selected files to the chat agent for analysis"
                            >
                                <Brain size={14} />
                                Ask Agent
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
