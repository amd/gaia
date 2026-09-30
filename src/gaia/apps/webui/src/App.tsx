// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { Menu, X } from 'lucide-react';
import { Sidebar } from './components/Sidebar';
import { ChatView } from './components/ChatView';
import { NewChat } from './components/NewChat';
import { SetupScreen } from './components/SetupScreen';
import { SettingsDialog } from './components/settings/SettingsDialog';
import { ConnectionBanner } from './components/ConnectionBanner';
import { UpdateIndicator } from './components/UpdateIndicator';
import { NotificationCenter } from './components/NotificationCenter';
import { useChatStore } from './stores/chatStore';
import { useModelStore } from './stores/modelStore';
import { useNotificationStore } from './stores/notificationStore';
import * as api from './services/api';
import { log, logBanner } from './utils/logger';
import { getSessionHash } from './utils/format';
import { resolveUrlNavTarget } from './utils/sessionNav';
import { getApiBase } from './utils/apiBase';
import { cleanupAbandonedDraft, isAbandonedDraft } from './utils/sessionCleanup';
import { FLAGSHIP_AGENT_ID } from './utils/newTask';
import type { SetupCheck } from './types';

// Heavy, occasional screens load on first use.
const MemoryDashboard = lazy(() => import('./components/MemoryDashboard').then((m) => ({ default: m.MemoryDashboard })));
const ScheduleManager = lazy(() => import('./components/ScheduleManager').then((m) => ({ default: m.ScheduleManager })));
const DocumentLibrary = lazy(() => import('./components/DocumentLibrary').then((m) => ({ default: m.DocumentLibrary })));
const FileBrowser = lazy(() => import('./components/FileBrowser').then((m) => ({ default: m.FileBrowser })));
const MobileAccessModal = lazy(() => import('./components/MobileAccessModal').then((m) => ({ default: m.MobileAccessModal })));

/** `checking` until `gaia init --check` answers; `needed` shows first-run setup. */
type SetupGate =
    | { state: 'checking' }
    | { state: 'ready' }
    | { state: 'needed'; check: SetupCheck }
    | { state: 'unknown'; error: string };

const LEMONADE_FAIL_THRESHOLD = 3;

function Loading() {
    return <div className="loading-spinner" role="status" aria-label="Loading" />;
}

function App() {
    const currentSessionId = useChatStore((s) => s.currentSessionId);
    const setSessions = useChatStore((s) => s.setSessions);
    const setCurrentSession = useChatStore((s) => s.setCurrentSession);
    const addSession = useChatStore((s) => s.addSession);
    const setMessages = useChatStore((s) => s.setMessages);
    const resetStreaming = useChatStore((s) => s.resetStreaming);
    const showDocLibrary = useChatStore((s) => s.showDocLibrary);
    const showFileBrowser = useChatStore((s) => s.showFileBrowser);
    const showMemoryDashboard = useChatStore((s) => s.showMemoryDashboard);
    const showSchedules = useChatStore((s) => s.showSchedules);
    const sidebarOpen = useChatStore((s) => s.sidebarOpen);
    const setSidebarOpen = useChatStore((s) => s.setSidebarOpen);
    const setSystemStatus = useChatStore((s) => s.setSystemStatus);
    const setBackendConnected = useChatStore((s) => s.setBackendConnected);
    const setAgents = useChatStore((s) => s.setAgents);
    const setRunningSessions = useChatStore((s) => s.setRunningSessions);
    const setPendingPrompt = useChatStore((s) => s.setPendingPrompt);
    const openSettings = useChatStore((s) => s.openSettings);
    const settingsSection = useChatStore((s) => s.settingsSection);
    const syncSystemTheme = useChatStore((s) => s.syncSystemTheme);
    const restoreNotice = useModelStore((s) => s.restoreNotice);
    const dismissRestoreNotice = useModelStore((s) => s.dismissRestoreNotice);
    const showNotificationPanel = useNotificationStore((s) => s.showPanel);
    const setShowNotificationPanel = useNotificationStore((s) => s.setShowPanel);

    const [setup, setSetup] = useState<SetupGate>({ state: 'checking' });
    const [createError, setCreateError] = useState<string | null>(null);
    const [showMobileAccess, setShowMobileAccess] = useState(false);
    const [tunnelActive, setTunnelActive] = useState(false);
    const [tunnelError, setTunnelError] = useState<string | null>(null);

    // ── First run: is GAIA set up? Same question the TUI asks. ──────────
    const checkSetup = useCallback(async () => {
        setSetup({ state: 'checking' });
        try {
            await useModelStore.getState().refresh();
            const active = useModelStore.getState().active;
            const check = await api.checkSetup(!!active?.remote);
            setSetup(check.ready ? { state: 'ready' } : { state: 'needed', check });
        } catch (err) {
            // Could not ask — not the same as "not set up"; the chat still works if it is.
            const error = err instanceof Error ? err.message : String(err);
            log.system.warn('Setup check could not be answered', err);
            setSetup({ state: 'unknown', error });
        }
    }, []);

    useEffect(() => {
        logBanner(__APP_VERSION__);
        void checkSetup();
        api.listAgents()
            .then((d) => setAgents(d.agents || []))
            .catch((err) => log.api.warn('Agent list unavailable; connector grants will be empty', err));
    }, [checkSetup, setAgents]);

    // ── Backend and model-server health ─────────────────────────────────
    const failCount = useRef(0);
    const checkSystemStatus = useCallback(async () => {
        try {
            const status = await api.getSystemStatus();
            setBackendConnected(true);
            if (status.detected_devices?.length) useChatStore.getState().setDetectedDevices(status.detected_devices);
            // One slow health probe under load is not an outage.
            failCount.current = status.lemonade_running ? 0 : failCount.current + 1;
            const prev = useChatStore.getState().systemStatus;
            if (status.lemonade_running || failCount.current >= LEMONADE_FAIL_THRESHOLD || !prev?.lemonade_running) {
                setSystemStatus(status);
            } else {
                setSystemStatus({ ...prev, download_progress: status.download_progress });
            }
        } catch (err) {
            log.system.warn('System status check failed', err);
            setBackendConnected(false);
            setSystemStatus(null);
        }
    }, [setBackendConnected, setSystemStatus]);

    useEffect(() => {
        void checkSystemStatus();
        const fast = useChatStore.getState().systemStatus?.download_progress ? 2_000 : 5_000;
        const t = setInterval(checkSystemStatus, fast);
        return () => clearInterval(t);
    }, [checkSystemStatus]);

    // ── Chats ───────────────────────────────────────────────────────────
    const sessionFingerprint = useRef('');
    useEffect(() => {
        const load = (initial: boolean) => {
            api.listSessions()
                .then((data) => {
                    const list = data.sessions || [];
                    // Never wipe a populated list on a transient empty reply.
                    if (!initial && list.length === 0 && useChatStore.getState().sessions.length > 0) return;
                    const fp = list.map((s) => `${s.id}|${s.updated_at}|${s.title}`).join('\n');
                    if (fp === sessionFingerprint.current) return;
                    sessionFingerprint.current = fp;
                    setSessions(list);
                })
                .catch((err) => { if (initial) log.system.error('Could not load chats', err); });
        };
        load(true);
        const t = setInterval(() => load(false), 5_000);
        return () => clearInterval(t);
    }, [setSessions]);

    useEffect(() => {
        const poll = () => {
            api.getActiveRuns()
                .then((d) => setRunningSessions(d.session_ids || []))
                .catch((err) => log.api.debug('Active-run poll failed', err));
        };
        poll();
        const t = setInterval(poll, 2_600);
        return () => clearInterval(t);
    }, [setRunningSessions]);

    // Open a chat named in the URL (?session= or #hash), on load and on back/forward.
    const urlResolved = useRef(false);
    useEffect(() => {
        const navigate = () => {
            const params = new URLSearchParams(window.location.search);
            const target = params.get('session') || window.location.hash.replace(/^#/, '');
            const { currentSessionId: cur, sessions } = useChatStore.getState();
            const id = resolveUrlNavTarget(target, cur, sessions);
            if (id) {
                setCurrentSession(id);
                setMessages([]);
            }
        };
        // The link can only resolve once the chat list has loaded.
        const first = () => {
            if (urlResolved.current) return;
            urlResolved.current = true;
            navigate();
        };
        const unsubscribe = useChatStore.subscribe((state) => { if (state.sessions.length > 0) first(); });
        if (useChatStore.getState().sessions.length > 0) first();
        window.addEventListener('hashchange', navigate);
        window.addEventListener('popstate', navigate);
        return () => {
            unsubscribe();
            window.removeEventListener('hashchange', navigate);
            window.removeEventListener('popstate', navigate);
        };
    }, [setCurrentSession, setMessages]);

    // Another client (MCP bridge) can ask this window to show a chat.
    useEffect(() => {
        let es: EventSource | null = null;
        let backoff = 2_000;
        let cancelled = false;
        let timer: ReturnType<typeof setTimeout> | null = null;
        const connect = () => {
            if (cancelled) return;
            es = new EventSource(`${getApiBase()}/sessions/events`);
            es.onopen = () => { backoff = 2_000; };
            es.onmessage = (ev) => {
                let data: { type?: string; session_id?: string };
                try {
                    data = JSON.parse(ev.data);
                } catch (err) {
                    log.api.warn('Malformed session event', err);
                    return;
                }
                if (data.type === 'set_active_session' && data.session_id
                    && data.session_id !== useChatStore.getState().currentSessionId) {
                    setCurrentSession(data.session_id);
                    setMessages([]);
                }
            };
            es.onerror = () => {
                es?.close();
                es = null;
                if (cancelled) return;
                timer = setTimeout(connect, backoff);
                backoff = Math.min(backoff * 2, 30_000);
            };
        };
        connect();
        return () => {
            cancelled = true;
            if (timer) clearTimeout(timer);
            es?.close();
        };
    }, [setCurrentSession, setMessages]);

    useEffect(() => {
        if (currentSessionId) {
            const hash = getSessionHash(currentSessionId);
            if (window.location.hash !== `#${hash}`) window.history.replaceState(null, '', `#${hash}`);
        } else if (urlResolved.current && window.location.hash) {
            window.history.replaceState(null, '', window.location.pathname + window.location.search);
        }
    }, [currentSessionId]);

    // A new chat's first message: make the chat, then hand ChatView the text.
    const startChat = useCallback(async (text: string) => {
        setCreateError(null);
        const store = useChatStore.getState();
        const session = await api.createSession({
            title: 'New Task',
            agent_type: FLAGSHIP_AGENT_ID,
            device: store.activeDevice,
        });
        addSession(session);
        setCurrentSession(session.id);
        setMessages([]);
        // Always sent: a config default of full access must not hide behind an "Ask" chip.
        try {
            await api.setPermissionMode(session.id, store.draftPermissionMode);
        } catch (err) {
            setCreateError(
                `The chat was created, but its permission mode could not be set, so your message was not sent: ${
                    err instanceof Error ? err.message : err}`,
            );
            return;
        }
        store.setDraftPermissionMode('ask');
        setPendingPrompt(text);
    }, [addSession, setCurrentSession, setMessages, setPendingPrompt]);

    const newChat = useCallback(() => {
        const store = useChatStore.getState();
        const outgoing = store.currentSessionId;
        if (outgoing && isAbandonedDraft(outgoing, store)) void cleanupAbandonedDraft(outgoing);
        store.setShowMemoryDashboard(false);
        store.setShowSchedules(false);
        setCurrentSession(null);
        setMessages([]);
    }, [setCurrentSession, setMessages]);

    const useSkill = useCallback((name: string) => {
        newChat();
        startChat(`Load the \`${name}\` skill, then tell me briefly what it lets you do.`)
            .catch((err) => setCreateError(err instanceof Error ? err.message : String(err)));
    }, [newChat, startChat]);

    // ── Keyboard shortcuts ─────────────────────────────────────────────
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            const mod = e.ctrlKey || e.metaKey;
            if (!mod) return;
            if (e.shiftKey && e.key.toLowerCase() === 'o') { e.preventDefault(); newChat(); }
            else if (!e.shiftKey && e.key.toLowerCase() === 'k') { e.preventDefault(); window.dispatchEvent(new CustomEvent('gaia:focus-search')); }
            else if (!e.shiftKey && e.key.toLowerCase() === 'b') { e.preventDefault(); useChatStore.getState().toggleSidebarCollapsed(); }
            else if (!e.shiftKey && e.key === ',') { e.preventDefault(); openSettings(); }
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [newChat, openSettings]);

    // The sidebar is a drawer on narrow windows: closed on load and when a
    // window narrows, open again when it widens (collapse is separate there).
    useEffect(() => {
        const mq = window.matchMedia?.('(max-width: 768px)');
        if (!mq) return;
        const sync = () => setSidebarOpen(!mq.matches);
        sync();
        mq.addEventListener('change', sync);
        return () => mq.removeEventListener('change', sync);
    }, [setSidebarOpen]);

    // Follow the OS appearance when the preference is "match system".
    useEffect(() => {
        if (typeof window.matchMedia !== 'function') return;
        const mq = window.matchMedia('(prefers-color-scheme: light)');
        mq.addEventListener('change', syncSystemTheme);
        return () => mq.removeEventListener('change', syncSystemTheme);
    }, [syncSystemTheme]);

    // Drop the previous chat's in-flight stream state when switching (#1580).
    useEffect(() => { resetStreaming(); }, [currentSessionId, resetStreaming]);

    // ── Mobile access ──────────────────────────────────────────────────
    useEffect(() => {
        api.getTunnelStatus()
            .then((s) => setTunnelActive(s.active === true))
            .catch((err) => log.system.debug('Mobile access unavailable', err));
    }, []);

    const openMobileAccess = useCallback(async () => {
        setShowMobileAccess(true);
        setTunnelError(null);
        if (tunnelActive) return;
        try {
            const status = await api.startTunnel();
            if (status.error) setTunnelError(status.error);
            else setTunnelActive(true);
        } catch (err) {
            setTunnelError(err instanceof Error ? err.message : 'Could not start mobile access');
        }
    }, [tunnelActive]);

    const stopMobileAccess = useCallback(async () => {
        try {
            await api.stopTunnel();
            setTunnelActive(false);
            setShowMobileAccess(false);
        } catch (err) {
            setTunnelError(err instanceof Error ? err.message : 'Could not stop mobile access');
        }
    }, []);

    const isMobile = typeof window !== 'undefined' && window.innerWidth <= 768;

    if (setup.state === 'needed') {
        return (
            <div className="app">
                <SetupScreen initial={setup.check} onReady={() => { setSetup({ state: 'ready' }); void checkSystemStatus(); }} />
            </div>
        );
    }

    const main = showMemoryDashboard ? (
        <Suspense fallback={<Loading />}><MemoryDashboard /></Suspense>
    ) : showSchedules ? (
        <Suspense fallback={<Loading />}><ScheduleManager /></Suspense>
    ) : currentSessionId ? (
        <ChatView key={currentSessionId} sessionId={currentSessionId} />
    ) : (
        <NewChat onSend={startChat} />
    );

    return (
        <div className="app">
            {!sidebarOpen && (
                <button className="sidebar-toggle" onClick={() => setSidebarOpen(true)} aria-label="Open sidebar">
                    <Menu size={18} />
                </button>
            )}
            <div
                className={`sidebar-overlay ${sidebarOpen ? 'visible' : ''}`}
                onClick={() => setSidebarOpen(false)}
                aria-hidden="true"
            />

            <Sidebar
                onNewChat={newChat}
                onMobileAccess={isMobile ? undefined : () => void openMobileAccess()}
                tunnelActive={tunnelActive}
            />

            <div className="main-content">
                {setup.state === 'ready' && <ConnectionBanner onRetry={checkSystemStatus} />}
                {setup.state === 'unknown' && (
                    <div className="app-notice is-warning" role="status">
                        <span>Could not check whether GAIA is set up: {setup.error}</span>
                        <button type="button" className="link-btn" onClick={() => void checkSetup()}>Check again</button>
                    </div>
                )}
                {restoreNotice && (
                    <div className={`app-notice${restoreNotice.ok ? '' : ' is-warning'}`} role="status">
                        <span>{restoreNotice.message}</span>
                        {!restoreNotice.ok && (
                            <button type="button" className="link-btn" onClick={() => { dismissRestoreNotice(); openSettings('model'); }}>
                                Pick a model
                            </button>
                        )}
                        <button type="button" className="app-notice-close" onClick={dismissRestoreNotice} aria-label="Dismiss">
                            <X size={14} />
                        </button>
                    </div>
                )}
                {main}
            </div>

            {showDocLibrary && <Suspense fallback={null}><DocumentLibrary /></Suspense>}
            {showFileBrowser && <Suspense fallback={null}><FileBrowser /></Suspense>}
            {showNotificationPanel && (
                <div className="notification-center-popover">
                    <NotificationCenter onClose={() => setShowNotificationPanel(false)} />
                </div>
            )}
            {showMobileAccess && (
                <Suspense fallback={null}>
                    <MobileAccessModal
                        isOpen={showMobileAccess}
                        onClose={() => setShowMobileAccess(false)}
                        onStop={() => void stopMobileAccess()}
                        error={tunnelError}
                    />
                </Suspense>
            )}
            {settingsSection && <SettingsDialog onUseSkill={useSkill} />}
            <UpdateIndicator />
            {createError && <div className="toast" role="alert">{createError}</div>}
        </div>
    );
}

export default App;
