// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState, useCallback, useRef, useEffect } from 'react';
import { Plus, Search, FileText, Settings, Sun, Moon, Trash2, PanelLeftClose, PanelLeftOpen, Smartphone, FolderSearch } from 'lucide-react';
import { useChatStore } from '../stores/chatStore';
import * as api from '../services/api';
import { log } from '../utils/logger';
import gaiaRobot from '../assets/gaia-robot.png';
import './Sidebar.css';

interface SidebarProps {
    onNewChat: () => void;
    tunnelActive?: boolean;
    tunnelLoading?: boolean;
    onMobileToggle?: () => void;
}

export function Sidebar({ onNewChat, tunnelActive, tunnelLoading, onMobileToggle }: SidebarProps) {
    const {
        sessions, currentSessionId, setCurrentSession, removeSession,
        setMessages, theme, toggleTheme, setShowDocLibrary, setShowFileBrowser, setShowSettings,
        sidebarOpen, setSidebarOpen, setLoadingMessages,
        sidebarCollapsed, toggleSidebarCollapsed,
        sidebarWidth, setSidebarWidth,
    } = useChatStore();

    const [search, setSearch] = useState('');
    const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
    const [isResizing, setIsResizing] = useState(false);
    const sidebarRef = useRef<HTMLElement>(null);

    const filtered = search
        ? sessions.filter((s) => s.title.toLowerCase().includes(search.toLowerCase()))
        : sessions;

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
        const title = sessions.find((s) => s.id === id)?.title || '?';
        // If already pending confirm for this id, execute the delete
        if (pendingDeleteId === id) {
            log.chat.info(`Deleting session: "${title}" (${id})`);
            // Remove from UI immediately (optimistic)
            removeSession(id);
            setPendingDeleteId(null);
            // Best-effort backend delete
            api.deleteSession(id)
                .then(() => log.chat.info(`Session deleted from backend: "${title}"`))
                .catch((err) => log.chat.warn(`Backend delete failed for "${title}" (may not be running)`, err));
            return;
        }
        // First click: request confirmation
        log.chat.debug(`Delete pending confirmation for: "${title}" (${id})`);
        setPendingDeleteId(id);
        // Auto-cancel after 3s
        setTimeout(() => setPendingDeleteId((prev) => (prev === id ? null : prev)), 3000);
    }, [pendingDeleteId, sessions, removeSession]);

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

    // Drag-to-resize handler
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

        const onMouseUp = () => {
            setIsResizing(false);
            document.removeEventListener('mousemove', onMouseMove);
            document.removeEventListener('mouseup', onMouseUp);
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
            log.ui.debug('Sidebar resize ended');
        };

        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('mouseup', onMouseUp);
    }, [sidebarWidth, setSidebarWidth]);

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
            aria-label="Chat sidebar"
        >
            <div className="sidebar-top">
                <div className="sidebar-brand">
                    <div className="brand-icon" aria-hidden="true">
                        <img src={gaiaRobot} alt="" width={28} height={28} />
                    </div>
                    <div className="brand-text">
                        <span className="brand-name">GAIA</span>
                        <span className="brand-label">Chat</span>
                    </div>
                </div>
                <div className="sidebar-top-actions">
                    <button className="new-chat-btn" onClick={onNewChat} title="New Chat" aria-label="New Chat">
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
                    type="text"
                    placeholder="Search..."
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    aria-label="Search conversations"
                />
            </div>

            <nav className="session-list" aria-label="Chat sessions">
                {filtered.length === 0 && (
                    <div className="empty-hint">
                        {search ? 'No results' : 'No conversations yet'}
                    </div>
                )}
                {filtered.map((s) => (
                    <div
                        key={s.id}
                        className={`session-item ${s.id === currentSessionId ? 'active' : ''}`}
                        onClick={() => handleSelect(s.id)}
                        onKeyDown={(e) => handleSessionKeyDown(e, s.id)}
                        role="button"
                        tabIndex={0}
                        aria-label={`Open chat: ${s.title}`}
                        aria-current={s.id === currentSessionId ? 'true' : undefined}
                    >
                        <span className="session-title">{s.title}</span>
                        <span className="session-time">{formatTime(s.updated_at)}</span>
                        {pendingDeleteId === s.id ? (
                            <button
                                className="session-delete confirm"
                                onClick={(e) => handleDelete(e, s.id)}
                                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') handleDelete(e, s.id); }}
                                title="Click to confirm delete"
                                aria-label={`Confirm delete: ${s.title}`}
                            >
                                <Trash2 size={12} />
                                <span className="confirm-label">Delete?</span>
                            </button>
                        ) : (
                            <button
                                className="session-delete"
                                onClick={(e) => handleDelete(e, s.id)}
                                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') handleDelete(e, s.id); }}
                                title="Delete"
                                aria-label={`Delete: ${s.title}`}
                            >
                                <Trash2 size={13} />
                            </button>
                        )}
                    </div>
                ))}
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
                    <button className="btn-icon" onClick={() => setShowDocLibrary(true)} title="Documents" aria-label="Document Library">
                        <FileText size={17} />
                    </button>
                    <button className="btn-icon" onClick={() => setShowFileBrowser(true)} title="Browse Files" aria-label="Browse Files">
                        <FolderSearch size={17} />
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
        </aside>
    );
}
