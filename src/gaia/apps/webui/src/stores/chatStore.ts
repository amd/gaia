// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Zustand store for GAIA Agent UI state. */

import { create } from 'zustand';
import type { ActiveRuns, AwaitingUser, Session, Message, Document, AgentStep, SystemStatus, AgentInfo, RenderCardData, PermissionMode } from '../types';
import { applyTheme, resolveTheme, type ThemePreference } from '../utils/theme';
import { log } from '../utils/logger';

/** Settings sections, in the order the settings nav shows them. */
export type SettingsSection =
    | 'general'
    | 'model'
    | 'permissions'
    | 'skills'
    | 'connectors'
    | 'memory'
    | 'privacy'
    | 'notifications'
    | 'advanced';

/**
 * Read a UI preference, saying so when the store is unreadable.
 *
 * Storage throws in private-browsing and sandboxed-iframe contexts; the
 * preference is cosmetic, so the default stands in rather than failing the
 * whole store's construction -- but never without a trace.
 */
function sameIds(a: readonly string[], b: readonly string[]): boolean {
    return a.length === b.length && a.every((id) => b.includes(id));
}

function readPref(key: string, fallback: string): string {
    if (typeof window === 'undefined') return fallback;
    try {
        return localStorage.getItem(key) || fallback;
    } catch (err) {
        log.system.warn(
            `Preference "${key}" unreadable, using "${fallback}". ` +
                'localStorage is blocked -- check private-browsing or site-data settings.',
            err,
        );
        return fallback;
    }
}

/** The stored appearance; nothing saved, or anything unrecognised, follows the OS. */
function readThemePreference(): ThemePreference {
    const v = readPref('gaia-chat-theme', 'system');
    return v === 'light' || v === 'dark' ? v : 'system';
}

/** Persist a UI preference. Never throws: a zustand setter must still update state. */
function writePref(key: string, value: string): void {
    try {
        localStorage.setItem(key, value);
    } catch (err) {
        log.system.warn(
            `Preference "${key}" not saved -- it will reset on reload. ` +
                'localStorage is blocked -- check private-browsing or site-data settings.',
            err,
        );
    }
}

/** A question the agent is blocked on; `expiresAt` is when the backend stops waiting (ms), or null. */
export interface LiveQuestion {
    sessionId: string;
    expiresAt: number | null;
}

interface ChatState {
    /** Registered agents; Settings → Connectors grants scopes to them. */
    agents: AgentInfo[];
    setAgents: (agents: AgentInfo[]) => void;

    // Device selection (CPU / GPU / NPU)
    activeDevice: string;
    setActiveDevice: (device: string) => void;
    detectedDevices: string[];
    setDetectedDevices: (devices: string[]) => void;

    // Sessions
    sessions: Session[];
    currentSessionId: string | null;
    /** IDs of sessions with a pending backend delete — filtered from poll results. */
    pendingDeleteIds: string[];
    setSessions: (sessions: Session[]) => void;
    setCurrentSession: (id: string | null) => void;
    addSession: (session: Session) => void;
    removeSession: (id: string) => void;
    updateSessionInList: (id: string, updates: Partial<Session>) => void;
    addPendingDelete: (id: string) => void;
    removePendingDelete: (id: string) => void;
    /** Session IDs with a chat turn still running server-side (#1580).
     *  Backend-truth (polled from /api/chat/active), so it survives
     *  navigating away, refresh, and revisit — drives the sidebar's
     *  "still running" spinner on backgrounded sessions. */
    runningSessionIds: string[];
    setRunningSessions: (ids: string[]) => void;
    /** Chats waiting in line for the model, first to run next first (#5226). */
    queuedSessionIds: string[];
    /** Chats whose running turn is paused on a permission prompt or a question (#5221). */
    awaitingBySession: Record<string, AwaitingUser>;
    /** When the last applied GET /api/chat/active was sent (ms); 0 before the first. */
    activeRunsAt: number;
    /** Apply one GET /api/chat/active poll, sent at `requestedAt`. */
    setActiveRuns: (runs: ActiveRuns, requestedAt: number) => void;

    // Messages (for current session)
    messages: Message[];
    setMessages: (messages: Message[]) => void;
    addMessage: (message: Message) => void;
    removeMessage: (id: number) => void;
    removeMessagesFrom: (id: number) => void;

    // Streaming state
    isStreaming: boolean;
    streamingContent: string;
    setStreaming: (streaming: boolean) => void;
    setStreamContent: (content: string) => void;
    clearStreamContent: () => void;
    /** Atomically clear all streaming state (streaming flag, content, steps).
     *  Used when switching sessions so the incoming view never mirrors the
     *  previous session's in-flight stream (issue #1580). */
    resetStreaming: () => void;

    // Agent activity (steps during current response)
    agentSteps: AgentStep[];
    addAgentStep: (step: AgentStep) => void;
    updateLastAgentStep: (updates: Partial<AgentStep>) => void;
    /** Atomically append content to the last thinking step's detail.
     *  Reads + writes inside a single set() to avoid stale-read races. */
    appendThinkingContent: (content: string) => void;
    /** Update the last tool step (not the absolute last step). */
    updateLastToolStep: (updates: Partial<AgentStep>) => void;
    clearAgentSteps: () => void;
    /** What the model is doing right now, from the latest phase status event; null before one arrives. */
    liveStatus: string | null;
    setLiveStatus: (status: string | null) => void;
    /** The run is blocked on the user's approval or answer, not on the model. */
    awaitingUser: boolean;
    setAwaitingUser: (awaiting: boolean) => void;
    /** Mid-run questions the agent is still blocked on, by request id. A card
     *  missing from here can no longer be answered. */
    liveQuestions: Record<string, LiveQuestion>;
    openQuestion: (requestId: string, sessionId: string, timeoutSeconds?: number | null) => void;
    closeQuestion: (requestId: string) => void;
    closeSessionQuestions: (sessionId: string) => void;

    // Structured cards from tool_result.render events (issue #2108).
    // Accumulated during the stream, transferred onto the finalized
    // Message by ChatView (onDone/handleStop), then cleared — mirroring
    // the agentSteps lifecycle exactly.
    cards: RenderCardData[];
    appendCard: (card: RenderCardData) => void;
    clearCards: () => void;

    // Documents
    documents: Document[];
    setDocuments: (docs: Document[]) => void;

    // Connection / system status
    systemStatus: SystemStatus | null;
    backendConnected: boolean;
    setSystemStatus: (status: SystemStatus | null) => void;
    setBackendConnected: (connected: boolean) => void;

    // UI state
    /** What the user picked; `theme` is what is painted. */
    themePreference: ThemePreference;
    theme: 'light' | 'dark';
    showDocLibrary: boolean;
    showFileBrowser: boolean;
    /** Open settings section, or null when settings is closed. */
    settingsSection: SettingsSection | null;
    showMemoryDashboard: boolean;
    showSchedules: boolean;
    /** Permission mode picked on the new-chat screen, applied when the chat is created. */
    draftPermissionMode: PermissionMode;
    /** The mode a new chat starts in: `full_access` in config, else ask. */
    defaultPermissionMode: PermissionMode;
    /** Library documents picked with no chat open; the next new chat starts with them. */
    draftDocumentIds: string[];
    sidebarOpen: boolean;
    sidebarCollapsed: boolean;
    isLoadingMessages: boolean;
    /** A first message queued before its chat view mounted; ChatView sends it. */
    pendingPrompt: string | null;
    setThemePreference: (pref: ThemePreference) => void;
    /** Re-resolve a `system` preference after the OS appearance changed. */
    syncSystemTheme: () => void;
    setShowDocLibrary: (show: boolean) => void;
    setShowFileBrowser: (show: boolean) => void;
    openSettings: (section?: SettingsSection) => void;
    closeSettings: () => void;
    setShowMemoryDashboard: (show: boolean) => void;
    setShowSchedules: (show: boolean) => void;
    setDraftPermissionMode: (mode: PermissionMode) => void;
    /** Adopt the configured default; an untouched draft follows it. */
    setDefaultPermissionMode: (mode: PermissionMode) => void;
    addDraftDocuments: (ids: string[]) => void;
    removeDraftDocument: (id: string) => void;
    clearDraftDocuments: () => void;
    setSidebarOpen: (open: boolean) => void;
    toggleSidebarCollapsed: () => void;
    setSidebarCollapsed: (collapsed: boolean) => void;
    setLoadingMessages: (loading: boolean) => void;
    setPendingPrompt: (prompt: string | null) => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
    agents: [],
    setAgents: (agents) => set({ agents }),

    // Device selection
    activeDevice: readPref('gaia-active-device', 'gpu'),
    setActiveDevice: (device) => {
        writePref('gaia-active-device', device);
        set({ activeDevice: device });
    },
    detectedDevices: ['gpu'],
    setDetectedDevices: (devices) => set({ detectedDevices: devices }),

    // Sessions
    sessions: [],
    currentSessionId: null,
    pendingDeleteIds: [],
    runningSessionIds: [],
    setRunningSessions: (ids) =>
        set((state) => {
            // Reference-stable update: skip the set() when the running set is
            // unchanged so the polled refresh doesn't re-render the sidebar.
            if (sameIds(state.runningSessionIds, ids)) return state;
            return { runningSessionIds: ids };
        }),
    queuedSessionIds: [],
    awaitingBySession: {},
    activeRunsAt: 0,
    setActiveRuns: (runs, requestedAt) =>
        set((state) => {
            const next: Partial<ChatState> = { activeRunsAt: requestedAt };
            if (!sameIds(state.runningSessionIds, runs.session_ids)) next.runningSessionIds = runs.session_ids;
            if (state.queuedSessionIds.join() !== runs.queued.join()) next.queuedSessionIds = runs.queued;
            if (JSON.stringify(state.awaitingBySession) !== JSON.stringify(runs.awaiting_user)) {
                next.awaitingBySession = runs.awaiting_user;
            }
            return next;
        }),
    setSessions: (sessions) =>
        set((state) => ({
            // Filter out any sessions that are pending backend deletion so poll
            // results don't resurrect sessions the user already deleted.
            sessions: sessions.filter((s) => !state.pendingDeleteIds.includes(s.id)),
        })),
    // Opening a chat leaves any full-page view (memory, schedules).
    setCurrentSession: (id) => set(id
        ? { currentSessionId: id, showMemoryDashboard: false, showSchedules: false }
        : { currentSessionId: id }),
    addSession: (session) =>
        set((state) => ({ sessions: [session, ...state.sessions] })),
    removeSession: (id) =>
        set((state) => ({
            sessions: state.sessions.filter((s) => s.id !== id),
            currentSessionId: state.currentSessionId === id ? null : state.currentSessionId,
            messages: state.currentSessionId === id ? [] : state.messages,
        })),
    updateSessionInList: (id, updates) =>
        set((state) => ({
            sessions: state.sessions.map((s) => (s.id === id ? { ...s, ...updates } : s)),
        })),
    addPendingDelete: (id) =>
        set((state) => ({
            pendingDeleteIds: [...state.pendingDeleteIds, id],
        })),
    removePendingDelete: (id) =>
        set((state) => ({
            pendingDeleteIds: state.pendingDeleteIds.filter((pid) => pid !== id),
        })),

    // Messages
    messages: [],
    setMessages: (messages) => set({ messages }),
    addMessage: (message) =>
        set((state) => ({ messages: [...state.messages, message] })),
    removeMessage: (id) =>
        set((state) => ({ messages: state.messages.filter((m) => m.id !== id) })),
    removeMessagesFrom: (id) =>
        set((state) => {
            const idx = state.messages.findIndex((m) => m.id === id);
            if (idx === -1) return state;
            return { messages: state.messages.slice(0, idx) };
        }),

    // Streaming
    isStreaming: false,
    streamingContent: '',
    setStreaming: (streaming) => set({ isStreaming: streaming }),
    setStreamContent: (content) => set({ streamingContent: content }),
    clearStreamContent: () => set({ streamingContent: '' }),
    resetStreaming: () => set({ isStreaming: false, streamingContent: '', agentSteps: [], cards: [], liveStatus: null, awaitingUser: false }),

    // Agent activity
    agentSteps: [],
    addAgentStep: (step) =>
        set((state) => ({
            agentSteps: [
                // A status or thought arriving mid-call doesn't end a running tool; its tool_end does.
                ...state.agentSteps.map((s) =>
                    s.active && s.type === 'tool' && step.type !== 'tool' && s.success == null && !s.result
                        ? s
                        : { ...s, active: false }),
                step,
            ],
        })),
    updateLastAgentStep: (updates) =>
        set((state) => {
            if (state.agentSteps.length === 0) return state;
            const steps = [...state.agentSteps];
            steps[steps.length - 1] = { ...steps[steps.length - 1], ...updates };
            return { agentSteps: steps };
        }),
    appendThinkingContent: (content) =>
        set((state) => {
            if (state.agentSteps.length === 0) return state;
            const steps = [...state.agentSteps];
            const last = steps[steps.length - 1];
            if (last.type !== 'thinking') return state;
            steps[steps.length - 1] = {
                ...last,
                detail: (last.detail ? last.detail + '\n' : '') + content,
                active: true,
            };
            return { agentSteps: steps };
        }),
    updateLastToolStep: (updates) =>
        set((state) => {
            if (state.agentSteps.length === 0) return state;
            const steps = [...state.agentSteps];
            // Find the last tool step (searching backwards)
            for (let i = steps.length - 1; i >= 0; i--) {
                if (steps[i].type === 'tool') {
                    steps[i] = { ...steps[i], ...updates };
                    return { agentSteps: steps };
                }
            }
            // No tool step found — don't corrupt non-tool steps
            return state;
        }),
    clearAgentSteps: () => set({ agentSteps: [], liveStatus: null, awaitingUser: false }),
    liveStatus: null,
    setLiveStatus: (status) => set({ liveStatus: status }),
    awaitingUser: false,
    setAwaitingUser: (awaiting) => set({ awaitingUser: awaiting }),
    liveQuestions: {},
    openQuestion: (requestId, sessionId, timeoutSeconds) =>
        set((state) => ({
            liveQuestions: {
                ...state.liveQuestions,
                [requestId]: {
                    sessionId,
                    expiresAt: timeoutSeconds != null && timeoutSeconds > 0 ? Date.now() + timeoutSeconds * 1000 : null,
                },
            },
        })),
    closeQuestion: (requestId) =>
        set((state) => {
            if (!(requestId in state.liveQuestions)) return state;
            const { [requestId]: _closed, ...rest } = state.liveQuestions;
            return { liveQuestions: rest };
        }),
    closeSessionQuestions: (sessionId) =>
        set((state) => {
            const rest = Object.fromEntries(
                Object.entries(state.liveQuestions).filter(([, q]) => q.sessionId !== sessionId),
            );
            return Object.keys(rest).length === Object.keys(state.liveQuestions).length ? state : { liveQuestions: rest };
        }),

    // Streaming cards (#2108)
    cards: [],
    appendCard: (card) =>
        set((state) => ({ cards: [...state.cards, card] })),
    clearCards: () => set({ cards: [] }),

    // Documents
    documents: [],
    setDocuments: (docs) => set({ documents: docs }),

    // Connection / system status
    systemStatus: null,
    backendConnected: true, // Assume connected until proven otherwise
    setSystemStatus: (status) => set({ systemStatus: status }),
    setBackendConnected: (connected) => set({ backendConnected: connected }),

    // UI
    themePreference: readThemePreference(),
    theme: resolveTheme(readThemePreference()),
    showDocLibrary: false,
    showFileBrowser: false,
    settingsSection: null,
    showMemoryDashboard: false,
    showSchedules: false,
    draftPermissionMode: 'ask',
    defaultPermissionMode: 'ask',
    draftDocumentIds: [],
    setThemePreference: (pref) => {
        writePref('gaia-chat-theme', pref);
        const theme = resolveTheme(pref);
        applyTheme(theme);
        set({ themePreference: pref, theme });
    },
    syncSystemTheme: () => {
        if (get().themePreference !== 'system') return;
        const theme = resolveTheme('system');
        applyTheme(theme);
        set({ theme });
    },
    sidebarOpen: typeof window !== 'undefined' ? window.innerWidth > 768 : true,
    sidebarCollapsed: readPref('gaia-chat-sidebar-collapsed', 'false') === 'true',
    isLoadingMessages: false,
    pendingPrompt: null,
    setShowDocLibrary: (show) => set({ showDocLibrary: show }),
    setShowFileBrowser: (show) => set({ showFileBrowser: show }),
    openSettings: (section = 'general') => set({ settingsSection: section }),
    closeSettings: () => set({ settingsSection: null }),
    setShowMemoryDashboard: (show) =>
        set(show ? { showMemoryDashboard: true, showSchedules: false } : { showMemoryDashboard: false }),
    setShowSchedules: (show) =>
        set(show ? { showSchedules: true, showMemoryDashboard: false } : { showSchedules: false }),
    setDraftPermissionMode: (mode) => set({ draftPermissionMode: mode }),
    setDefaultPermissionMode: (mode) =>
        set((state) => ({
            defaultPermissionMode: mode,
            draftPermissionMode:
                state.draftPermissionMode === state.defaultPermissionMode ? mode : state.draftPermissionMode,
        })),
    addDraftDocuments: (ids) =>
        set((state) => {
            const toAdd = ids.filter((id) => !state.draftDocumentIds.includes(id));
            return toAdd.length ? { draftDocumentIds: [...state.draftDocumentIds, ...toAdd] } : state;
        }),
    removeDraftDocument: (id) =>
        set((state) => ({ draftDocumentIds: state.draftDocumentIds.filter((d) => d !== id) })),
    clearDraftDocuments: () => set({ draftDocumentIds: [] }),
    setSidebarOpen: (open) => set({ sidebarOpen: open }),
    toggleSidebarCollapsed: () =>
        set((state) => {
            const next = !state.sidebarCollapsed;
            writePref('gaia-chat-sidebar-collapsed', String(next));
            return { sidebarCollapsed: next };
        }),
    setSidebarCollapsed: (collapsed) => {
        writePref('gaia-chat-sidebar-collapsed', String(collapsed));
        set({ sidebarCollapsed: collapsed });
    },
    setLoadingMessages: (loading) => set({ isLoadingMessages: loading }),
    setPendingPrompt: (prompt) => set({ pendingPrompt: prompt }),
}));
