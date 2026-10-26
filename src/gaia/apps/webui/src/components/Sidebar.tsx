// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
    Bell, Brain, Check, Clock, EyeOff, FileText, MoreHorizontal, PanelLeftClose, PanelLeftOpen,
    Pencil, Pin, PinOff, Search, Settings, Smartphone, SquarePen, Trash2, X,
} from 'lucide-react';
import { useShallow } from 'zustand/react/shallow';
import { useChatStore } from '../stores/chatStore';
import { ModelStatusPill } from './ModelStatusPill';
import { useNotificationStore, selectUnreadCount, selectPromptingSessionIds } from '../stores/notificationStore';
import * as api from '../services/api';
import { log } from '../utils/logger';
import {
    groupSessionsForList, hiddenAutomatedCount, sessionRowStatus, type LiveRuns, type RowStatus,
} from '../utils/sessionGrouping';
import { ordinal } from '../utils/chatAlerts';
import { formatShortcut } from '../utils/platform';
import { cleanupAbandonedDraft } from '../utils/sessionCleanup';
import { MAX_SESSION_TITLE_CHARS } from '../utils/constants';
import { CLAIMS_ESCAPE } from '../hooks/useEscapeToClose';
import { useMediaQuery } from '../hooks/useMediaQuery';
import { Popover } from './Popover';
import gaiaRobot from '../assets/gaia-robot.png';
import { useSidebarWidth } from './SidebarResizer';
import type { Session } from '../types';
import './Sidebar.css';

interface SidebarProps {
    onNewChat: () => void;
    onMobileAccess?: () => void;
    tunnelActive?: boolean;
}

const STATUS_LABEL: Record<RowStatus, string> = {
    running: 'Running',
    queued: 'Waiting for the GPU',
    waiting: 'Waiting for you',
    done: 'Done',
    failed: 'Failed',
};

const SHOW_AUTOMATED_KEY = 'gaia-show-automated-runs';

function readShowAutomated(): boolean {
    try {
        return localStorage.getItem(SHOW_AUTOMATED_KEY) === '1';
    } catch (err) {
        log.system.warn('Chat-list filter unreadable (site data blocked); hiding eval and scheduled runs.', err);
        return false;
    }
}

function SessionRow({ session, active, phone, moved, status, queuePlace, unsent, failed, onSelect, onDelete, onPin, onRename }: {
    session: Session;
    active: boolean;
    /** Just pinned or unpinned from the phone menu: the row jumped groups, so scroll to it and mark it. */
    moved: boolean;
    /** Phone width: the row's actions sit behind one ⋯ menu instead of three icons. */
    phone: boolean;
    status: RowStatus | null;
    /** 1-based place in line for the GPU while queued; 0 otherwise. */
    queuePlace: number;
    /** Messages typed into this chat that have not gone out yet, and how many of them failed. */
    unsent: number;
    failed: number;
    onSelect: (id: string) => void;
    onDelete: (id: string) => void;
    onPin: (id: string, pinned: boolean, follow?: boolean) => void;
    onRename: (id: string, title: string) => void;
}) {
    const [confirm, setConfirm] = useState(false);
    const [menuOpen, setMenuOpen] = useState(false);
    const moreRef = useRef<HTMLButtonElement>(null);
    const closeMenu = useCallback(() => { setMenuOpen(false); setConfirm(false); }, []);
    const [draft, setDraft] = useState<string | null>(null);
    const editing = draft !== null;
    const settled = useRef(false);
    const startEdit = () => { settled.current = false; setDraft(session.title); };
    const finishEdit = (save: boolean) => {
        // Unmounting the focused input fires blur again; ignore it.
        if (draft === null || settled.current) return;
        settled.current = true;
        setDraft(null);
        if (save) onRename(session.id, draft);
    };
    useEffect(() => {
        if (!confirm) return;
        const t = setTimeout(() => setConfirm(false), 3000);
        return () => clearTimeout(t);
    }, [confirm]);
    const rowRef = useRef<HTMLLIElement>(null);
    useEffect(() => {
        if (moved) rowRef.current?.scrollIntoView?.({ block: 'nearest' });
    }, [moved]);
    // Keeps the field focused, so tapping Save or Cancel isn't preceded by a blur that saves.
    const keepFocus = (e: React.PointerEvent | React.MouseEvent) => e.preventDefault();

    const unread = !!session.unread && !active;
    // Done is news only until read; failed stays until the next turn.
    const dot = status === 'done' && !unread ? null : status;
    const place = queuePlace > 0 ? `${ordinal(queuePlace)} in line` : null;
    const label = dot ? [STATUS_LABEL[dot], place].filter(Boolean).join(', ') : undefined;
    const waiting = failed > 0 ? `${failed} not sent` : unsent > 0 ? `${unsent} to send` : null;
    const note = [place, waiting].filter(Boolean).join(' · ');
    const unsentWords = `${unsent} unsent message${unsent === 1 ? '' : 's'}`;

    return (
        <li
            ref={rowRef}
            className={`sb-row${active ? ' is-active' : ''}${unread ? ' is-unread' : ''}${moved ? ' is-moved' : ''}`}
            data-status={dot ?? undefined}
        >
            {editing ? (
                <input
                    className="sb-row-edit"
                    value={draft}
                    maxLength={MAX_SESSION_TITLE_CHARS}
                    onChange={(e) => setDraft(e.target.value)}
                    onBlur={() => finishEdit(true)}
                    onKeyDown={(e) => {
                        if (e.key === 'Enter') finishEdit(true);
                        if (e.key === 'Escape') { e.preventDefault(); finishEdit(false); }
                    }}
                    {...CLAIMS_ESCAPE}
                    autoFocus
                    onFocus={(e) => e.currentTarget.select()}
                    aria-label={`Rename ${session.title}`}
                />
            ) : (
                <button
                    type="button"
                    className="sb-row-main"
                    onClick={() => onSelect(session.id)}
                    onDoubleClick={startEdit}
                    aria-current={active ? 'page' : undefined}
                    aria-label={[session.title, label, waiting, unread ? 'unread' : null].filter(Boolean).join(', ')}
                    title={session.title}
                >
                    {dot && <span className={`sb-dot is-${dot}`} aria-hidden="true" title={label} />}
                    {session.private && <EyeOff size={12} className="sb-row-icon" aria-label="Private chat" />}
                    <span className="sb-row-title">{session.title}</span>
                    {note && <span className={`sb-row-queue${failed > 0 ? ' is-failed' : ''}`}>{note}</span>}
                </button>
            )}
            {phone && editing && (
                <>
                    <button
                        type="button"
                        className="sb-row-edit-btn is-save"
                        onPointerDown={keepFocus}
                        onMouseDown={keepFocus}
                        onClick={() => finishEdit(true)}
                        aria-label="Save name"
                    >
                        <Check size={18} aria-hidden="true" />
                    </button>
                    <button
                        type="button"
                        className="sb-row-edit-btn"
                        onPointerDown={keepFocus}
                        onMouseDown={keepFocus}
                        onClick={() => finishEdit(false)}
                        aria-label="Cancel rename"
                    >
                        <X size={18} aria-hidden="true" />
                    </button>
                </>
            )}
            {phone ? (!editing && (
                <>
                    <button
                        ref={moreRef}
                        type="button"
                        className="sb-row-more"
                        onClick={() => (menuOpen ? closeMenu() : setMenuOpen(true))}
                        aria-haspopup="dialog"
                        aria-expanded={menuOpen}
                        aria-label={`More actions for ${session.title}`}
                    >
                        <MoreHorizontal size={18} aria-hidden="true" />
                    </button>
                    <Popover open={menuOpen} onClose={closeMenu} anchor={moreRef} label="Chat actions" placement="below" align="end">
                        <div className="sb-row-menu">
                            <button type="button" className="popover-item" onClick={() => { closeMenu(); startEdit(); }}>
                                <Pencil size={15} aria-hidden="true" />
                                Rename
                            </button>
                            <button type="button" className="popover-item" onClick={() => { closeMenu(); onPin(session.id, !session.pinned, true); }}>
                                {session.pinned ? <PinOff size={15} aria-hidden="true" /> : <Pin size={15} aria-hidden="true" />}
                                {session.pinned ? 'Unpin' : 'Pin'}
                            </button>
                            <button
                                type="button"
                                className="popover-item sb-row-menu-delete"
                                onClick={() => (confirm ? onDelete(session.id) : setConfirm(true))}
                            >
                                <Trash2 size={15} aria-hidden="true" />
                                {confirm ? `${unsent > 0 ? `Delete ${unsent} unsent?` : 'Delete?'} Tap again` : 'Delete'}
                            </button>
                        </div>
                    </Popover>
                </>
            )) : (
                <>
                    {!editing && (
                        <button
                            type="button"
                            className="sb-row-rename"
                            onClick={startEdit}
                            aria-label={`Rename ${session.title}`}
                            title="Rename"
                        >
                            <Pencil size={13} />
                        </button>
                    )}
                    <button
                        type="button"
                        className={`sb-row-pin${session.pinned ? ' is-pinned' : ''}`}
                        onClick={() => onPin(session.id, !session.pinned)}
                        aria-label={session.pinned ? `Unpin ${session.title}` : `Pin ${session.title}`}
                        aria-pressed={!!session.pinned}
                        title={session.pinned ? 'Unpin' : 'Pin to top'}
                    >
                        {session.pinned ? <PinOff size={13} /> : <Pin size={13} />}
                    </button>
                    <button
                        type="button"
                        className={`sb-row-delete${confirm ? ' is-confirm' : ''}`}
                        onClick={() => (confirm ? onDelete(session.id) : setConfirm(true))}
                        aria-label={confirm
                            ? `Confirm delete ${session.title}${unsent > 0 ? ` and its ${unsentWords}` : ''}`
                            : `Delete ${session.title}`}
                        title={confirm
                            ? (unsent > 0 ? `Click again to delete this chat and its ${unsentWords}` : 'Click again to delete')
                            : 'Delete'}
                    >
                        {confirm
                            ? <span className="sb-row-delete-label">{unsent > 0 ? `Delete ${unsent} unsent?` : 'Delete?'}</span>
                            : <Trash2 size={13} />}
                    </button>
                </>
            )}
        </li>
    );
}

export function Sidebar({ onNewChat, onMobileAccess, tunnelActive }: SidebarProps) {
    const sessions = useChatStore((s) => s.sessions);
    const currentSessionId = useChatStore((s) => s.currentSessionId);
    const runningSessionIds = useChatStore((s) => s.runningSessionIds);
    const queuedSessionIds = useChatStore((s) => s.queuedSessionIds);
    const awaitingBySession = useChatStore((s) => s.awaitingBySession);
    const messageQueues = useChatStore((s) => s.messageQueues);
    const updateSessionInList = useChatStore((s) => s.updateSessionInList);
    const prompting = useNotificationStore(useShallow(selectPromptingSessionIds));
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
    const unread = useNotificationStore(selectUnreadCount);
    const showNotifications = useNotificationStore((s) => s.showPanel);
    const setShowNotifications = useNotificationStore((s) => s.setShowPanel);

    const [query, setQuery] = useState('');
    const [deleteError, setDeleteError] = useState<string | null>(null);
    const [rowError, setRowError] = useState<string | null>(null);
    const [showAutomated, setShowAutomated] = useState(readShowAutomated);
    const phone = useMediaQuery('(max-width: 768px)');
    const searchRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        const focusSearch = () => {
            if (collapsed) toggleCollapsed();
            requestAnimationFrame(() => searchRef.current?.focus());
        };
        window.addEventListener('gaia:focus-search', focusSearch);
        return () => window.removeEventListener('gaia:focus-search', focusSearch);
    }, [collapsed, toggleCollapsed]);

    const live: LiveRuns = useMemo(() => ({
        running: runningSessionIds,
        queued: queuedSessionIds,
        awaiting: awaitingBySession,
        prompting,
    }), [runningSessionIds, queuedSessionIds, awaitingBySession, prompting]);

    const hiddenCount = hiddenAutomatedCount(sessions);
    const groups = useMemo(() => {
        const q = query.trim().toLowerCase();
        const list = q ? sessions.filter((s) => s.title.toLowerCase().includes(q)) : sessions;
        return groupSessionsForList(list, live, { showAutomated });
    }, [sessions, query, live, showAutomated]);

    const toggleAutomated = useCallback(() => {
        setShowAutomated((on) => {
            try {
                localStorage.setItem(SHOW_AUTOMATED_KEY, on ? '0' : '1');
            } catch (err) {
                log.system.warn('Chat-list filter not saved; it will reset on reload.', err);
            }
            return !on;
        });
    }, []);

    const markRead = useCallback((id: string) => {
        updateSessionInList(id, { unread: false });
        // No rollback: flipping unread back would re-fire this effect in a tight loop.
        api.markSessionRead(id).catch((err) => {
            log.chat.error(`Could not mark chat ${id} read`, err);
            setRowError(`Could not mark the chat read: ${err instanceof Error ? err.message : err}`);
        });
    }, [updateSessionInList]);

    // A reply that lands while you're looking at the chat is already read.
    const currentUnread = sessions.find((s) => s.id === currentSessionId)?.unread;
    useEffect(() => {
        if (!currentSessionId || !currentUnread) return;
        if (document.hasFocus()) {
            markRead(currentSessionId);
            return;
        }
        const onFocus = () => markRead(currentSessionId);
        window.addEventListener('focus', onFocus, { once: true });
        return () => window.removeEventListener('focus', onFocus);
    }, [currentSessionId, currentUnread, markRead]);

    // The row a phone user just pinned or unpinned; it changes groups, so it is followed and marked briefly.
    const [movedId, setMovedId] = useState<string | null>(null);
    useEffect(() => {
        if (!movedId) return;
        const t = setTimeout(() => setMovedId(null), 1200);
        return () => clearTimeout(t);
    }, [movedId]);

    const pin = useCallback(async (id: string, pinned: boolean) => {
        setRowError(null);
        updateSessionInList(id, { pinned });
        try {
            await api.pinSession(id, pinned);
        } catch (err) {
            log.chat.error(`Could not ${pinned ? 'pin' : 'unpin'} chat ${id}`, err);
            updateSessionInList(id, { pinned: !pinned });
            setRowError(err instanceof Error ? err.message : `Could not ${pinned ? 'pin' : 'unpin'} the chat.`);
        }
    }, [updateSessionInList]);

    const rename = useCallback(async (id: string, draft: string) => {
        const title = draft.trim().slice(0, MAX_SESSION_TITLE_CHARS).trim();
        const previous = useChatStore.getState().sessions.find((x) => x.id === id)?.title;
        if (!title || title === previous) return;
        setRowError(null);
        updateSessionInList(id, { title });
        try {
            // Explicit rename: pin against the auto-retitler (#2165).
            await api.updateSession(id, { title, title_is_custom: true });
        } catch (err) {
            log.chat.error(`Could not rename chat ${id}`, err);
            if (previous !== undefined) updateSessionInList(id, { title: previous });
            setRowError(err instanceof Error ? err.message : 'Could not rename the chat.');
        }
    }, [updateSessionInList]);

    const closeOnMobile = useCallback(() => {
        if (window.innerWidth <= 768) setSidebarOpen(false);
    }, [setSidebarOpen]);

    const select = useCallback((id: string) => {
        if (id === currentSessionId) {
            // Still leaves Memory / Scheduled tasks for the open chat.
            setCurrentSession(id);
            closeOnMobile();
            return;
        }
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

    const isMobile = typeof window !== 'undefined' && window.innerWidth <= 768;
    const isCollapsed = collapsed && !isMobile;
    const setSidebarCollapsed = useChatStore((s) => s.setSidebarCollapsed);
    const collapse = useCallback(() => setSidebarCollapsed(true), [setSidebarCollapsed]);
    const pane = useSidebarWidth(collapse);
    const resizable = !isMobile && !isCollapsed;

    const nav = [
        { key: 'memory', label: 'Memory', icon: Brain, on: showMemory, onClick: () => { setShowMemoryDashboard(true); closeOnMobile(); } },
        { key: 'schedules', label: 'Scheduled tasks', icon: Clock, on: showSchedules, onClick: () => { setShowSchedules(true); closeOnMobile(); } },
        { key: 'documents', label: 'Documents', icon: FileText, on: false, onClick: () => { setShowDocLibrary(true); closeOnMobile(); } },
    ];

    return (
        <aside
            className={`sb${sidebarOpen ? ' is-open' : ''}${isCollapsed ? ' is-collapsed' : ''}${pane.resizing ? ' is-resizing' : ''}`}
            style={resizable ? { width: pane.width, minWidth: pane.width } : undefined}
            aria-label="Chats"
        >
            {resizable && pane.handle}
            <div className="sb-top">
                {/* The icon stays when collapsed: with no title bar it is the app's mark. */}
                <div className="sb-brand">
                    <img src={gaiaRobot} alt="" width={22} height={22} />
                    {!isCollapsed && <span>GAIA</span>}
                </div>
                <button
                    type="button"
                    className="sb-icon-btn"
                    onClick={isMobile ? () => setSidebarOpen(false) : toggleCollapsed}
                    aria-label={isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
                    title={`${isCollapsed ? 'Expand' : 'Collapse'} sidebar (${formatShortcut('Mod+B')})`}
                >
                    {isMobile ? <X size={17} /> : isCollapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
                </button>
            </div>

            <div className="sb-actions">
                <button type="button" className="sb-nav-item" onClick={() => { onNewChat(); closeOnMobile(); }} title={`New chat (${formatShortcut('Mod+Shift+O')})`}>
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
                            onKeyDown={(e) => { if (e.key === 'Escape') { e.preventDefault(); setQuery(''); searchRef.current?.blur(); } }}
                            {...CLAIMS_ESCAPE}
                            placeholder="Search chats"
                            aria-label={`Search chats (${formatShortcut('Mod+K')})`}
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
                        <section key={g.label} className={`sb-group${g.label === 'Needs you' ? ' is-needs-you' : ''}`}>
                            <h2 className="sb-group-label">{g.label}</h2>
                            <ul>
                                {g.sessions.map((s) => (
                                    <SessionRow
                                        key={s.id}
                                        session={s}
                                        active={s.id === currentSessionId}
                                        phone={phone}
                                        moved={s.id === movedId}
                                        status={sessionRowStatus(s, live)}
                                        queuePlace={queuedSessionIds.indexOf(s.id) + 1}
                                        unsent={messageQueues[s.id]?.length ?? 0}
                                        failed={messageQueues[s.id]?.filter((m) => m.state === 'failed').length ?? 0}
                                        onSelect={select}
                                        onDelete={remove}
                                        onPin={(id, pinned, follow) => {
                                            if (follow) setMovedId(id);
                                            void pin(id, pinned);
                                        }}
                                        onRename={(id, title) => void rename(id, title)}
                                    />
                                ))}
                            </ul>
                        </section>
                    ))}
                    {hiddenCount > 0 && (
                        <button
                            type="button"
                            className="sb-filter"
                            onClick={toggleAutomated}
                            aria-pressed={showAutomated}
                        >
                            {showAutomated
                                ? 'Hide background runs'
                                : `Show ${hiddenCount} background run${hiddenCount === 1 ? '' : 's'}`}
                        </button>
                    )}
                    {deleteError && <p className="sb-error" role="alert">{deleteError}</p>}
                    {rowError && <p className="sb-error" role="alert">{rowError}</p>}
                </nav>
            )}

            <div className="sb-bottom">
                {!isCollapsed && <ModelStatusPill />}
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
                    <button type="button" className="sb-icon-btn" onClick={() => openSettings()} aria-label="Settings" title={`Settings (${formatShortcut('Mod+,')})`}>
                        <Settings size={16} />
                    </button>
                </div>
            </div>
        </aside>
    );
}
