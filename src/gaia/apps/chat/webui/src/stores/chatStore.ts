// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Zustand store for GAIA Chat UI state. */

import { create } from 'zustand';
import type { Session, Message, Document } from '../types';

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

    // Documents
    documents: Document[];
    setDocuments: (docs: Document[]) => void;

    // UI state
    theme: 'light' | 'dark';
    showDocLibrary: boolean;
    showSettings: boolean;
    sidebarOpen: boolean;
    isLoadingMessages: boolean;
    toggleTheme: () => void;
    setShowDocLibrary: (show: boolean) => void;
    setShowSettings: (show: boolean) => void;
    toggleSidebar: () => void;
    setSidebarOpen: (open: boolean) => void;
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

    // Documents
    documents: [],
    setDocuments: (docs) => set({ documents: docs }),

    // UI
    theme: (localStorage.getItem('gaia-chat-theme') as 'light' | 'dark') ||
        (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'),
    showDocLibrary: false,
    showSettings: false,
    toggleTheme: () =>
        set((state) => {
            const next = state.theme === 'dark' ? 'light' : 'dark';
            localStorage.setItem('gaia-chat-theme', next);
            document.documentElement.setAttribute('data-theme', next);
            return { theme: next };
        }),
    sidebarOpen: true,
    isLoadingMessages: false,
    setShowDocLibrary: (show) => set({ showDocLibrary: show }),
    setShowSettings: (show) => set({ showSettings: show }),
    toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
    setSidebarOpen: (open) => set({ sidebarOpen: open }),
    setLoadingMessages: (loading) => set({ isLoadingMessages: loading }),
}));
