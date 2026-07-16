// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Zustand store for GAIA Agent UI state. */

import { create } from 'zustand';
import type { Session, Message, Document, AgentStep, SystemStatus, AgentInfo, RenderCardData } from '../types';

interface ChatState {
    // Agents
    agents: AgentInfo[];
    activeAgentId: string;
    setAgents: (agents: AgentInfo[]) => void;
    setActiveAgentId: (id: string) => void;

    // Device selection (CPU / GPU / NPU)
    activeDevice: string;
    setActiveDevice: (device: string) => void;
    detectedDevices: string[];
    setDetectedDevices: (devices: string[]) => void;

    // Model-size tier selection (issue #1162): "full" | "lite"
    activeModelTier: string;
    setActiveModelTier: (tier: string) => void;

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
    appendStreamContent: (content: string) => void;
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
    theme: 'light' | 'dark';
    showDocLibrary: boolean;
    showFileBrowser: boolean;
    showSettings: boolean;
    showMemoryDashboard: boolean;
    showSchedules: boolean;
    sidebarOpen: boolean;
    sidebarCollapsed: boolean;
    sidebarWidth: number;
    isLoadingMessages: boolean;
    /** Prompt queued from WelcomeScreen to be consumed by ChatView on mount. */
    pendingPrompt: string | null;
    toggleTheme: () => void;
    setShowDocLibrary: (show: boolean) => void;
    setShowFileBrowser: (show: boolean) => void;
    setShowSettings: (show: boolean) => void;
    setShowMemoryDashboard: (show: boolean) => void;
    setShowSchedules: (show: boolean) => void;
    toggleSidebar: () => void;
    setSidebarOpen: (open: boolean) => void;
    toggleSidebarCollapsed: () => void;
    setSidebarCollapsed: (collapsed: boolean) => void;
    setSidebarWidth: (width: number) => void;
    setLoadingMessages: (loading: boolean) => void;
    setPendingPrompt: (prompt: string | null) => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
    // Agents
    agents: [],
    activeAgentId: (() => {
        try { return localStorage.getItem('gaia-active-agent-id') || 'chat'; }
        catch { return 'chat'; }
    })(),
    setAgents: (agents) => set({ agents }),
    setActiveAgentId: (id) => {
        try { localStorage.setItem('gaia-active-agent-id', id); } catch { /* noop */ }
        set({ activeAgentId: id });
    },

    // Device selection
    activeDevice: (() => {
        try { return localStorage.getItem('gaia-active-device') || 'gpu'; }
        catch { return 'gpu'; }
    })(),
    setActiveDevice: (device) => {
        try { localStorage.setItem('gaia-active-device', device); } catch { /* noop */ }
        set({ activeDevice: device });
    },
    detectedDevices: ['gpu'],
    setDetectedDevices: (devices) => set({ detectedDevices: devices }),

    // Model-size tier selection (#1162)
    activeModelTier: (() => {
        try { return localStorage.getItem('gaia-active-model-tier') || 'full'; }
        catch { return 'full'; }
    })(),
    setActiveModelTier: (tier) => {
        try { localStorage.setItem('gaia-active-model-tier', tier); } catch { /* noop */ }
        set({ activeModelTier: tier });
    },

    // Sessions
    sessions: [],
    currentSessionId: null,
    pendingDeleteIds: [],
    runningSessionIds: [],
    setRunningSessions: (ids) =>
        set((state) => {
            // Reference-stable update: skip the set() when the running set is
            // unchanged so the polled refresh doesn't re-render the sidebar.
            const prev = state.runningSessionIds;
            if (prev.length === ids.length && prev.every((id) => ids.includes(id))) {
                return state;
            }
            return { runningSessionIds: ids };
        }),
    setSessions: (sessions) =>
        set((state) => ({
            // Filter out any sessions that are pending backend deletion so poll
            // results don't resurrect sessions the user already deleted.
            sessions: sessions.filter((s) => !state.pendingDeleteIds.includes(s.id)),
        })),
    setCurrentSession: (id) => set({ currentSessionId: id }),
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
    appendStreamContent: (content) =>
        set((state) => ({ streamingContent: state.streamingContent + content })),
    setStreamContent: (content) => set({ streamingContent: content }),
    clearStreamContent: () => set({ streamingContent: '' }),
    resetStreaming: () => set({ isStreaming: false, streamingContent: '', agentSteps: [], cards: [] }),

    // Agent activity
    agentSteps: [],
    addAgentStep: (step) =>
        set((state) => ({
            agentSteps: [
                // Deactivate previous steps
                ...state.agentSteps.map((s) => ({ ...s, active: false })),
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
    clearAgentSteps: () => set({ agentSteps: [] }),

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
    theme: (() => {
        try { return (localStorage.getItem('gaia-chat-theme') as 'light' | 'dark') || 'dark'; }
        catch { return 'dark'; }
    })(),
    showDocLibrary: false,
    showFileBrowser: false,
    showSettings: false,
    showMemoryDashboard: false,
    showSchedules: false,
    toggleTheme: () =>
        set((state) => {
            const next = state.theme === 'dark' ? 'light' : 'dark';
            try { localStorage.setItem('gaia-chat-theme', next); } catch { /* noop */ }
            document.documentElement.setAttribute('data-theme', next);
            return { theme: next };
        }),
    sidebarOpen: typeof window !== 'undefined' ? window.innerWidth > 768 : true,
    sidebarCollapsed: (() => {
        try { return typeof window !== 'undefined' && localStorage.getItem('gaia-chat-sidebar-collapsed') === 'true'; }
        catch { return false; }
    })(),
    sidebarWidth: (() => {
        try { return typeof window !== 'undefined' ? parseInt(localStorage.getItem('gaia-chat-sidebar-width') || '300', 10) : 300; }
        catch { return 300; }
    })(),
    isLoadingMessages: false,
    pendingPrompt: null,
    setShowDocLibrary: (show) => set({ showDocLibrary: show }),
    setShowFileBrowser: (show) => set({ showFileBrowser: show }),
    setShowSettings: (show) =>
        set(show ? { showSettings: true, showMemoryDashboard: false, showSchedules: false } : { showSettings: false }),
    setShowMemoryDashboard: (show) =>
        set(show ? { showMemoryDashboard: true, showSettings: false, showSchedules: false } : { showMemoryDashboard: false }),
    setShowSchedules: (show) =>
        set(show ? { showSchedules: true, showSettings: false, showMemoryDashboard: false } : { showSchedules: false }),
    toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
    setSidebarOpen: (open) => set({ sidebarOpen: open }),
    toggleSidebarCollapsed: () =>
        set((state) => {
            const next = !state.sidebarCollapsed;
            try { localStorage.setItem('gaia-chat-sidebar-collapsed', String(next)); } catch { /* noop */ }
            return { sidebarCollapsed: next };
        }),
    setSidebarCollapsed: (collapsed) => {
        try { localStorage.setItem('gaia-chat-sidebar-collapsed', String(collapsed)); } catch { /* noop */ }
        set({ sidebarCollapsed: collapsed });
    },
    setSidebarWidth: (width) => {
        const clamped = Math.max(200, Math.min(500, width));
        try { localStorage.setItem('gaia-chat-sidebar-width', String(clamped)); } catch { /* noop */ }
        set({ sidebarWidth: clamped });
    },
    setLoadingMessages: (loading) => set({ isLoadingMessages: loading }),
    setPendingPrompt: (prompt) => set({ pendingPrompt: prompt }),
}));
