// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useCallback } from 'react';
import { Menu } from 'lucide-react';
import { Sidebar } from './components/Sidebar';
import { ChatView } from './components/ChatView';
import { WelcomeScreen } from './components/WelcomeScreen';
import { DocumentLibrary } from './components/DocumentLibrary';
import { SettingsModal } from './components/SettingsModal';
import { useChatStore } from './stores/chatStore';
import * as api from './services/api';

function App() {
    const {
        currentSessionId,
        setSessions,
        setCurrentSession,
        addSession,
        setMessages,
        showDocLibrary,
        showSettings,
        sidebarOpen,
        toggleSidebar,
        setSidebarOpen,
    } = useChatStore();

    // Load sessions on mount
    useEffect(() => {
        api.listSessions()
            .then((data) => setSessions(data.sessions || []))
            .catch((err) => console.error('Failed to load sessions:', err));
    }, [setSessions]);

    // Close sidebar on resize to desktop
    useEffect(() => {
        const handleResize = () => {
            if (window.innerWidth > 768) {
                setSidebarOpen(true);
            }
        };
        window.addEventListener('resize', handleResize);
        return () => window.removeEventListener('resize', handleResize);
    }, [setSidebarOpen]);

    // Create new chat
    const handleNewChat = useCallback(async () => {
        try {
            const session = await api.createSession({ title: 'New Chat' });
            addSession(session);
            setCurrentSession(session.id);
            setMessages([]);
            // Auto-close sidebar on mobile
            if (window.innerWidth <= 768) setSidebarOpen(false);
        } catch (err) {
            console.error('Failed to create session:', err);
        }
    }, [addSession, setCurrentSession, setMessages, setSidebarOpen]);

    // Create chat with a pre-filled prompt
    const handleNewChatWithPrompt = useCallback(async (prompt: string) => {
        await handleNewChat();
        // Dispatch a custom event so ChatView picks up the initial prompt
        window.dispatchEvent(new CustomEvent('gaia:send-prompt', { detail: { prompt } }));
    }, [handleNewChat]);

    return (
        <div className="app">
            {/* Mobile sidebar toggle */}
            <button
                className="sidebar-toggle"
                onClick={toggleSidebar}
                aria-label={sidebarOpen ? 'Close sidebar' : 'Open sidebar'}
            >
                <Menu size={18} />
            </button>

            {/* Mobile overlay when sidebar is open */}
            <div
                className={`sidebar-overlay ${sidebarOpen ? 'visible' : ''}`}
                onClick={() => setSidebarOpen(false)}
                aria-hidden="true"
            />

            <Sidebar onNewChat={handleNewChat} />

            {currentSessionId ? (
                <ChatView key={currentSessionId} sessionId={currentSessionId} />
            ) : (
                <WelcomeScreen
                    onNewChat={handleNewChat}
                    onSendPrompt={handleNewChatWithPrompt}
                />
            )}

            {showDocLibrary && <DocumentLibrary />}
            {showSettings && <SettingsModal />}
        </div>
    );
}

export default App;
