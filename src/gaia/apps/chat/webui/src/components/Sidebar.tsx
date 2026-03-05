// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState, useCallback, useRef, useEffect } from 'react';
import { Plus, Search, FileText, Settings, Sun, Moon, Trash2 } from 'lucide-react';
import { useChatStore } from '../stores/chatStore';
import * as api from '../services/api';
import './Sidebar.css';

interface SidebarProps {
    onNewChat: () => void;
}

export function Sidebar({ onNewChat }: SidebarProps) {
    const {
        sessions, currentSessionId, setCurrentSession, removeSession,
        setMessages, theme, toggleTheme, setShowDocLibrary, setShowSettings,
        sidebarOpen, setSidebarOpen, setLoadingMessages,
    } = useChatStore();

    const [search, setSearch] = useState('');
    const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);

    const filtered = search
        ? sessions.filter((s) => s.title.toLowerCase().includes(search.toLowerCase()))
        : sessions;

    const handleSelect = useCallback(async (id: string) => {
        if (id === currentSessionId) return;
        setCurrentSession(id);
        setLoadingMessages(true);
        // Auto-close sidebar on mobile
        if (window.innerWidth <= 768) setSidebarOpen(false);
        try {
            const data = await api.getMessages(id);
            setMessages(data.messages || []);
        } catch (err) {
            console.error('Failed to load messages:', err);
            setMessages([]);
        } finally {
            setLoadingMessages(false);
        }
    }, [currentSessionId, setCurrentSession, setMessages, setSidebarOpen, setLoadingMessages]);

    const handleDelete = useCallback(async (e: React.MouseEvent | React.KeyboardEvent, id: string) => {
        e.stopPropagation();
        // If already pending confirm for this id, execute the delete
        if (pendingDeleteId === id) {
            try {
                await api.deleteSession(id);
                removeSession(id);
            } catch (err) {
                console.error('Failed to delete session:', err);
            }
            setPendingDeleteId(null);
            return;
        }
        // First click: request confirmation
        setPendingDeleteId(id);
        // Auto-cancel after 3s
        setTimeout(() => setPendingDeleteId((prev) => (prev === id ? null : prev)), 3000);
    }, [pendingDeleteId, removeSession]);

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

    return (
        <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`} role="complementary" aria-label="Chat sidebar">
            <div className="sidebar-top">
                <div className="sidebar-brand">
                    <span className="brand-icon" role="img" aria-label="lock">&#x1F512;</span>
                    <span className="brand-name">GAIA Chat</span>
                </div>
                <button className="new-chat-btn" onClick={onNewChat} title="New Chat" aria-label="New Chat">
                    <Plus size={18} />
                </button>
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
                        <button
                            className={`session-delete ${pendingDeleteId === s.id ? 'confirm' : ''}`}
                            onClick={(e) => handleDelete(e, s.id)}
                            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') handleDelete(e, s.id); }}
                            title={pendingDeleteId === s.id ? 'Click again to confirm' : 'Delete'}
                            aria-label={pendingDeleteId === s.id ? `Confirm delete: ${s.title}` : `Delete: ${s.title}`}
                        >
                            <Trash2 size={13} />
                        </button>
                    </div>
                ))}
            </nav>

            <div className="sidebar-bottom">
                <div className="privacy-badge">
                    <span className="privacy-dot" aria-hidden="true" />
                    <span>100% Local</span>
                </div>
                <div className="sidebar-actions">
                    <button className="btn-icon" onClick={() => setShowDocLibrary(true)} title="Documents" aria-label="Document Library">
                        <FileText size={17} />
                    </button>
                    <button className="btn-icon" onClick={() => setShowSettings(true)} title="Settings" aria-label="Settings">
                        <Settings size={17} />
                    </button>
                    <button className="btn-icon" onClick={toggleTheme} title="Toggle theme" aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}>
                        {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
                    </button>
                </div>
            </div>
        </aside>
    );
}
