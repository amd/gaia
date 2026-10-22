// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import {
    Bell, Brain, Clock, EyeOff, FileText, PanelLeftClose, PanelLeftOpen,
    Pin, PinOff, Search, Settings, Smartphone, SquarePen, Trash2, X,
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
import { cleanupAbandonedDraft } from '../utils/sessionCleanup';
import { CLAIMS_ESCAPE } from '../hooks/useEscapeToClose';
import gaiaRobot from '../assets/gaia-robot.png';
import { useSidebarWidth } from './SidebarResizer';
import type { Session } from '../types';
import type { SessionSearchResult } from '../types/api.gen';
import { foldText, splitMatch, type MatchParts } from '../utils/chatSearch';
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
const SEARCH_DEBOUNCE_MS = 200;
const NO_HITS = new Map<string, SessionSearchResult>();

function Highlight({ parts }: { parts: MatchParts }) {
    return <>{parts.before}<mark className="sb-hit">{parts.match}</mark>{parts.after}</>;
}

function readShowAutomated(): boolean {
    try {
        return localStorage.getItem(SHOW_AUTOMATED_KEY) === '1';
    } catch (err) {
        log.system.warn('Chat-list filter unreadable (site data blocked); hiding eval and scheduled runs.', err);
        return false;
    }
}

const resultRowId = (id: string) => `sb-result-${id}`;

function SessionRow({ session, active, status, queuePlace, unsent, failed, onSelect, onDelete, onPin, rowId, kbdActive, titleMatch, snippet }: {
    session: Session;
    active: boolean;
    /** DOM id the search box points at while this row is the keyboard pick. */
    rowId?: string;
    kbdActive?: boolean;
    /** Where the search query sits in the title, and in the chat's text. */
    titleMatch?: MatchParts | null;
    snippet?: MatchParts | null;
    status: RowStatus | null;
    /** 1-based place in line for the GPU while queued; 0 otherwise. */
    queuePlace: number;
    /** Messages typed into this chat that have not gone out yet, and how many of them failed. */
    unsent: number;
    failed: number;
    onSelect: (id: string) => void;
    onDelete: (id: string) => void;
    onPin: (id: string, pinned: boolean) => void;
}) {
    const [confirm, setConfirm] = useState(false);
    useEffect(() => {
        if (!confirm) return;
        const t = setTimeout(() => setConfirm(false), 3000);
        return () => clearTimeout(t);
    }, [confirm]);

    const unread = !!session.unread && !active;
    // Done is news only until read; failed stays until the next turn.
    const dot = status === 'done' && !unread ? null : status;
    const place = queuePlace > 0 ? `${ordinal(queuePlace)} in line` : null;
    const label = dot ? [STATUS_LABEL[dot], place].filter(Boolean).join(', ') : undefined;
    const waiting = failed > 0 ? `${failed} not sent` : unsent > 0 ? `${unsent} to send` : null;
    const note = [place, waiting].filter(Boolean).join(' · ');
    const unsentWords = `${unsent} unsent message${unsent === 1 ? '' : 's'}`;
    const snippetText = snippet ? `${snippet.before}${snippet.match}${snippet.after}` : null;
    const title = <span className="sb-row-title">{titleMatch ? <Highlight parts={titleMatch} /> : session.title}</span>;

    return (
        <li className={`sb-row${active ? ' is-active' : ''}${unread ? ' is-unread' : ''}${kbdActive ? ' is-kbd-active' : ''}`} data-status={dot ?? undefined}>
            <button
                type="button"
                id={rowId}
                className="sb-row-main"
                onClick={() => onSelect(session.id)}
                aria-current={active ? 'page' : undefined}
                aria-label={[session.title, label, waiting, unread ? 'unread' : null, snippetText].filter(Boolean).join(', ')}
                title={session.title}
            >
                {dot && <span className={`sb-dot is-${dot}`} aria-hidden="true" title={label} />}
                {session.private && <EyeOff size={12} className="sb-row-icon" aria-label="Private chat" />}
                {snippet ? (
                    <span className="sb-row-text">
                        {title}
                        <span className="sb-row-snippet"><Highlight parts={snippet} /></span>
                    </span>
                ) : title}
                {note && <span className={`sb-row-queue${failed > 0 ? ' is-failed' : ''}`}>{note}</span>}
            </button>
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
    const [hits, setHits] = useState<{ query: string; results: SessionSearchResult[] } | null>(null);
    const [searchError, setSearchError] = useState<string | null>(null);
    const [activeIdx, setActiveIdx] = useState(-1);
    const [deleteError, setDeleteError] = useState<string | null>(null);
    const [rowError, setRowError] = useState<string | null>(null);
    const [showAutomated, setShowAutomated] = useState(readShowAutomated);
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

    // Titles filter on every keystroke; message text comes from the backend.
    const q = query.trim();
    useEffect(() => {
        setActiveIdx(-1);
        setSearchError(null);
        if (!q) {
            setHits(null);
            return;
        }
        const ctrl = new AbortController();
        const timer = setTimeout(() => {
            api.searchSessions(q, ctrl.signal)
                .then((results) => setHits({ query: q, results }))
                .catch((err) => {
                    if (ctrl.signal.aborted) return;
                    log.chat.error('Chat search failed', err);
                    setSearchError(`Could not search inside chats: ${err instanceof Error ? err.message : err}`);
                    setHits({ query: q, results: [] });
                });
        }, SEARCH_DEBOUNCE_MS);
        return () => {
            clearTimeout(timer);
            ctrl.abort();
        };
    }, [q]);
    const pending = !!q && hits?.query !== q;
    const contentHits = useMemo(
        () => (hits && hits.query === q ? new Map(hits.results.map((r) => [r.session_id, r])) : NO_HITS),
        [hits, q],
    );

    const hiddenCount = hiddenAutomatedCount(sessions);
    const groups = useMemo(() => {
        const needle = foldText(q);
        const list = q
            ? sessions.filter((s) => foldText(s.title).includes(needle) || contentHits.has(s.id))
            : sessions;
        return groupSessionsForList(list, live, { showAutomated });
    }, [sessions, q, contentHits, live, showAutomated]);
    const results = useMemo(() => (q ? groups.flatMap((g) => g.sessions) : []), [q, groups]);
    const picked = activeIdx >= 0 && results.length > 0 ? results[Math.min(activeIdx, results.length - 1)] : undefined;

    useEffect(() => {
        if (picked) document.getElementById(resultRowId(picked.id))?.scrollIntoView?.({ block: 'nearest' });
    }, [picked]);

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

    const onSearchKey = (e: KeyboardEvent<HTMLInputElement>) => {
        if (e.key === 'Escape') {
            e.preventDefault();
            setQuery('');
            searchRef.current?.blur();
            return;
        }
        if (!q || results.length === 0 || e.nativeEvent.isComposing) return;
        const at = picked ? results.indexOf(picked) : -1;
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            setActiveIdx(Math.min(at + 1, results.length - 1));
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            setActiveIdx(Math.max(at - 1, 0));
        } else if (e.key === 'Enter') {
            e.preventDefault();
            select((picked ?? results[0]).id);
        }
    };

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
                            onKeyDown={onSearchKey}
                            {...CLAIMS_ESCAPE}
                            placeholder="Search chats"
                            aria-label="Search chats (Ctrl+K)"
                            aria-controls="sb-chat-list"
                            aria-activedescendant={picked ? resultRowId(picked.id) : undefined}
                        />
                    </label>
                )}
                <p className="sb-sr-only" role="status" aria-live="polite">
                    {q && !pending
                        ? results.length === 0
                            ? 'No chats match.'
                            : `${results.length} chat${results.length === 1 ? '' : 's'} match`
                        : ''}
                </p>
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
                <nav className="sb-list" id="sb-chat-list" aria-label="Recent chats">
                    {groups.length === 0 && (
                        <p className="sb-empty">{q ? (pending ? 'Searching inside chats…' : 'No chats match.') : 'No chats yet.'}</p>
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
                                        status={sessionRowStatus(s, live)}
                                        queuePlace={queuedSessionIds.indexOf(s.id) + 1}
                                        unsent={messageQueues[s.id]?.length ?? 0}
                                        failed={messageQueues[s.id]?.filter((m) => m.state === 'failed').length ?? 0}
                                        onSelect={select}
                                        onDelete={remove}
                                        onPin={(id, pinned) => void pin(id, pinned)}
                                        rowId={q ? resultRowId(s.id) : undefined}
                                        kbdActive={picked?.id === s.id}
                                        titleMatch={q ? splitMatch(s.title, q) : null}
                                        snippet={contentHits.get(s.id)?.field === 'message' ? contentHits.get(s.id)!.snippet : null}
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
                    {searchError && <p className="sb-error" role="alert">{searchError}</p>}
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
                    <button type="button" className="sb-icon-btn" onClick={() => openSettings()} aria-label="Settings" title="Settings (Ctrl+,)">
                        <Settings size={16} />
                    </button>
                </div>
            </div>
        </aside>
    );
}
