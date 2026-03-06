// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Zustand store for GAIA Chat UI state. */

import { create } from 'zustand';
import type { Session, Message, Document, AgentStep } from '../types';

interface ChatState {
    // Sessions
    sessions: Session[];
    currentSessionId: string | null;
    setSessions: (sessions: Session[]) => void;
    setCurrentSession: (id: string | null) => void;
    addSession: (session: Session) => void;
    removeSession: (id: string) => void;
    updateSessionInList: (id: string, updates: Partial<Session>) => void;

    // Messages (for current session)
    messages: Message[];
    setMessages: (messages: Message[]) => void;
    addMessage: (message: Message) => void;

    // Streaming state
    isStreaming: boolean;
    streamingContent: string;
    setStreaming: (streaming: boolean) => void;
    appendStreamContent: (content: string) => void;
    clearStreamContent: () => void;

    // Agent activity (steps during current response)
    agentSteps: AgentStep[];
    addAgentStep: (step: AgentStep) => void;
    updateLastAgentStep: (updates: Partial<AgentStep>) => void;
    clearAgentSteps: () => void;

    // Documents
    documents: Document[];
    setDocuments: (docs: Document[]) => void;

    // UI state
    theme: 'light' | 'dark';
    showDocLibrary: boolean;
    showSettings: boolean;
    sidebarOpen: boolean;
    sidebarCollapsed: boolean;
    sidebarWidth: number;
    isLoadingMessages: boolean;
    toggleTheme: () => void;
    setShowDocLibrary: (show: boolean) => void;
    setShowSettings: (show: boolean) => void;
    toggleSidebar: () => void;
    setSidebarOpen: (open: boolean) => void;
    toggleSidebarCollapsed: () => void;
    setSidebarCollapsed: (collapsed: boolean) => void;
    setSidebarWidth: (width: number) => void;
    setLoadingMessages: (loading: boolean) => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
    // Sessions
    sessions: [],
    currentSessionId: null,
    setSessions: (sessions) => set({ sessions }),
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

    // Messages
    messages: [],
    setMessages: (messages) => set({ messages }),
    addMessage: (message) =>
        set((state) => ({ messages: [...state.messages, message] })),

    // Streaming
    isStreaming: false,
    streamingContent: '',
    setStreaming: (streaming) => set({ isStreaming: streaming }),
    appendStreamContent: (content) =>
        set((state) => ({ streamingContent: state.streamingContent + content })),
    clearStreamContent: () => set({ streamingContent: '' }),

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
    clearAgentSteps: () => set({ agentSteps: [] }),

    // Documents
    documents: [],
    setDocuments: (docs) => set({ documents: docs }),

    // UI
    theme: (localStorage.getItem('gaia-chat-theme') as 'light' | 'dark') || 'dark',
    showDocLibrary: false,
    showSettings: false,
    toggleTheme: () =>
        set((state) => {
            const next = state.theme === 'dark' ? 'light' : 'dark';
            localStorage.setItem('gaia-chat-theme', next);
            document.documentElement.setAttribute('data-theme', next);
            return { theme: next };
        }),
    sidebarOpen: typeof window !== 'undefined' ? window.innerWidth > 768 : true,
    sidebarCollapsed: typeof window !== 'undefined'
        ? localStorage.getItem('gaia-chat-sidebar-collapsed') === 'true'
        : false,
    sidebarWidth: typeof window !== 'undefined'
        ? parseInt(localStorage.getItem('gaia-chat-sidebar-width') || '300', 10)
        : 300,
    isLoadingMessages: false,
    setShowDocLibrary: (show) => set({ showDocLibrary: show }),
    setShowSettings: (show) => set({ showSettings: show }),
    toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
    setSidebarOpen: (open) => set({ sidebarOpen: open }),
    toggleSidebarCollapsed: () =>
        set((state) => {
            const next = !state.sidebarCollapsed;
            localStorage.setItem('gaia-chat-sidebar-collapsed', String(next));
            return { sidebarCollapsed: next };
        }),
    setSidebarCollapsed: (collapsed) => {
        localStorage.setItem('gaia-chat-sidebar-collapsed', String(collapsed));
        set({ sidebarCollapsed: collapsed });
    },
    setSidebarWidth: (width) => {
        const clamped = Math.max(200, Math.min(500, width));
        localStorage.setItem('gaia-chat-sidebar-width', String(clamped));
        set({ sidebarWidth: clamped });
    },
    setLoadingMessages: (loading) => set({ isLoadingMessages: loading }),
}));
