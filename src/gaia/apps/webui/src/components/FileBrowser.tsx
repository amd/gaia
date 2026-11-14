// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { ensureModel } from '../stores/modelNeededStore';
import { useEffect, useState, useCallback, useRef } from 'react';
import {
    X, Search, Folder, FileText, Home, Download, Monitor, ChevronRight,
    File, FolderOpen, ArrowUp, Brain, Upload, HardDrive,
    Table, Code, Eye
} from 'lucide-react';
import { useChatStore } from '../stores/chatStore';
import { useEscapeToClose } from '../hooks/useEscapeToClose';
import { useFocusTrap } from '../hooks/useFocusTrap';
import { useDialogFocus } from '../hooks/useDialogFocus';
import * as api from '../services/api';
import { log } from '../utils/logger';
import { formatSize } from '../utils/format';
import { isMacPlatform } from '../utils/platform';
import { dateLocale, useT, type Translate } from '../i18n/useT';
import type { MessageKey } from '../i18n/registry';
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

// Format date
function formatDate(iso: string | null, t: Translate): string {
    if (!iso) return '';
    const d = new Date(iso);
    const now = new Date();
    const diff = now.getTime() - d.getTime();
    if (diff < 60000) return t('time.justNow');
    const mins = Math.floor(diff / 60000);
    if (mins < 60) return t('time.minutesAgo', { n: mins });
    const hours = Math.floor(mins / 60);
    if (hours < 24) return t('time.hoursAgo', { n: hours });
    const days = Math.floor(hours / 24);
    if (days < 7) return t('time.daysAgo', { n: days });
    return d.toLocaleDateString(dateLocale(t), { month: 'short', day: 'numeric', year: d.getFullYear() !== now.getFullYear() ? 'numeric' : undefined });
}

function formatSearchScope(locations: string[], t: Translate): string {
    if (locations.length === 0) return '';
    const [first, ...rest] = locations;
    return rest.length > 0 ? t('fileBrowser.scopeMore', { first, n: rest.length }) : first;
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
    { label: 'fileBrowser.filter.all', value: '' },
    { label: 'fileBrowser.filter.documents', value: 'pdf,doc,docx,txt,md' },
    { label: 'fileBrowser.filter.spreadsheets', value: 'csv,xlsx,xls,tsv' },
    { label: 'fileBrowser.filter.code', value: 'py,js,ts,java,c,cpp,go,rs,rb,sh' },
    { label: 'fileBrowser.filter.data', value: 'json,xml,yaml,yml,csv,tsv' },
] as const;

const QUICK_LINK_LABELS: Record<string, MessageKey> = {
    home: 'fileBrowser.home',
    desktop: 'fileBrowser.desktop',
    documents: 'fileBrowser.documents',
    download: 'fileBrowser.downloads',
};

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
    /** Images inside added folders, which GAIA reads only when added one by one. */
    skippedImages: number;
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
    let skippedImages = 0;
    const folderPaths = new Set(entries.filter(e => e.type === 'folder').map(e => e.path));

    for (const filepath of supported) {
        try {
            if (folderPaths.has(filepath)) {
                const result = await api.indexFolder(filepath);
                const newIds: string[] = [];
                result.documents.forEach(d => { if (d.id) newIds.push(d.id); });
                skippedImages += result.skipped_images ?? 0;
                // Each failed file has a library row saying why, to retry from.
                if (result.failed > 0) {
                    failed += result.failed;
                    lastError = result.errors[0] ?? lastError;
                }
                if (newIds.length > 0) {
                    newIds.forEach(id => docIds.push(id));
                    succeededFiles.push(filepath);
                }
            } else {
                if (!(await ensureModel('embedding'))) break;
                const doc = await api.uploadDocumentByPath(filepath);
                if (doc?.indexing_status === 'failed') {
                    // Neither attached nor held for the next chat: GAIA can't search it.
                    failed++;
                    lastError = doc.last_error ?? '';
                } else if (doc?.id) {
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
        const attached: string[] = [];
        for (const docId of docIds) {
            try {
                await api.attachDocument(sessionId, docId);
                attached.push(docId);
            } catch (attachErr) {
                log.doc.warn(`Could not attach document to session: ${attachErr}`);
            }
        }
        const freshSession = useChatStore.getState().sessions.find(s => s.id === sessionId);
        const existing = freshSession?.document_ids ?? [];
        const toAdd = attached.filter(id => !existing.includes(id));
        if (toAdd.length > 0) {
            updateSessionInList(sessionId, { document_ids: [...existing, ...toAdd] });
        }
    } else if (docIds.length > 0) {
        // No chat is open: the agent only sees attached documents, so hold them for the next one.
        useChatStore.getState().addDraftDocuments(docIds);
    }
    // The chat's document bar and the library list only documents the store knows about.
    if (docIds.length > 0 || failed > 0) {
        try {
            const data = await api.listDocuments();
            useChatStore.getState().setDocuments(data.documents || []);
        } catch (err) {
            log.doc.error('Could not refresh the document list after indexing', err);
        }
    }

    return { docIds, supported, succeededFiles, unsupported, failed, lastError, skippedImages };
}

const isHiddenName = (name: string) => name.startsWith('.');

interface FileBrowserProps {
    /** Starts a new chat with this first message; used when no chat is open. */
    onStartChat: (text: string) => Promise<boolean>;
}

export function FileBrowser({ onStartChat }: FileBrowserProps) {
    const t = useT();
    const { setShowFileBrowser, currentSessionId, updateSessionInList, setPendingPrompt } = useChatStore();
    const closeBrowser = useCallback(() => setShowFileBrowser(false), [setShowFileBrowser]);
    useEscapeToClose(true, closeBrowser);
    const dialogRef = useRef<HTMLDivElement>(null);
    useFocusTrap(dialogRef, true);
    useDialogFocus(dialogRef, true);

    // Browse state
    const [currentPath, setCurrentPath] = useState<string>('');
    const [parentPath, setParentPath] = useState<string | null>(null);
    const [entries, setEntries] = useState<FileEntry[]>([]);
    const [quickLinks, setQuickLinks] = useState<QuickLink[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [deniedName, setDeniedName] = useState<string | null>(null);
    const [showHidden, setShowHidden] = useState(false);

    // Search state
    const [searchQuery, setSearchQuery] = useState('');
    const [searchResults, setSearchResults] = useState<any[] | null>(null);
    const [searchedLocations, setSearchedLocations] = useState<string[]>([]);
    const [searchTruncated, setSearchTruncated] = useState(false);
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
        setDeniedName(null);
        setSearchResults(null);
        setSearchQuery('');
        setSearchedLocations([]);
        try {
            const data: BrowseResponse = await api.browseFiles(path);
            setCurrentPath(data.current_path);
            setParentPath(data.parent_path);
            setEntries(data.entries);
            if (data.quick_links.length > 0) setQuickLinks(data.quick_links);
            log.ui.info(`Browsing: ${data.current_path} (${data.entries.length} items)`);
        } catch (err) {
            if (path && (err as { reason?: unknown }).reason === 'os_denied') {
                const name = path.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || path;
                setCurrentPath(path);
                setParentPath(null);
                setEntries([]);
                setDeniedName(name);
            } else {
                setError(err instanceof Error ? err.message : 'Failed to load directory');
            }
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
            setSearchedLocations([]);
            return;
        }
        setIsSearching(true);
        setError(null);
        try {
            const data = await api.searchFiles(searchQuery.trim(), typeFilter || undefined, 30);
            setSearchResults(data.results);
            setSearchedLocations(data.searched_locations ?? []);
            setSearchTruncated(data.truncated === true);
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

    const toggleSelection = useCallback((path: string) => {
        setSelectedFiles(prev => {
            const next = new Set(prev);
            if (next.has(path)) next.delete(path);
            else next.add(path);
            return next;
        });
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
            const dotIdx = firstFile.lastIndexOf('.');
            const ext = dotIdx > 0 ? firstFile.slice(dotIdx).toLowerCase() : '';
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
        setIndexStatus(t.plural('fileBrowser.adding', files.length));

        try {
            const result = await indexAndAttachFiles(files, entries, currentSessionId, updateSessionInList);

            if (result.failed > 0) {
                setIndexStatus(t('fileBrowser.addedWithFailures', { added: result.docIds.length, failed: result.failed }));
                setIndexError({
                    filename: `${result.failed} file(s)`,
                    error: result.lastError || 'Indexing failed for some files',
                });
            } else {
                const added = t.plural('fileBrowser.added', result.docIds.length);
                const parts = [added];
                if (result.unsupported.length > 0) parts.push(t.plural('fileBrowser.skipped', result.unsupported.length));
                if (result.skippedImages > 0) parts.push(t.plural('docs.folder.images', result.skippedImages));
                setIndexStatus(parts.join(' '));
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
    }, [selectedFiles, currentSessionId, updateSessionInList, entries, t]);

    // A second click can land before the re-render that disables the button.
    const askingRef = useRef(false);
    const handleAskAgent = useCallback(async () => {
        if (selectedFiles.size === 0 || askingRef.current) return;
        askingRef.current = true;
        const files = Array.from(selectedFiles);
        setIndexError(null);
        setIndexingFiles(new Set(files));
        setIndexStatus(t.plural('fileBrowser.adding', files.length));

        try {
            const result = await indexAndAttachFiles(files, entries, currentSessionId, updateSessionInList);

            if (result.supported.length === 0) {
                const dotIdx = files[0].lastIndexOf('.');
                const ext = dotIdx > 0 ? files[0].slice(dotIdx).toLowerCase() : '';
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
                setIndexStatus(t.plural('fileBrowser.someFailed', result.failed));
            }

            // Build prompt from files that were actually indexed (not just supported)
            const fileNames = (result.succeededFiles.length > 0 ? result.succeededFiles : result.supported)
                .map(f => f.split(/[\\/]/).pop() || f);
            const prompt = fileNames.length === 1
                ? `Please analyze this document for me: ${fileNames[0]}`
                : `Please analyze these documents for me:\n${fileNames.map(n => `- ${n}`).join('\n')}`;

            if (currentSessionId) {
                setPendingPrompt({ sessionId: currentSessionId, text: prompt });
            } else {
                await onStartChat(prompt);
            }
            setShowFileBrowser(false);
            setSelectedFiles(new Set());
        } catch (err) {
            log.doc.error('Ask Agent failed', err);
            setIndexError({
                filename: files.length === 1 ? files[0].split(/[\\/]/).pop() || files[0] : `${files.length} files`,
                error: err instanceof Error ? err.message : 'An unexpected error occurred.',
            });
        } finally {
            askingRef.current = false;
            setIndexingFiles(new Set());
            setIndexStatus(null);
        }
    }, [selectedFiles, currentSessionId, updateSessionInList, entries, setPendingPrompt, setShowFileBrowser, onStartChat, t]);

    // Build breadcrumb segments from current path
    const pathSegments = currentPath ? currentPath.replace(/\\/g, '/').split('/').filter(Boolean) : [];
    // On Windows, first segment is drive letter like "C:"
    const breadcrumbs = pathSegments.map((seg, i) => ({
        label: seg,
        path: pathSegments.slice(0, i + 1).join('/') + (i === 0 && seg.includes(':') ? '/' : ''),
    }));
    const searchScope = formatSearchScope(searchedLocations, t);

    // Items to display: search results or browse entries
    const visibleEntries = entries.filter(e => !isHiddenName(e.name));
    const displayItems = searchResults
        ? searchResults.map((r: any) => ({
            name: r.name,
            path: r.path,
            type: 'file' as const,
            size: r.size,
            extension: r.extension || '',
            modified: r.modified || '',
        }))
        : showHidden ? entries : visibleEntries;
    const hiddenCount = entries.length - visibleEntries.length;

    // Nothing the user can't see gets indexed: covers Show hidden turned off and leaving a search.
    useEffect(() => {
        if (showHidden || searchResults) return;
        setSelectedFiles(prev => {
            const kept = [...prev].filter(p => !isHiddenName(p.split(/[\\/]/).pop() ?? ''));
            return kept.length === prev.size ? prev : new Set(kept);
        });
    }, [showHidden, searchResults]);

    return (
        <div ref={dialogRef} className="modal-overlay" onClick={() => setShowFileBrowser(false)} role="dialog" aria-modal="true" aria-label={t('fileBrowser.title')}>
            <div className={`modal-panel file-browser-modal ${previewFile ? 'has-preview' : ''}`} onClick={(e) => e.stopPropagation()}>
                {/* Header */}
                <div className="modal-header">
                    <h3><FolderOpen size={18} /> {t('fileBrowser.title')}</h3>
                    <button className="btn-icon" onClick={() => setShowFileBrowser(false)} aria-label={t('fileBrowser.close')}>
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
                                <span>{QUICK_LINK_LABELS[link.icon] ? t(QUICK_LINK_LABELS[link.icon]) : link.name}</span>
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
                                placeholder={t('fileBrowser.searchPlaceholder')}
                                value={searchQuery}
                                onChange={(e) => setSearchQuery(e.target.value)}
                                onKeyDown={handleSearchKeyDown}
                                aria-label={t('fileBrowser.searchAria')}
                            />
                            {searchQuery && (
                                <button
                                    className="fb-search-clear"
                                    onClick={() => { setSearchQuery(''); setSearchResults(null); setSearchedLocations([]); }}
                                    aria-label={t('fileBrowser.clearSearch')}
                                >
                                    <X size={12} />
                                </button>
                            )}
                        </div>
                        <select
                            className="fb-type-filter"
                            value={typeFilter}
                            onChange={(e) => setTypeFilter(e.target.value)}
                            aria-label={t('fileBrowser.typeFilter')}
                        >
                            {FILE_TYPE_FILTERS.map(f => (
                                <option key={f.value} value={f.value}>{t(f.label)}</option>
                            ))}
                        </select>
                        <button
                            className="fb-search-btn"
                            onClick={handleSearch}
                            disabled={!searchQuery.trim() || isSearching}
                        >
                            {isSearching ? t('fileBrowser.searching') : t('fileBrowser.search')}
                        </button>
                    </div>

                    {/* Breadcrumb */}
                    {!searchResults && (
                        <div className="fb-pathbar">
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
                                        title={t('fileBrowser.goUp')}
                                    >
                                        <ArrowUp size={14} />
                                    </button>
                                )}
                            </div>
                            <label className="fb-hidden-toggle">
                                <input
                                    type="checkbox"
                                    checked={showHidden}
                                    onChange={(e) => setShowHidden(e.target.checked)}
                                />
                                {t('fileBrowser.showHidden')}
                            </label>
                        </div>
                    )}

                    {/* Search results header */}
                    {searchResults && (
                        <div className="fb-search-header">
                            <div className="fb-search-summary">
                                <span>{t.plural('fileBrowser.found', searchResults.length, { query: searchQuery })}</span>
                                {searchScope && (
                                    <span
                                        className="fb-search-scope"
                                        title={searchedLocations.join('\n')}
                                    >
                                        {t('fileBrowser.searchingIn')} <code>{searchScope}</code>
                                    </span>
                                )}
                                {searchTruncated && (
                                    <span>
                                        {t('fileBrowser.searchStopped')}
                                    </span>
                                )}
                            </div>
                            <button className="fb-back-btn" onClick={() => { setSearchResults(null); setSearchQuery(''); setSearchedLocations([]); }}>
                                {t('fileBrowser.backToBrowsing')}
                            </button>
                        </div>
                    )}

                    {/* Error */}
                    {(error || deniedName) && (
                        <div className="fb-error">
                            {deniedName ? t(isMacPlatform() ? 'fileBrowser.deniedMac' : 'fileBrowser.denied', { name: deniedName }) : error}
                        </div>
                    )}

                    {/* File list + Preview split */}
                    <div className="fb-content">
                        <div className="fb-file-list">
                            {loading && <div className="fb-loading">{t('fileBrowser.loading')}</div>}

                            {!loading && !error && !deniedName && displayItems.length === 0 && (
                                <div className="fb-empty">
                                    {searchResults
                                        ? t('fileBrowser.noFiles')
                                        : hiddenCount > 0
                                            ? t('fileBrowser.onlyHidden')
                                            : t('fileBrowser.empty')}
                                </div>
                            )}

                            {!loading && displayItems.map((entry) => {
                                const ext = entry.extension || (entry.name.includes('.') ? '.' + entry.name.split('.').pop()?.toLowerCase() : '');
                                const fileUnsupported = entry.type === 'file' && ext && !isExtensionSupported(ext);

                                return (
                                <div
                                    key={entry.path}
                                    className={`fb-entry ${entry.type} ${selectedFiles.has(entry.path) ? 'selected' : ''} ${fileUnsupported ? 'unsupported' : ''}`}
                                    onClick={() => entry.type === 'folder' ? handleEntryClick(entry) : toggleSelection(entry.path)}
                                    onDoubleClick={() => entry.type === 'folder' ? handleEntryClick(entry) : undefined}
                                    title={fileUnsupported ? t('fileBrowser.notIndexableTitle') : entry.path}
                                >
                                    <input
                                        type="checkbox"
                                        className="fb-entry-checkbox"
                                        checked={selectedFiles.has(entry.path)}
                                        onChange={() => toggleSelection(entry.path)}
                                        onClick={(e) => e.stopPropagation()}
                                        aria-label={t('fileBrowser.selectAria', { name: entry.name })}
                                    />
                                    <span className="fb-entry-icon">{getFileIcon(entry)}</span>
                                    <span className="fb-entry-name" title={entry.path}>{entry.name}</span>
                                    {fileUnsupported && (
                                        <span className="fb-unsupported-badge">
                                            {t('fileBrowser.notIndexable')}
                                        </span>
                                    )}
                                    {entry.type === 'file' && (
                                        <button
                                            type="button"
                                            className="fb-entry-preview-btn"
                                            onClick={(e) => { e.stopPropagation(); handleFilePreview(entry.path); }}
                                            aria-label={t('fileBrowser.previewAria', { name: entry.name })}
                                            title={t('fileBrowser.previewTitle')}
                                        >
                                            <Eye size={14} />
                                        </button>
                                    )}
                                    <span className="fb-entry-size">{entry.type === 'file' ? formatSize(entry.size) : ''}</span>
                                    <span className="fb-entry-date">{formatDate(entry.modified, t)}</span>
                                </div>
                                );
                            })}
                        </div>

                        {/* Preview Panel */}
                        {previewFile && (
                            <div className="fb-preview">
                                <div className="fb-preview-header">
                                    <span className="fb-preview-name">{previewFile.name}</span>
                                    <button className="btn-icon-sm" onClick={() => setPreviewFile(null)} aria-label={t('fileBrowser.closePreview')}>
                                        <X size={14} />
                                    </button>
                                </div>
                                <div className="fb-preview-meta">
                                    <span>{previewFile.size_display}</span>
                                    {previewFile.total_lines && <span>{t('fileBrowser.lines', { n: previewFile.total_lines })}</span>}
                                    {previewFile.columns && <span>{t('fileBrowser.columnCount', { n: previewFile.columns.length })}</span>}
                                    {previewFile.row_count !== null && <span>{t('fileBrowser.rows', { n: previewFile.row_count })}</span>}
                                </div>
                                {previewFile.columns && (
                                    <div className="fb-preview-columns">
                                        <strong>{t('fileBrowser.columnsLabel')}</strong> {previewFile.columns.join(', ')}
                                    </div>
                                )}
                                {previewLoading ? (
                                    <div className="fb-preview-loading">{t('fileBrowser.previewLoading')}</div>
                                ) : previewFile.is_text && previewFile.preview_lines.length > 0 ? (
                                    <pre className="fb-preview-content">
                                        {previewFile.preview_lines.slice(0, 30).join('\n')}
                                    </pre>
                                ) : (
                                    <div className="fb-preview-binary">{t('fileBrowser.binary')}</div>
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
                                ? t.plural('fileBrowser.selected', selectedFiles.size)
                                : t('fileBrowser.selectHint')}
                        </div>
                        {indexStatus && <span className="fb-index-status">{indexStatus}</span>}
                        <div className="fb-action-btns">
                            <button
                                className="fb-action-btn secondary"
                                onClick={handleIndexSelected}
                                disabled={selectedFiles.size === 0 || indexingFiles.size > 0}
                                title={t('fileBrowser.addToLibraryTitle')}
                            >
                                <Upload size={14} />
                                {t('fileBrowser.addToLibrary')}
                            </button>
                            <button
                                className="fb-action-btn primary"
                                onClick={handleAskAgent}
                                disabled={selectedFiles.size === 0 || indexingFiles.size > 0}
                                title={t('fileBrowser.askGaiaTitle')}
                            >
                                <Brain size={14} />
                                {t('fileBrowser.askGaia')}
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
