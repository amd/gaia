// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useCallback, useState, useRef } from 'react';
import { Menu, Smartphone } from 'lucide-react';
import { Sidebar } from './components/Sidebar';
import { ChatView } from './components/ChatView';
import { WelcomeScreen } from './components/WelcomeScreen';
import { DocumentLibrary } from './components/DocumentLibrary';
import { FileBrowser } from './components/FileBrowser';
import { SettingsModal } from './components/SettingsModal';
import { MobileAccessModal } from './components/MobileAccessModal';
import { ConnectionBanner } from './components/ConnectionBanner';
import { PermissionPrompt } from './components/PermissionPrompt';
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
        showFileBrowser,
        showSettings,
        sidebarOpen,
        toggleSidebar,
        setSidebarOpen,
        setSystemStatus,
        setBackendConnected,
    } = useChatStore();

    // Mobile gateway state
    const [showMobileAccess, setShowMobileAccess] = useState(false);
    const [tunnelActive, setTunnelActive] = useState(false);
    const [tunnelLoading, setTunnelLoading] = useState(false);
    const [tunnelError, setTunnelError] = useState<string | null>(null);

    // ── Check system status (Lemonade, backend connectivity) ────────
    const statusPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

    const checkSystemStatus = useCallback(async () => {
        try {
            const status = await api.getSystemStatus();
            setSystemStatus(status);
            setBackendConnected(true);
            log.system.info('System status:', {
                lemonade: status.lemonade_running,
                model: status.model_loaded,
            });
        } catch (err) {
            log.system.warn('System status check failed', err);
            // The system/status endpoint is lightweight and should always succeed
            // if the backend is running. Any failure means the backend is unreachable
            // (either a network error, or the Vite proxy returning 500/502).
            setBackendConnected(false);
            setSystemStatus(null);
        }
    }, [setSystemStatus, setBackendConnected]);

    // Check status on mount, then poll every 15 seconds
    useEffect(() => {
        checkSystemStatus();
        statusPollRef.current = setInterval(checkSystemStatus, 15_000);
        return () => {
            if (statusPollRef.current) clearInterval(statusPollRef.current);
        };
    }, [checkSystemStatus]);

    // Startup banner + load sessions on mount, then poll for changes
    const sessionPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const lastSessionFingerprintRef = useRef<string>('');

    useEffect(() => {
        logBanner(__APP_VERSION__);
        log.system.info('App mounting, loading sessions...');
        const t = log.system.time();

        const loadSessions = (isInitial = false) => {
            api.listSessions()
                .then((data) => {
                    const sessions = data.sessions || [];
                    const fingerprint = sessions.map((s: { id: string; title?: string }) => `${s.id}:${s.title ?? ''}`).join('|');
                    if (isInitial) {
                        setSessions(sessions);
                        setBackendConnected(true);
                        log.system.timed(`Loaded ${sessions.length} session(s)`, t);
                        lastSessionFingerprintRef.current = fingerprint;
                    } else if (fingerprint !== lastSessionFingerprintRef.current) {
                        // Guard: don't replace a populated list with an empty one
                        // (transient API error returning empty array)
                        const { sessions: currentSessions } = useChatStore.getState();
                        if (sessions.length === 0 && currentSessions.length > 0) return;
                        log.system.info(`Session list changed, refreshing sidebar`);
                        setSessions(sessions);
                        lastSessionFingerprintRef.current = fingerprint;
                    }
                })
                .catch((err) => {
                    if (isInitial) {
                        log.system.error('Failed to load sessions from backend', err);
                        log.system.warn('Is the Python backend running? Start it with: gaia chat --ui');
                    }
                });
        };

        loadSessions(true);

        // Poll every 5s so sessions created by external tools (MCP, API) appear
        sessionPollRef.current = setInterval(() => loadSessions(false), 5_000);
        return () => {
            if (sessionPollRef.current) clearInterval(sessionPollRef.current);
        };
    }, [setSessions, setBackendConnected]);

    // Support URL-based session navigation (?session=<id>)
    useEffect(() => {
        const params = new URLSearchParams(window.location.search);
        const sessionParam = params.get('session');
        if (sessionParam && !currentSessionId) {
            log.nav.info(`URL session parameter: ${sessionParam}`);
            // Defer so session list has time to load
            const timer = setTimeout(() => {
                const { sessions } = useChatStore.getState();
                if (sessions.some((s: { id: string }) => s.id === sessionParam)) {
                    setCurrentSession(sessionParam);
                    setMessages([]);
                } else {
                    log.nav.warn(`Session ${sessionParam} not found in loaded sessions`);
                }
            }, 500);
            return () => clearTimeout(timer);
        }
    }, [currentSessionId, setCurrentSession, setMessages]);

    // Check tunnel status on mount
    useEffect(() => {
        api.getTunnelStatus()
            .then((status) => {
                setTunnelActive(status.active === true);
            })
            .catch(() => {
                // Ignore - tunnel feature may not be available
            });
    }, []);

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

    // Create new task
    const [createError, setCreateError] = useState<string | null>(null);

    const handleNewTask = useCallback(async () => {
        log.chat.info('Creating new task session...');
        setCreateError(null);
        try {
            const session = await api.createSession({ title: 'New Task' });
            log.chat.info(`Session created: id=${session.id}, title="${session.title}"`);
            setMessages([]);
            addSession(session);
            setCurrentSession(session.id);
            // Auto-close sidebar on mobile
            if (window.innerWidth <= 768) setSidebarOpen(false);
        } catch (err) {
            log.chat.error('Failed to create session', err);
            // Trigger a status recheck to update the banner
            checkSystemStatus();
            setCreateError('Failed to create task. Is the server running?');
            // Auto-clear error after a few seconds
            setTimeout(() => setCreateError(null), 6000);
        }
    }, [addSession, setCurrentSession, setMessages, setSidebarOpen, checkSystemStatus]);

    // Create task with a pre-filled prompt — stores the prompt in Zustand
    // so ChatView can consume it reliably on mount (no timing race).
    const { setPendingPrompt } = useChatStore();
    const handleNewTaskWithPrompt = useCallback(async (prompt: string) => {
        log.chat.info(`New task with prompt: "${prompt.slice(0, 60)}..."`);
        setPendingPrompt(prompt);
        await handleNewTask();
    }, [handleNewTask, setPendingPrompt]);

    // Mobile gateway toggle
    const handleMobileToggle = useCallback(async () => {
        if (tunnelActive) {
            // Stop tunnel
            log.system.info('Stopping mobile access tunnel...');
            try {
                await api.stopTunnel();
            } catch {
                // Ignore stop errors
            }
            setTunnelActive(false);
            setShowMobileAccess(false);
        } else {
            // Start tunnel
            log.system.info('Starting mobile access tunnel...');
            setShowMobileAccess(true);
            setTunnelLoading(true);
            setTunnelError(null);
            try {
                const status = await api.startTunnel();
                if (status.error) {
                    log.system.error('Tunnel failed to start:', status.error);
                    setTunnelActive(false);
                    setTunnelError(status.error);
                } else {
                    setTunnelActive(true);
                    log.system.info('Tunnel started successfully');
                }
            } catch (err) {
                log.system.error('Tunnel start error:', err);
                setTunnelActive(false);
                setTunnelError(err instanceof Error ? err.message : 'Failed to connect');
            } finally {
                setTunnelLoading(false);
            }
        }
    }, [tunnelActive]);

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

    // Reactive mobile detection — updates on resize
    const [isMobile, setIsMobile] = useState(
        typeof window !== 'undefined' && window.innerWidth <= 768
    );
    useEffect(() => {
        const handleResize = () => setIsMobile(window.innerWidth <= 768);
        window.addEventListener('resize', handleResize);
        return () => window.removeEventListener('resize', handleResize);
    }, []);

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

            <Sidebar
                onNewTask={handleNewTask}
                tunnelActive={tunnelActive}
                tunnelLoading={tunnelLoading}
                onMobileToggle={handleMobileToggle}
            />

            <div className="main-content">
                {/* Connection / LLM status banner */}
                <ConnectionBanner onRetry={checkSystemStatus} />

                {currentSessionId ? (
                    <ChatView key={currentSessionId} sessionId={currentSessionId} />
                ) : (
                    <WelcomeScreen
                        onNewTask={handleNewTask}
                        onSendPrompt={handleNewTaskWithPrompt}
                    />
                )}
            </div>

            {showDocLibrary && <DocumentLibrary />}
            {showFileBrowser && <FileBrowser />}
            {showSettings && <SettingsModal />}

            {/* Mobile Access Modal */}
            {!isMobile && (
                <MobileAccessModal
                    isOpen={showMobileAccess}
                    onClose={() => setShowMobileAccess(false)}
                    error={tunnelError}
                />
            )}

            {/* Tool confirmation popup */}
            <PermissionPrompt />

            {/* Session creation error toast */}
            {createError && (
                <div className="toast" role="alert">{createError}</div>
            )}
        </div>
    );
}

export default App;
