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
import { FlagshipAgentNotice } from './components/FlagshipAgentNotice';
import { useChatStore } from './stores/chatStore';
import { useModelStore } from './stores/modelStore';
import { useNotificationStore } from './stores/notificationStore';
import { useScheduleStore } from './stores/scheduleStore';
import { useTrayBridge } from './hooks/useTrayBridge';
import { useChatAlerts } from './hooks/useChatAlerts';
import { startActiveRunsPoll } from './services/activeRuns';
import { startQueueDispatcher } from './services/queueDispatch';
import * as api from './services/api';
import { createStatusPoller, type StatusOutcome } from './services/statusPoll';
import { log, logBanner } from './utils/logger';
import { announceMobileAccess, useMobileAccessRequests } from './utils/mobileAccessEvents';
import { getSessionHash } from './utils/format';
import { readUrlTarget, resolveUrlNavTarget } from './utils/sessionNav';
import { getApiBase } from './utils/apiBase';
import { cleanupAbandonedDraft, isAbandonedDraft } from './utils/sessionCleanup';
import { FLAGSHIP_AGENT_ID, hasFlagshipAgent, isFlagshipMissingError } from './utils/newTask';
import { runWhileVisible } from './utils/visibleInterval';
import { retryUntilOk, type Retrier } from './utils/retry';
import type { SetupCheck, StartCard } from './types';

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
/** A backend that has answered no status request for this long is treated as gone. */
const BACKEND_SILENT_LOST_MS = 5 * 60_000;
const RESTORE_NOTICE_MS = 6000;

function Loading() {
    return <div className="loading-spinner" role="status" aria-label="Loading" />;
}

// First failure, then every 10th (~5 min at the 30 s cap), so a stuck read stays in the log.
const shouldLogRetry = (attempt: number) => attempt === 1 || attempt % 10 === 0;

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
    const agents = useChatStore((s) => s.agents);
    const setPendingPrompt = useChatStore((s) => s.setPendingPrompt);
    const setDefaultPermissionMode = useChatStore((s) => s.setDefaultPermissionMode);
    const openSettings = useChatStore((s) => s.openSettings);
    const settingsSection = useChatStore((s) => s.settingsSection);
    const syncSystemTheme = useChatStore((s) => s.syncSystemTheme);
    const restoreNotice = useModelStore((s) => s.restoreNotice);
    const dismissRestoreNotice = useModelStore((s) => s.dismissRestoreNotice);
    const showNotificationPanel = useNotificationStore((s) => s.showPanel);
    const initialized = useChatStore((s) => s.systemStatus?.initialized === true);
    const setShowNotificationPanel = useNotificationStore((s) => s.setShowPanel);

    // A successful restore is news for a moment; a failed one waits for the user to pick a model.
    useEffect(() => {
        if (!restoreNotice?.ok) return;
        const t = setTimeout(dismissRestoreNotice, RESTORE_NOTICE_MS);
        return () => clearTimeout(t);
    }, [restoreNotice, dismissRestoreNotice]);

    const [setup, setSetup] = useState<SetupGate>({ state: 'checking' });
    const setupRef = useRef(setup);
    setupRef.current = setup;
    // The agent list has loaded at least once; until then "no flagship" is unknown, not missing.
    const [agentsKnown, setAgentsKnown] = useState(false);
    const agentsKnownRef = useRef(agentsKnown);
    agentsKnownRef.current = agentsKnown;
    // A new chat was refused because the flagship is not installed.
    const [flagshipRefused, setFlagshipRefused] = useState(false);
    const flagshipMissing = flagshipRefused || (agentsKnown && !hasFlagshipAgent(agents));
    const flagshipMissingRef = useRef(flagshipMissing);
    flagshipMissingRef.current = flagshipMissing;
    const [createError, setCreateError] = useState<string | null>(null);
    // A task picked at the end of setup that needs the user to finish typing it.
    const [draftText, setDraftText] = useState('');
    const [newChatFocus, setNewChatFocus] = useState(0);
    const [showMobileAccess, setShowMobileAccess] = useState(false);
    const [tunnelActive, setTunnelActive] = useState(false);
    const [tunnelError, setTunnelError] = useState<string | null>(null);

    // ── First run: is GAIA set up? Same question the TUI asks. ──────────
    // Quiet: keep the current screen until the answer, so a re-check never blanks the app.
    const checkSetup = useCallback(async (quiet = false) => {
        if (!quiet) setSetup({ state: 'checking' });
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

    // Only the latest read may set the list; an older one in flight predates whatever setup installed.
    const agentsReadSeq = useRef(0);
    const loadAgents = useCallback(async () => {
        const seq = ++agentsReadSeq.current;
        const list = (await api.listAgents()).agents || [];
        if (seq !== agentsReadSeq.current) return;
        setAgents(list);
        setAgentsKnown(true);
        if (hasFlagshipAgent(list)) setFlagshipRefused(false);
    }, [setAgents]);
    // Setup just installed the agent: the list read at startup predates it, so say nothing until re-read.
    const reloadAgentsAfterSetup = useCallback(() => {
        const wasKnown = agentsKnownRef.current;
        setAgentsKnown(false);
        loadAgents().catch((err) => {
            log.api.warn('Agent list unavailable after setup', err);
            if (wasKnown) setAgentsKnown(true);
        });
    }, [loadAgents]);
    // The agent may be installed from the CLI while the app is open; re-check before claiming it isn't.
    useEffect(() => {
        const onFocus = () => {
            if (!flagshipMissingRef.current) return;
            loadAgents().catch((err) => log.api.warn('Agent list unavailable', err));
        };
        window.addEventListener('focus', onFocus);
        return () => window.removeEventListener('focus', onFocus);
    }, [loadAgents]);
    // A new chat starts in the configured mode (`full_access` in config).
    const loadDefaultPermissionMode = useCallback(async () => {
        setDefaultPermissionMode((await api.listAllPermissions()).default_mode);
    }, [setDefaultPermissionMode]);

    // The desktop window loads before the backend listens, so these retry until answered.
    const startupLoads = useRef<Retrier[]>([]);
    useEffect(() => {
        logBanner(__APP_VERSION__);
        void checkSetup();
        const loads = [
            retryUntilOk(loadAgents, {
                onError: (err, n) => { if (shouldLogRetry(n)) log.api.warn(`Agent list unavailable (attempt ${n}); retrying`, err); },
            }),
            retryUntilOk(loadDefaultPermissionMode, {
                onError: (err, n) => { if (shouldLogRetry(n)) log.api.warn(`Default permission mode unavailable (attempt ${n}); retrying`, err); },
            }),
        ];
        startupLoads.current = loads;
        return () => loads.forEach((l) => l.stop());
    }, [checkSetup, loadAgents, loadDefaultPermissionMode]);

    // The backend answered after being unreachable: read now what startup could not.
    const onBackendBack = useCallback(() => {
        startupLoads.current.forEach((l) => l.kick());
        useModelStore.getState().retryNow();
        if (setupRef.current.state === 'unknown') void checkSetup(true);
    }, [checkSetup]);
    const onBackendBackRef = useRef(onBackendBack);
    onBackendBackRef.current = onBackendBack;

    // ── Backend and model-server health ─────────────────────────────────
    const failCount = useRef(0);
    const slowCount = useRef(0);
    const slowSince = useRef<number | null>(null);
    const wasUnreachable = useRef(false);
    // A backend that answers late is busy, not gone; only a refused request means it is unreachable.
    const [backendSlow, setBackendSlow] = useState(false);
    // The backend answered the status request with an HTTP error: reachable, but failing.
    const [backendError, setBackendError] = useState<string | null>(null);
    const applyStatus = useCallback((outcome: StatusOutcome) => {
        if (outcome.kind === 'unreachable') {
            log.system.warn('System status check failed', outcome.error);
            wasUnreachable.current = true;
            slowCount.current = 0;
            slowSince.current = null;
            setBackendSlow(false);
            setBackendError(null);
            setBackendConnected(false);
            setSystemStatus(null);
            return;
        }
        if (outcome.kind === 'error') {
            log.system.warn(`System status answered HTTP ${outcome.httpStatus}`, outcome.message);
            if (wasUnreachable.current) onBackendBackRef.current();
            wasUnreachable.current = false;
            slowCount.current = 0;
            slowSince.current = null;
            setBackendSlow(false);
            setBackendError(`HTTP ${outcome.httpStatus}: ${outcome.message}`);
            setBackendConnected(true);
            return;
        }
        if (outcome.kind === 'slow') {
            slowCount.current += 1;
            slowSince.current ??= Date.now();
            setBackendError(null);
            log.system.warn(`System status did not answer in time (${slowCount.current} in a row)`);
            if (Date.now() - slowSince.current >= BACKEND_SILENT_LOST_MS) {
                // Busy does not last this long; one that never answers is as good as gone.
                setBackendSlow(false);
                setBackendConnected(false);
                setSystemStatus(null);
                return;
            }
            // Something accepted the request, so the backend is back, if slow. Fresh
            // from an outage, say so at once rather than show nothing until it answers.
            setBackendConnected(true);
            if (wasUnreachable.current || slowCount.current >= LEMONADE_FAIL_THRESHOLD) setBackendSlow(true);
            return;
        }
        const { status } = outcome;
        if (wasUnreachable.current) onBackendBackRef.current();
        wasUnreachable.current = false;
        slowCount.current = 0;
        slowSince.current = null;
        setBackendSlow(false);
        setBackendError(null);
        setBackendConnected(true);
        // One slow health probe under load is not an outage.
        failCount.current = status.lemonade_running ? 0 : failCount.current + 1;
        const prev = useChatStore.getState().systemStatus;
        if (status.lemonade_running || failCount.current >= LEMONADE_FAIL_THRESHOLD || !prev?.lemonade_running) {
            setSystemStatus(status);
        } else {
            setSystemStatus({ ...prev, download_progress: status.download_progress });
        }
    }, [setBackendConnected, setSystemStatus]);
    const applyStatusRef = useRef(applyStatus);
    applyStatusRef.current = applyStatus;
    const [statusPoller] = useState(() => createStatusPoller((outcome) => applyStatusRef.current(outcome)));
    const checkSystemStatus = statusPoller.check;

    useEffect(() => runWhileVisible(statusPoller.start), [statusPoller]);

    // ── Chats ───────────────────────────────────────────────────────────
    const sessionFingerprint = useRef('');
    useEffect(() => {
        const load = (initial: boolean) => {
            api.listSessions()
                .then((data) => {
                    const list = data.sessions || [];
                    // Never wipe a populated list on a transient empty reply.
                    if (!initial && list.length === 0 && useChatStore.getState().sessions.length > 0) return;
                    // Includes the model and inference location, so a model change reaches the indicators.
                    const fp = list
                        .map((s) => `${s.id}|${s.updated_at}|${s.title}|${s.inference_remote}|${s.inference_provider}|${s.inference_description}|${s.effective_model}|${s.unread}|${s.pinned}|${s.last_run_status}|${s.kind}`)
                        .join('\n');
                    if (fp === sessionFingerprint.current) return;
                    sessionFingerprint.current = fp;
                    setSessions(list);
                    // Another window or the TUI switched models: re-read it, or the footer and picker go stale.
                    const picked = useModelStore.getState().active?.model;
                    if (picked && list.some((s) => s.effective_model && s.effective_model !== picked)) {
                        void useModelStore.getState().refresh();
                    }
                })
                .catch((err) => { if (initial) log.system.error('Could not load chats', err); });
        };
        load(true);
        const t = setInterval(() => load(false), 5_000);
        return () => clearInterval(t);
    }, [setSessions]);

    useEffect(() => startActiveRunsPoll(), []);
    // Schedules run unattended: a failed run raises a notification even with the panel closed.
    useEffect(() => {
        const load = () => { void useScheduleStore.getState().loadSchedules(); };
        load();
        const t = setInterval(load, 30_000);
        return () => clearInterval(t);
    }, []);
    // Queued messages go out when their chat's turn ends, even with another chat open.
    useEffect(() => startQueueDispatcher(), []);

    useChatAlerts();

    // Open a chat named in the URL (?session= or #hash), on load and on back/forward.
    const urlResolved = useRef(false);
    useEffect(() => {
        const navigate = () => {
            const target = readUrlTarget(window.location);
            const { currentSessionId: cur, sessions } = useChatStore.getState();
            const id = resolveUrlNavTarget(target, cur, sessions);
            if (id) {
                setCurrentSession(id);
                setMessages([]);
            }
            return id;
        };
        // The link can only resolve once the chat list has loaded.
        const first = () => {
            if (urlResolved.current) return;
            urlResolved.current = true;
            // A link to a deleted chat: drop it, as the hash-sync effect would.
            if (!navigate() && !useChatStore.getState().currentSessionId && window.location.hash) {
                window.history.replaceState(null, '', window.location.pathname + window.location.search);
            }
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
        let store = useChatStore.getState();
        let session;
        try {
            session = await api.createSession({
                title: 'New Task',
                agent_type: FLAGSHIP_AGENT_ID,
                document_ids: store.draftDocumentIds,
                private: store.draftPrivate,
            });
        } catch (err) {
            if (!isFlagshipMissingError(err)) throw err;
            setFlagshipRefused(true);
            throw new Error("The GAIA agent isn't installed. Install it from the notice above, then send again.");
        }
        if (flagshipMissingRef.current) {
            // Created after all: the agent list was stale (installed from the CLI meanwhile).
            setFlagshipRefused(false);
            void loadAgents().catch((err) => log.api.warn('Agent list unavailable', err));
        }
        // The chip may still show a guess if startup never reached the backend; the saved default wins.
        if (!store.defaultPermissionModeKnown) {
            await loadDefaultPermissionMode()
                .catch((err) => log.api.warn("Default permission mode unavailable; using the chip's mode", err));
            store = useChatStore.getState();
        }
        store.clearDraftDocuments();
        store.setDraftPrivate(false);
        addSession(session);
        setCurrentSession(session.id);
        setMessages([]);
        setDraftText('');
        // Always sent, so the chat runs in exactly the mode its chip showed.
        try {
            await api.setPermissionMode(session.id, store.draftPermissionMode);
        } catch (err) {
            setCreateError(
                `The chat was created, but its permission mode could not be set, so your message was not sent: ${
                    err instanceof Error ? err.message : err}`,
            );
            return false;
        }
        store.resetDraftPermissionMode();
        setPendingPrompt(text);
        return true;
    }, [addSession, setCurrentSession, setMessages, setPendingPrompt, loadAgents, loadDefaultPermissionMode]);

    const newChat = useCallback(() => {
        const store = useChatStore.getState();
        const outgoing = store.currentSessionId;
        if (outgoing && isAbandonedDraft(outgoing, store)) void cleanupAbandonedDraft(outgoing);
        store.setShowMemoryDashboard(false);
        store.setShowSchedules(false);
        setCurrentSession(null);
        setMessages([]);
        setNewChatFocus((n) => n + 1);
    }, [setCurrentSession, setMessages]);

    // ── Desktop tray: its icon follows this window; its menu acts here ──
    const openChatFromTray = useCallback((id: string) => {
        const store = useChatStore.getState();
        store.closeSettings();
        if (id !== store.currentSessionId) {
            void cleanupAbandonedDraft(store.currentSessionId);
            setMessages([]);
        }
        setCurrentSession(id);
    }, [setCurrentSession, setMessages]);
    const newChatFromTray = useCallback(() => {
        useChatStore.getState().closeSettings();
        newChat();
    }, [newChat]);
    useTrayBridge({ openChat: openChatFromTray, newChat: newChatFromTray, refreshStatus: () => void checkSystemStatus() });

    const useSkill = useCallback((name: string) => {
        newChat();
        startChat(`Load the \`${name}\` skill, then tell me briefly what it lets you do.`)
            .catch((err) => setCreateError(err instanceof Error ? err.message : String(err)));
    }, [newChat, startChat]);

    const finishSetup = useCallback((first?: StartCard) => {
        setSetup({ state: 'ready' });
        reloadAgentsAfterSetup();
        void checkSystemStatus();
        if (!first) return;
        if (first.needs_input) {
            setDraftText(first.prompt);
            return;
        }
        startChat(first.prompt).catch((err) => setCreateError(err instanceof Error ? err.message : String(err)));
    }, [checkSystemStatus, startChat, reloadAgentsAfterSetup]);

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

    // The session panel's Phone access toggle drives the same flow.
    useMobileAccessRequests((on) => { void (on ? openMobileAccess() : stopMobileAccess()); });
    useEffect(() => { announceMobileAccess(tunnelActive); }, [tunnelActive]);

    const isMobile = typeof window !== 'undefined' && window.innerWidth <= 768;

    // A PC set up before can chat while the check runs; a new one waits, so
    // the chat screen never appears and is then replaced by setup.
    if (setup.state === 'checking' && !initialized) {
        return (
            <div className="app">
                <main className="startup" role="status" aria-label="Starting GAIA">
                    <div className="loading-spinner" aria-hidden="true" />
                    <p className="startup-text">Starting GAIA…</p>
                </main>
            </div>
        );
    }

    if (setup.state === 'needed') {
        return (
            <div className="app">
                <SetupScreen initial={setup.check} onReady={finishSetup} />
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
        <NewChat key={draftText} onSend={startChat} initialText={draftText} focusRequest={newChatFocus} />
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
                {setup.state === 'ready' && <ConnectionBanner onRetry={checkSystemStatus} backendSlow={backendSlow} backendError={backendError} />}
                {setup.state === 'unknown' && (
                    <div className="app-notice is-warning" role="status">
                        <span>Could not check whether GAIA is set up: {setup.error}</span>
                        <button type="button" className="link-btn" onClick={() => void checkSetup()}>Check again</button>
                    </div>
                )}
                {flagshipMissing && <FlagshipAgentNotice onInstalled={loadAgents} />}
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
