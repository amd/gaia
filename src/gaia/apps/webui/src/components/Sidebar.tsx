// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import { Plus, Search, Settings, Sun, Moon, Trash2, PanelLeftClose, PanelLeftOpen, Smartphone, Brain } from 'lucide-react';
import { useChatStore } from '../stores/chatStore';
import * as api from '../services/api';
import { log } from '../utils/logger';
import { getSessionHash } from '../utils/format';
import gaiaRobot from '../assets/gaia-robot.png';
import type { Session } from '../types';
import './Sidebar.css';

interface SidebarProps {
    onNewTask: () => void;
    tunnelActive?: boolean;
    tunnelLoading?: boolean;
    onMobileToggle?: () => void;
}

/** Copy a session's hash link to the clipboard. */
function copySessionLink(e: React.MouseEvent, sessionId: string) {
    e.stopPropagation();
    e.preventDefault();
    const hash = getSessionHash(sessionId);
    const url = `${window.location.origin}${window.location.pathname}#${hash}`;
    navigator.clipboard.writeText(url).then(() => {
        log.ui.info(`Copied session link: ${url}`);
    }).catch(() => {
        // Fallback: select the URL in a temporary input
        log.ui.warn('Clipboard write failed');
    });
}

/** Extracted session row to share between grouped and flat rendering. */
function SessionItem({ session: s, isActive, isPendingDelete, isDeleting, onSelect, onKeyDown, onDelete, formatTime }: {
    session: Session;
    isActive: boolean;
    isPendingDelete: boolean;
    isDeleting: boolean;
    onSelect: (id: string) => void;
    onKeyDown: (e: React.KeyboardEvent, id: string) => void;
    onDelete: (e: React.MouseEvent | React.KeyboardEvent, id: string) => void;
    formatTime: (iso: string) => string;
}) {
    const [copied, setCopied] = useState(false);

    const handleCopyHash = useCallback((e: React.MouseEvent) => {
        copySessionLink(e, s.id);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
    }, [s.id]);

    return (
        <div
            className={`session-item ${isActive ? 'active' : ''} ${isDeleting ? 'session-deleting' : ''}`}
            onClick={() => onSelect(s.id)}
            onKeyDown={(e) => onKeyDown(e, s.id)}
            role="button"
            tabIndex={0}
            aria-label={`Open task: ${s.title}`}
            aria-current={isActive ? 'true' : undefined}
        >
            <span className="session-title">{s.title}</span>
            <a
                className={`session-hash ${copied ? 'copied' : ''}`}
                href={`#${getSessionHash(s.id)}`}
                onClick={handleCopyHash}
                title={copied ? 'Copied!' : `Copy link #${getSessionHash(s.id)}`}
                aria-label={`Copy link for session ${getSessionHash(s.id)}`}
            >
                #{getSessionHash(s.id)}
            </a>
            <span className="session-time">{formatTime(s.updated_at)}</span>
            {isPendingDelete ? (
                <button
                    className="session-delete confirm"
                    onClick={(e) => onDelete(e, s.id)}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onDelete(e, s.id); }}
                    title="Click to confirm delete"
                    aria-label={`Confirm delete: ${s.title}`}
                >
                    <Trash2 size={12} />
                    <span className="confirm-label">Delete?</span>
                </button>
            ) : (
                <button
                    className="session-delete"
                    onClick={(e) => onDelete(e, s.id)}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onDelete(e, s.id); }}
                    title="Delete"
                    aria-label={`Delete: ${s.title}`}
                >
                    <Trash2 size={13} />
                </button>
            )}
        </div>
    );
}

export function Sidebar({ onNewTask, tunnelActive, tunnelLoading, onMobileToggle }: SidebarProps) {
    const {
        sessions, currentSessionId, setCurrentSession, removeSession, addSession,
        setMessages, theme, toggleTheme, setShowSettings, setShowMemoryDashboard,
        sidebarOpen, setSidebarOpen, setLoadingMessages,
        sidebarCollapsed, toggleSidebarCollapsed,
        sidebarWidth, setSidebarWidth,
        addPendingDelete, removePendingDelete,
    } = useChatStore();

    const [search, setSearch] = useState('');
    const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
    const [deletingId, setDeletingId] = useState<string | null>(null);
    const [isResizing, setIsResizing] = useState(false);
    // Undo-delete: temporarily preserve the deleted session for restoration
    const [deletedSession, setDeletedSession] = useState<{ session: Session; timer: ReturnType<typeof setTimeout> } | null>(null);
    const sidebarRef = useRef<HTMLElement>(null);
    const searchInputRef = useRef<HTMLInputElement>(null);
    const deleteTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    // Listen for Ctrl+K focus-search event from ChatView
    useEffect(() => {
        const handler = () => {
            if (sidebarCollapsed) {
                toggleSidebarCollapsed();
                // Delay focus until after React re-renders the search input
                requestAnimationFrame(() => searchInputRef.current?.focus());
            } else {
                searchInputRef.current?.focus();
            }
        };
        window.addEventListener('gaia:focus-search', handler);
        return () => window.removeEventListener('gaia:focus-search', handler);
    }, [sidebarCollapsed, toggleSidebarCollapsed]);

    const filtered = search
        ? sessions.filter((s) => s.title.toLowerCase().includes(search.toLowerCase()))
        : sessions;

    // Group sessions by time period (Today, Yesterday, Previous 7 Days, Older)
    const groupedSessions = useMemo(() => {
        if (search) return null; // Don't group when searching
        const now = new Date();
        const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
        const yesterdayStart = todayStart - 86400000;
        const weekStart = todayStart - 7 * 86400000;

        const groups: { label: string; sessions: typeof filtered }[] = [
            { label: 'Today', sessions: [] },
            { label: 'Yesterday', sessions: [] },
            { label: 'Previous 7 Days', sessions: [] },
            { label: 'Older', sessions: [] },
        ];

        for (const s of filtered) {
            const t = new Date(s.updated_at).getTime();
            if (t >= todayStart) groups[0].sessions.push(s);
            else if (t >= yesterdayStart) groups[1].sessions.push(s);
            else if (t >= weekStart) groups[2].sessions.push(s);
            else groups[3].sessions.push(s);
        }

        return groups.filter((g) => g.sessions.length > 0);
    }, [filtered, search]);

    const handleSelect = useCallback(async (id: string) => {
        if (id === currentSessionId) return;
        const title = sessions.find((s) => s.id === id)?.title || '?';
        log.nav.info(`Selecting session: "${title}" (${id})`);
        setCurrentSession(id);
        setMessages([]);
        setLoadingMessages(true);
        // Auto-close sidebar on mobile
        if (window.innerWidth <= 768) setSidebarOpen(false);
        try {
            const data = await api.getMessages(id);
            // Guard: only apply if this session is still the current one
            // (prevents stale data from overwriting when user rapidly switches)
            const stillCurrent = useChatStore.getState().currentSessionId === id;
            if (!stillCurrent) {
                log.nav.debug(`Discarding stale message load for session=${id} (user switched away)`);
                return;
            }
            setMessages(data.messages || []);
            log.nav.info(`Loaded ${(data.messages || []).length} message(s) for "${title}"`);
        } catch (err) {
            log.nav.error(`Failed to load messages for session ${id}`, err);
            setMessages([]);
        } finally {
            setLoadingMessages(false);
        }
    }, [currentSessionId, sessions, setCurrentSession, setMessages, setSidebarOpen, setLoadingMessages]);

    const handleDelete = useCallback(async (e: React.MouseEvent | React.KeyboardEvent, id: string) => {
        e.stopPropagation();
        e.preventDefault();
        const session = sessions.find((s) => s.id === id);
        const title = session?.title || '?';
        // If already pending confirm for this id, execute the delete
        if (pendingDeleteId === id) {
            log.chat.info(`Deleting session: "${title}" (${id})`);
            setPendingDeleteId(null);

            // Start shrink + fade animation, then remove after it completes
            setDeletingId(id);

            // Mark as pending-delete so session polling won't resurrect it
            addPendingDelete(id);

            // Delete from backend immediately (fixes bug where deferred delete
            // was lost on page refresh, causing sessions to reappear)
            api.deleteSession(id)
                .then(() => {
                    log.chat.info(`Session deleted from backend: "${title}"`);
                    removePendingDelete(id);
                })
                .catch((err) => {
                    log.chat.warn(`Backend delete failed for "${title}"`, err);
                    removePendingDelete(id);
                });

            // Show undo toast for 5 seconds — undo will re-create the session
            if (deletedSession?.timer) clearTimeout(deletedSession.timer);
            const timer = setTimeout(() => {
                setDeletedSession(null);
            }, 5000);
            if (session) setDeletedSession({ session, timer });

            // Remove from UI after the animation completes (250ms)
            setTimeout(() => {
                removeSession(id);
                setDeletingId(null);
            }, 250);
            return;
        }
        // First click: request confirmation
        log.chat.debug(`Delete pending confirmation for: "${title}" (${id})`);
        setPendingDeleteId(id);
        // Auto-cancel after 3s
        if (deleteTimerRef.current) clearTimeout(deleteTimerRef.current);
        deleteTimerRef.current = setTimeout(() => setPendingDeleteId((prev) => (prev === id ? null : prev)), 3000);
    }, [pendingDeleteId, sessions, removeSession, deletedSession, addPendingDelete, removePendingDelete]);

    const handleUndoDelete = useCallback(() => {
        if (!deletedSession) return;
        clearTimeout(deletedSession.timer);
        const { session } = deletedSession;
        log.chat.info(`Undo delete: re-creating "${session.title}"`);
        // Remove from pending-delete filter so it can appear again
        removePendingDelete(session.id);
        // Re-create the session on the backend (original was already deleted)
        api.createSession({ title: session.title })
            .then((newSession) => {
                addSession(newSession);
                log.chat.info(`Undo delete: re-created "${newSession.title}" (new id=${newSession.id})`);
            })
            .catch((err) => {
                log.chat.warn(`Undo delete failed for "${session.title}"`, err);
            });
        setDeletedSession(null);
    }, [deletedSession, addSession, removePendingDelete]);

    // Cancel pending delete on outside click
    useEffect(() => {
        if (!pendingDeleteId) return;
        const handler = () => setPendingDeleteId(null);
        window.addEventListener('click', handler, { once: true });
        return () => window.removeEventListener('click', handler);
    }, [pendingDeleteId]);

    const handleSessionKeyDown = useCallback((e: React.KeyboardEvent, id: string) => {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            handleSelect(id);
        }
    }, [handleSelect]);

    // Drag-to-resize handler — store cleanup ref to prevent listener leak on unmount
    const resizeCleanupRef = useRef<(() => void) | null>(null);

    const handleResizeStart = useCallback((e: React.MouseEvent) => {
        e.preventDefault();
        setIsResizing(true);
        log.ui.debug('Sidebar resize started');

        const startX = e.clientX;
        const startWidth = sidebarWidth;

        const onMouseMove = (ev: MouseEvent) => {
            const delta = ev.clientX - startX;
            setSidebarWidth(startWidth + delta);
        };

        const cleanup = () => {
            setIsResizing(false);
            document.removeEventListener('mousemove', onMouseMove);
            document.removeEventListener('mouseup', cleanup);
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
            resizeCleanupRef.current = null;
            log.ui.debug('Sidebar resize ended');
        };

        resizeCleanupRef.current = cleanup;
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('mouseup', cleanup);
    }, [sidebarWidth, setSidebarWidth]);

    // Clean up resize listeners and delete timer if component unmounts
    useEffect(() => {
        return () => {
            if (resizeCleanupRef.current) resizeCleanupRef.current();
            if (deleteTimerRef.current) clearTimeout(deleteTimerRef.current);
        };
    }, []);

    const formatTime = (iso: string) => {
        const d = new Date(iso);
        const now = new Date();
        const diff = now.getTime() - d.getTime();
        const mins = Math.floor(diff / 60000);
        if (mins < 1) return 'now';
        if (mins < 60) return `${mins}m`;
        const hrs = Math.floor(mins / 60);
        if (hrs < 24) return `${hrs}h`;
        const days = Math.floor(hrs / 24);
        if (days < 7) return `${days}d`;
        return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    };

    // Compute inline width style (only on desktop)
    const isMobile = typeof window !== 'undefined' && window.innerWidth <= 768;
    const sidebarStyle = isMobile ? undefined : {
        width: sidebarCollapsed ? 56 : sidebarWidth,
        minWidth: sidebarCollapsed ? 56 : sidebarWidth,
    };

    return (
        <aside
            ref={sidebarRef}
            className={`sidebar ${sidebarOpen ? 'open' : ''} ${sidebarCollapsed ? 'collapsed' : ''} ${isResizing ? 'resizing' : ''}`}
            style={sidebarStyle}
            role="complementary"
            aria-label="Task sidebar"
        >
            <div className="sidebar-top">
                <div className="sidebar-brand">
                    <div className="brand-icon" aria-hidden="true">
                        <img src={gaiaRobot} alt="" width={28} height={28} />
                    </div>
                    <div className="brand-text">
                        <span className="brand-name">GAIA</span>
                        <span className="brand-version">v{__APP_VERSION__}</span>
                        <span className="beta-badge">BETA</span>
                    </div>
                </div>
                <div className="sidebar-top-actions">
                    <button className="new-task-btn" onClick={onNewTask} title="New Task" aria-label="New Task">
                        <Plus size={18} />
                    </button>
                    <button
                        className="collapse-btn"
                        onClick={toggleSidebarCollapsed}
                        title={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
                        aria-label={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
                    >
                        {sidebarCollapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
                    </button>
                </div>
            </div>

            <div className="sidebar-search">
                <Search size={14} className="search-icon" aria-hidden="true" />
                <input
                    ref={searchInputRef}
                    type="text"
                    placeholder="Search... (Ctrl+K)"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    aria-label="Search tasks"
                />
            </div>

            <nav className="session-list" aria-label="Task sessions">
                {filtered.length === 0 && (
                    <div className="empty-hint">
                        {search ? 'No results' : 'No tasks yet'}
                    </div>
                )}
                {groupedSessions ? (
                    /* Render grouped sessions (Today, Yesterday, etc.) */
                    groupedSessions.map((group) => (
                        <div key={group.label}>
                            <div className="session-group-label">{group.label}</div>
                            {group.sessions.map((s) => (
                                <SessionItem
                                    key={s.id}
                                    session={s}
                                    isActive={s.id === currentSessionId}
                                    isPendingDelete={pendingDeleteId === s.id}
                                    isDeleting={deletingId === s.id}
                                    onSelect={handleSelect}
                                    onKeyDown={handleSessionKeyDown}
                                    onDelete={handleDelete}
                                    formatTime={formatTime}
                                />
                            ))}
                        </div>
                    ))
                ) : (
                    /* Flat list when searching */
                    filtered.map((s) => (
                        <SessionItem
                            key={s.id}
                            session={s}
                            isActive={s.id === currentSessionId}
                            isPendingDelete={pendingDeleteId === s.id}
                            isDeleting={deletingId === s.id}
                            onSelect={handleSelect}
                            onKeyDown={handleSessionKeyDown}
                            onDelete={handleDelete}
                            formatTime={formatTime}
                        />
                    ))
                )}
            </nav>

            <div className="sidebar-bottom">
                <div className="privacy-badge">
                    <span className="privacy-dot" aria-hidden="true" />
                    <span>100% Local</span>
                    <span className="version-badge">v{__APP_VERSION__}</span>
                </div>
                <div className="sidebar-actions">
                    {/* Mobile Access Gateway */}
                    {onMobileToggle && (
                        <button
                            className={`btn-icon mobile-toggle-btn ${tunnelActive ? 'active' : ''} ${tunnelLoading ? 'loading' : ''}`}
                            onClick={onMobileToggle}
                            disabled={tunnelLoading}
                            title={tunnelActive ? 'Stop mobile access' : 'Enable mobile access'}
                            aria-label={tunnelActive ? 'Stop mobile access' : 'Enable mobile access'}
                        >
                            <Smartphone size={17} />
                        </button>
                    )}
                    <button className="btn-icon" onClick={() => setShowMemoryDashboard(true)} title="Memory Dashboard" aria-label="Memory Dashboard">
                        <Brain size={17} />
                    </button>
                    <button className="btn-icon" onClick={() => setShowSettings(true)} title="Settings" aria-label="Settings">
                        <Settings size={17} />
                    </button>
                    <button className="btn-icon" onClick={toggleTheme} title="Toggle theme" aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}>
                        {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
                    </button>
                </div>
            </div>

            {/* Drag-to-resize handle */}
            {!sidebarCollapsed && (
                <div
                    className="sidebar-resize-handle"
                    onMouseDown={handleResizeStart}
                    title="Drag to resize sidebar"
                    aria-hidden="true"
                />
            )}

            {/* Undo-delete toast */}
            {deletedSession && (
                <div className="toast toast-undo" role="status">
                    Deleted &ldquo;{deletedSession.session.title}&rdquo;
                    <span className="toast-action" onClick={handleUndoDelete}>Undo</span>
                </div>
            )}
        </aside>
    );
}
