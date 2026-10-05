// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
    Bell, Brain, Clock, Cloud, Cpu, EyeOff, FileText, HelpCircle, Loader2, PanelLeftClose, PanelLeftOpen,
    Search, Settings, Smartphone, SquarePen, Trash2, X,
} from 'lucide-react';
import { useChatStore } from '../stores/chatStore';
import { useModelStore, useInferencePlace, shortModelName, UNKNOWN_PLACE_TITLE } from '../stores/modelStore';
import { useNotificationStore, selectUnreadCount } from '../stores/notificationStore';
import * as api from '../services/api';
import { log } from '../utils/logger';
import { groupSessionsByRecency } from '../utils/sessionGrouping';
import { cleanupAbandonedDraft } from '../utils/sessionCleanup';
import gaiaRobot from '../assets/gaia-robot.png';
import type { Session } from '../types';
import './Sidebar.css';

interface SidebarProps {
    onNewChat: () => void;
    onMobileAccess?: () => void;
    tunnelActive?: boolean;
}

function SessionRow({ session, active, running, onSelect, onDelete }: {
    session: Session;
    active: boolean;
    running: boolean;
    onSelect: (id: string) => void;
    onDelete: (id: string) => void;
}) {
    const [confirm, setConfirm] = useState(false);
    useEffect(() => {
        if (!confirm) return;
        const t = setTimeout(() => setConfirm(false), 3000);
        return () => clearTimeout(t);
    }, [confirm]);

    return (
        <li className={`sb-row${active ? ' is-active' : ''}`}>
            <button
                type="button"
                className="sb-row-main"
                onClick={() => onSelect(session.id)}
                aria-current={active ? 'page' : undefined}
                title={session.title}
            >
                {running && <Loader2 size={12} className="spin sb-row-icon" aria-label="Running" />}
                {session.private && <EyeOff size={12} className="sb-row-icon" aria-label="Private chat" />}
                <span className="sb-row-title">{session.title}</span>
            </button>
            <button
                type="button"
                className={`sb-row-delete${confirm ? ' is-confirm' : ''}`}
                onClick={() => (confirm ? onDelete(session.id) : setConfirm(true))}
                aria-label={confirm ? `Confirm delete ${session.title}` : `Delete ${session.title}`}
                title={confirm ? 'Click again to delete' : 'Delete'}
            >
                {confirm ? <span className="sb-row-delete-label">Delete?</span> : <Trash2 size={13} />}
            </button>
        </li>
    );
}

export function Sidebar({ onNewChat, onMobileAccess, tunnelActive }: SidebarProps) {
    const sessions = useChatStore((s) => s.sessions);
    const currentSessionId = useChatStore((s) => s.currentSessionId);
    const runningSessionIds = useChatStore((s) => s.runningSessionIds);
    const sidebarOpen = useChatStore((s) => s.sidebarOpen);
    const collapsed = useChatStore((s) => s.sidebarCollapsed);
    const toggleCollapsed = useChatStore((s) => s.toggleSidebarCollapsed);
    const setSidebarOpen = useChatStore((s) => s.setSidebarOpen);
    const setCurrentSession = useChatStore((s) => s.setCurrentSession);
    const setMessages = useChatStore((s) => s.setMessages);
    const removeSession = useChatStore((s) => s.removeSession);
    const addPendingDelete = useChatStore((s) => s.addPendingDelete);
    const removePendingDelete = useChatStore((s) => s.removePendingDelete);
    const openSettings = useChatStore((s) => s.openSettings);
    const setShowMemoryDashboard = useChatStore((s) => s.setShowMemoryDashboard);
    const setShowSchedules = useChatStore((s) => s.setShowSchedules);
    const setShowDocLibrary = useChatStore((s) => s.setShowDocLibrary);
    const showMemory = useChatStore((s) => s.showMemoryDashboard);
    const showSchedules = useChatStore((s) => s.showSchedules);
    const active = useModelStore((s) => s.active);
    const place = useInferencePlace();
    const unread = useNotificationStore(selectUnreadCount);
    const showNotifications = useNotificationStore((s) => s.showPanel);
    const setShowNotifications = useNotificationStore((s) => s.setShowPanel);

    const [query, setQuery] = useState('');
    const [deleteError, setDeleteError] = useState<string | null>(null);
    const searchRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        const focusSearch = () => {
            if (collapsed) toggleCollapsed();
            requestAnimationFrame(() => searchRef.current?.focus());
        };
        window.addEventListener('gaia:focus-search', focusSearch);
        return () => window.removeEventListener('gaia:focus-search', focusSearch);
    }, [collapsed, toggleCollapsed]);

    const groups = useMemo(() => {
        const q = query.trim().toLowerCase();
        const list = q ? sessions.filter((s) => s.title.toLowerCase().includes(q)) : sessions;
        return groupSessionsByRecency(list);
    }, [sessions, query]);

    const closeOnMobile = useCallback(() => {
        if (window.innerWidth <= 768) setSidebarOpen(false);
    }, [setSidebarOpen]);

    const select = useCallback((id: string) => {
        if (id === currentSessionId) return;
        void cleanupAbandonedDraft(currentSessionId);
        setCurrentSession(id);
        setMessages([]);
        closeOnMobile();
    }, [currentSessionId, setCurrentSession, setMessages, closeOnMobile]);

    const remove = useCallback(async (id: string) => {
        setDeleteError(null);
        addPendingDelete(id);
        try {
            await api.deleteSession(id);
            removeSession(id);
        } catch (err) {
            log.chat.error(`Delete failed for session ${id}`, err);
            setDeleteError(err instanceof Error ? err.message : 'Could not delete the chat.');
        } finally {
            removePendingDelete(id);
        }
    }, [addPendingDelete, removePendingDelete, removeSession]);

    const LocationIcon = place?.remote ? Cloud : place ? Cpu : HelpCircle;
    const isMobile = typeof window !== 'undefined' && window.innerWidth <= 768;
    const isCollapsed = collapsed && !isMobile;

    const nav = [
        { key: 'memory', label: 'Memory', icon: Brain, on: showMemory, onClick: () => { setShowMemoryDashboard(true); closeOnMobile(); } },
        { key: 'schedules', label: 'Scheduled tasks', icon: Clock, on: showSchedules, onClick: () => { setShowSchedules(true); closeOnMobile(); } },
        { key: 'documents', label: 'Documents', icon: FileText, on: false, onClick: () => { setShowDocLibrary(true); closeOnMobile(); } },
    ];

    return (
        <aside
            className={`sb${sidebarOpen ? ' is-open' : ''}${isCollapsed ? ' is-collapsed' : ''}`}
            aria-label="Chats"
        >
            <div className="sb-top">
                {!isCollapsed && (
                    <div className="sb-brand">
                        <img src={gaiaRobot} alt="" width={22} height={22} />
                        <span>GAIA</span>
                    </div>
                )}
                <button
                    type="button"
                    className="sb-icon-btn"
                    onClick={isMobile ? () => setSidebarOpen(false) : toggleCollapsed}
                    aria-label={isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
                    title={isCollapsed ? 'Expand sidebar (Ctrl+B)' : 'Collapse sidebar (Ctrl+B)'}
                >
                    {isMobile ? <X size={17} /> : isCollapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
                </button>
            </div>

            <div className="sb-actions">
                <button type="button" className="sb-nav-item" onClick={() => { onNewChat(); closeOnMobile(); }} title="New chat (Ctrl+Shift+O)">
                    <SquarePen size={16} aria-hidden="true" />
                    {!isCollapsed && <span>New chat</span>}
                </button>
                {isCollapsed ? (
                    <button type="button" className="sb-nav-item" onClick={() => window.dispatchEvent(new CustomEvent('gaia:focus-search'))} aria-label="Search chats">
                        <Search size={16} aria-hidden="true" />
                    </button>
                ) : (
                    <label className="sb-search">
                        <Search size={14} aria-hidden="true" />
                        <input
                            ref={searchRef}
                            type="search"
                            value={query}
                            onChange={(e) => setQuery(e.target.value)}
                            onKeyDown={(e) => { if (e.key === 'Escape') { setQuery(''); searchRef.current?.blur(); } }}
                            placeholder="Search chats"
                            aria-label="Search chats (Ctrl+K)"
                        />
                    </label>
                )}
                {nav.map(({ key, label, icon: Icon, on, onClick }) => (
                    <button
                        key={key}
                        type="button"
                        className={`sb-nav-item${on ? ' is-active' : ''}`}
                        onClick={onClick}
                        aria-current={on ? 'page' : undefined}
                        aria-label={label}
                        title={isCollapsed ? label : undefined}
                    >
                        <Icon size={16} aria-hidden="true" />
                        {!isCollapsed && <span>{label}</span>}
                    </button>
                ))}
            </div>

            {!isCollapsed && (
                <nav className="sb-list" aria-label="Recent chats">
                    {groups.length === 0 && (
                        <p className="sb-empty">{query ? 'No chats match.' : 'No chats yet.'}</p>
                    )}
                    {groups.map((g) => (
                        <section key={g.label} className="sb-group">
                            <h2 className="sb-group-label">{g.label}</h2>
                            <ul>
                                {g.sessions.map((s) => (
                                    <SessionRow
                                        key={s.id}
                                        session={s}
                                        active={s.id === currentSessionId}
                                        running={runningSessionIds.includes(s.id)}
                                        onSelect={select}
                                        onDelete={remove}
                                    />
                                ))}
                            </ul>
                        </section>
                    ))}
                    {deleteError && <p className="sb-error" role="alert">{deleteError}</p>}
                </nav>
            )}

            <div className="sb-bottom">
                {!isCollapsed && (
                    <button
                        type="button"
                        className="sb-location"
                        onClick={() => openSettings('model')}
                        title={!place
                            ? UNKNOWN_PLACE_TITLE
                            : place.description
                                ?? (place.remote ? `Chat history is sent to ${place.label}` : 'Runs on this PC')}
                    >
                        <LocationIcon size={13} aria-hidden="true" />
                        <span className="sb-location-text">
                            {[place?.label, active && shortModelName(active.model)].filter(Boolean).join(' · ') || 'Model'}
                        </span>
                    </button>
                )}
                <div className="sb-bottom-actions">
                    <button
                        type="button"
                        className={`sb-icon-btn${showNotifications ? ' is-active' : ''}`}
                        onClick={() => setShowNotifications(!showNotifications)}
                        aria-label={unread > 0 ? `Notifications, ${unread} unread` : 'Notifications'}
                        aria-expanded={showNotifications}
                        title="Notifications"
                    >
                        <Bell size={16} />
                        {unread > 0 && <span className="sb-badge" aria-hidden="true">{unread > 99 ? '99+' : unread}</span>}
                    </button>
                    {onMobileAccess && (
                        <button
                            type="button"
                            className={`sb-icon-btn${tunnelActive ? ' is-active' : ''}`}
                            onClick={onMobileAccess}
                            aria-label={tunnelActive ? 'Mobile access is on' : 'Mobile access'}
                            title="Mobile access"
                        >
                            <Smartphone size={16} />
                        </button>
                    )}
                    <button type="button" className="sb-icon-btn" onClick={() => openSettings()} aria-label="Settings" title="Settings (Ctrl+,)">
                        <Settings size={16} />
                    </button>
                </div>
            </div>
        </aside>
    );
}
