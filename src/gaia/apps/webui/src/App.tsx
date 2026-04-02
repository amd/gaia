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
import { getSessionHash, findSessionByHash } from './utils/format';

/** Wrapper that delays unmount to allow CSS exit animations to play. */
function AnimatedPresence({ show, children, duration = 250 }: {
    show: boolean;
    children: React.ReactNode;
    duration?: number;
}) {
    const [shouldRender, setShouldRender] = useState(false);
    const [animState, setAnimState] = useState<'entering' | 'exiting' | 'idle'>('idle');

    useEffect(() => {
        if (show) {
            setShouldRender(true);
            // Use rAF to ensure DOM has mounted before applying entering class
            requestAnimationFrame(() => setAnimState('entering'));
        } else if (shouldRender) {
            setAnimState('exiting');
            const timer = setTimeout(() => {
                setShouldRender(false);
                setAnimState('idle');
            }, duration);
            return () => clearTimeout(timer);
        }
    }, [show, shouldRender, duration]);

    if (!shouldRender) return null;

    return (
        <div className={`animated-presence ${animState}`} data-duration={duration}>
            {children}
        </div>
    );
}

function App() {
    const {
        currentSessionId,
        setSessions,
        setCurrentSession,
        addSession,
        removeSession,
        updateSessionInList,
        setMessages,
        showDocLibrary,
        showFileBrowser,
        showSettings,
        sidebarOpen,
        toggleSidebar,
        setSidebarOpen,
        systemStatus,
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
    // Track consecutive "lemonade not running" reports so a single slow
    // health-check under heavy load doesn't immediately show the warning banner.
    const lemonadeFailCountRef = useRef(0);
    const LEMONADE_FAIL_THRESHOLD = 3; // require 3 consecutive failures (~45s)

    const checkSystemStatus = useCallback(async () => {
        try {
            const status = await api.getSystemStatus();
            setBackendConnected(true);

            if (status.lemonade_running) {
                // Server confirmed running — reset failure counter
                lemonadeFailCountRef.current = 0;
                setSystemStatus(status);
            } else {
                // Server reported Lemonade not running — might be a transient
                // timeout when the LLM is overwhelmed with parallel requests.
                lemonadeFailCountRef.current += 1;
                log.system.warn(
                    `Lemonade health check failed (${lemonadeFailCountRef.current}/${LEMONADE_FAIL_THRESHOLD})`
                );

                if (lemonadeFailCountRef.current >= LEMONADE_FAIL_THRESHOLD) {
                    // Enough consecutive failures — propagate the "not running" state
                    setSystemStatus(status);
                } else {
                    // Below threshold — keep the previous (good) status to avoid
                    // flashing the warning banner on transient timeouts.
                    // Still update non-lemonade fields (disk, memory, etc).
                    const prev = useChatStore.getState().systemStatus;
                    if (prev && prev.lemonade_running) {
                        setSystemStatus({ ...prev, disk_space_gb: status.disk_space_gb, memory_available_gb: status.memory_available_gb });
                    } else {
                        // No previous good status — show what we have
                        setSystemStatus(status);
                    }
                }
            }

            log.system.info('System status:', {
                lemonade: status.lemonade_running,
                model: status.model_loaded,
                failCount: lemonadeFailCountRef.current,
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

    // Check status on mount, then poll adaptively:
    // 3s while init_state === 'initializing', 15s otherwise.
    const currentPollIntervalRef = useRef(3_000);

    useEffect(() => {
        checkSystemStatus();
        currentPollIntervalRef.current = 3_000; // Start fast during potential init
        statusPollRef.current = setInterval(checkSystemStatus, 3_000);
        return () => {
            if (statusPollRef.current) clearInterval(statusPollRef.current);
        };
    }, [checkSystemStatus]);

    // Adjust poll interval when init completes.
    // Keep 3s fast-poll while systemStatus is null (first response pending)
    // or init_state is 'initializing'. Switch to 15s only after a definitive
    // 'ready' or 'degraded' state arrives.
    useEffect(() => {
        const initState = systemStatus?.init_state;
        // Stay fast while waiting for first response or during init
        if (!initState || initState === 'initializing') return;
        const desiredInterval = 15_000;
        if (desiredInterval !== currentPollIntervalRef.current) {
            currentPollIntervalRef.current = desiredInterval;
            if (statusPollRef.current) clearInterval(statusPollRef.current);
            statusPollRef.current = setInterval(checkSystemStatus, desiredInterval);
        }
    }, [systemStatus?.init_state, checkSystemStatus]);

    // Startup banner + load sessions on mount, then poll for changes
    const sessionPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
    /** Fingerprint of the last server session list (id:updated_at:title per session). */
    const lastSessionFingerprintRef = useRef<string>('');

    useEffect(() => {
        logBanner(__APP_VERSION__);
        log.system.info('App mounting, loading sessions...');
        const t = log.system.time();

        /** Build a cheap fingerprint string for a session list so we can detect
         *  any change — new/deleted sessions, title edits, updated_at bumps. */
        const fingerprint = (sessions: Array<{ id: string; updated_at: string; title: string }>) =>
            sessions.map((s) => `${s.id}|${s.updated_at}|${s.title}`).join('\n');

        const loadSessions = (isInitial = false) => {
            api.listSessions()
                .then((data) => {
                    const serverSessions = data.sessions || [];
                    if (isInitial) {
                        setSessions(serverSessions);
                        setBackendConnected(true);
                        lastSessionFingerprintRef.current = fingerprint(serverSessions);
                        log.system.timed(`Loaded ${serverSessions.length} session(s)`, t);
                        return;
                    }

                    // Guard: never replace a populated sidebar with an empty list.
                    // This prevents transient backend glitches (restart, slow DB)
                    // from wiping the user's session list.
                    const localSessions = useChatStore.getState().sessions;
                    if (serverSessions.length === 0 && localSessions.length > 0) {
                        log.system.warn(
                            'Session poll returned 0 sessions but sidebar has '
                            + `${localSessions.length} — skipping update to prevent data loss`
                        );
                        return;
                    }

                    // Compare fingerprints to detect ANY change (count, titles,
                    // updated_at timestamps) — not just count changes.
                    const fp = fingerprint(serverSessions);
                    if (fp !== lastSessionFingerprintRef.current) {
                        log.system.info(
                            `Session list changed (${localSessions.length} → ${serverSessions.length} sessions)`
                        );
                        setSessions(serverSessions);
                        lastSessionFingerprintRef.current = fp;
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
    }, [setSessions, addSession, removeSession, updateSessionInList, setBackendConnected]);

    // Support URL-based session navigation (?session=<id> or #<hash>)
    useEffect(() => {
        if (currentSessionId) return; // Already have a session selected

        const params = new URLSearchParams(window.location.search);
        const sessionParam = params.get('session');
        const hashParam = window.location.hash.replace(/^#/, '');

        const target = sessionParam || hashParam;
        if (!target) return;

        log.nav.info(`URL session parameter: ${target}`);
        // Defer so session list has time to load
        const timer = setTimeout(() => {
            const { sessions } = useChatStore.getState();
            // Try exact match first (full UUID), then short hash match
            let matchId: string | null = sessions.some((s: { id: string }) => s.id === target)
                ? target
                : findSessionByHash(sessions, target);
            if (matchId) {
                setCurrentSession(matchId);
                setMessages([]);
            } else {
                log.nav.warn(`Session ${target} not found in loaded sessions`);
            }
        }, 500);
        return () => clearTimeout(timer);
    }, [currentSessionId, setCurrentSession, setMessages]);

    // Update URL hash when the current session changes
    useEffect(() => {
        if (currentSessionId) {
            const hash = getSessionHash(currentSessionId);
            if (window.location.hash !== `#${hash}`) {
                window.history.replaceState(null, '', `#${hash}`);
            }
        } else if (window.location.hash) {
            window.history.replaceState(null, '', window.location.pathname + window.location.search);
        }
    }, [currentSessionId]);

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
            addSession(session);
            setCurrentSession(session.id);
            setMessages([]);
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

    // ── Welcome -> Chat crossfade transition ─────────────────────────
    const [isViewTransitioning, setIsViewTransitioning] = useState(false);
    const [displayedSessionId, setDisplayedSessionId] = useState<string | null>(null);

    useEffect(() => {
        if (currentSessionId !== displayedSessionId) {
            setIsViewTransitioning(true);
            // Allow fade-out to complete, then swap content
            const timer = setTimeout(() => {
                setDisplayedSessionId(currentSessionId);
                // Brief delay before removing transition class (allows new content to mount)
                requestAnimationFrame(() => {
                    requestAnimationFrame(() => {
                        setIsViewTransitioning(false);
                    });
                });
            }, 220); // matches CSS transition duration
            return () => clearTimeout(timer);
        }
    }, [currentSessionId, displayedSessionId]);

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

                <div className={`view-container ${isViewTransitioning ? 'view-transitioning' : ''}`}>
                    {displayedSessionId ? (
                        <ChatView key={displayedSessionId} sessionId={displayedSessionId} />
                    ) : (
                        <WelcomeScreen
                            onNewTask={handleNewTask}
                            onSendPrompt={handleNewTaskWithPrompt}
                        />
                    )}
                </div>
            </div>

            <AnimatedPresence show={showDocLibrary}>
                <DocumentLibrary />
            </AnimatedPresence>
            <AnimatedPresence show={showFileBrowser}>
                <FileBrowser />
            </AnimatedPresence>
            <AnimatedPresence show={showSettings}>
                <SettingsModal />
            </AnimatedPresence>

            {/* Mobile Access Modal */}
            {!isMobile && (
                <AnimatedPresence show={showMobileAccess}>
                    <MobileAccessModal
                        isOpen={showMobileAccess}
                        onClose={() => setShowMobileAccess(false)}
                        error={tunnelError}
                    />
                </AnimatedPresence>
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
