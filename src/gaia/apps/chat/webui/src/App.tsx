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
import { log, logBanner } from './utils/logger';

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

    // Startup banner + load sessions on mount
    useEffect(() => {
        logBanner(__APP_VERSION__);
        log.system.info('App mounting, loading sessions...');
        const t = log.system.time();

        api.listSessions()
            .then((data) => {
                const sessions = data.sessions || [];
                setSessions(sessions);
                log.system.timed(`Loaded ${sessions.length} session(s)`, t);
            })
            .catch((err) => {
                log.system.error('Failed to load sessions from backend', err);
                log.system.warn('Is the Python backend running? Start it with: python -m gaia.chat.ui.server');
            });
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
        log.chat.info('Creating new chat session...');
        try {
            const session = await api.createSession({ title: 'New Chat' });
            log.chat.info(`Session created: id=${session.id}, title="${session.title}"`);
            addSession(session);
            setCurrentSession(session.id);
            setMessages([]);
            // Auto-close sidebar on mobile
            if (window.innerWidth <= 768) setSidebarOpen(false);
        } catch (err) {
            log.chat.error('Failed to create session', err);
        }
    }, [addSession, setCurrentSession, setMessages, setSidebarOpen]);

    // Create chat with a pre-filled prompt
    const handleNewChatWithPrompt = useCallback(async (prompt: string) => {
        log.chat.info(`New chat with prompt: "${prompt.slice(0, 60)}..."`);
        await handleNewChat();
        // Dispatch a custom event so ChatView picks up the initial prompt
        window.dispatchEvent(new CustomEvent('gaia:send-prompt', { detail: { prompt } }));
    }, [handleNewChat]);

    // Log view transitions
    useEffect(() => {
        if (currentSessionId) {
            log.nav.info(`Viewing session: ${currentSessionId}`);
        } else {
            log.nav.info('Viewing welcome screen (no session selected)');
        }
    }, [currentSessionId]);

    useEffect(() => {
        if (showDocLibrary) log.ui.info('Document Library opened');
    }, [showDocLibrary]);

    useEffect(() => {
        if (showSettings) log.ui.info('Settings modal opened');
    }, [showSettings]);

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
