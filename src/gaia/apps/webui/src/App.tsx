// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { Menu, X } from 'lucide-react';
import { Sidebar } from './components/Sidebar';
import { ChatView } from './components/ChatView';
import { NewChat } from './components/NewChat';
import { ModelNeededDialog } from './components/ModelNeededDialog';
import { LogsDialog } from './components/LogsDialog';
import { SetupScreen } from './components/SetupScreen';
import { ensureModel } from './stores/modelNeededStore';
import { SettingsDialog } from './components/settings/SettingsDialog';
import { buildPersonalizePrompt, PERSONALIZE_EVENT, type PersonalizeRequest } from './utils/connectorPersonalize';
import { ErrorBoundary } from './components/ErrorBoundary';
import { ConnectionBanner } from './components/ConnectionBanner';
import { UpdateIndicator } from './components/UpdateIndicator';
import { DownloadChip } from './components/setup/DownloadChip';
import { NotificationCenter } from './components/NotificationCenter';
import { FlagshipAgentNotice } from './components/FlagshipAgentNotice';
import { useChatStore } from './stores/chatStore';
import { useModelStore } from './stores/modelStore';
import { useNotificationStore } from './stores/notificationStore';
import { useScheduleStore } from './stores/scheduleStore';
import { useGoalHealthStore } from './stores/goalHealthStore';
import { useTrayBridge } from './hooks/useTrayBridge';
import { useChatAlerts } from './hooks/useChatAlerts';
import { useMediaQuery } from './hooks/useMediaQuery';
import { startActiveRunsPoll } from './services/activeRuns';
import { startQueueDispatcher } from './services/queueDispatch';
import * as api from './services/api';
import { createStatusPoller, type StatusOutcome } from './services/statusPoll';
import { log, logBanner } from './utils/logger';
import { announceMobileAccess, useMobileAccessRequests } from './utils/mobileAccessEvents';
import { getSessionHash } from './utils/format';
import { lastSessionTarget, readUrlTarget, rememberLastSession, resolveUrlNavTarget } from './utils/sessionNav';
import { NEW_CHAT_DRAFT_KEY, chatDraftKey, clearSavedDraft, readSavedDraft, writeSavedDraft } from './utils/savedDraft';
import { getApiBase } from './utils/apiBase';
import { followBackendLifecycle, resyncBackend } from './stores/backendStore';
import { cleanupAbandonedDraft, isAbandonedDraft } from './utils/sessionCleanup';
import { FLAGSHIP_AGENT_ID, hasFlagshipAgent, isFlagshipMissingError } from './utils/newTask';
import { runWhileVisible } from './utils/visibleInterval';
import { RETRY_INITIAL_MS, retryUntilOk, type Retrier } from './utils/retry';
import { shouldShowStartupScreen } from './utils/startupGate';
import { getT, useT } from './i18n/useT';
import type { SetupCheck, StartCard } from './types';

// Heavy, occasional screens load on first use.
const MemoryDashboard = lazy(() => import('./components/MemoryDashboard').then((m) => ({ default: m.MemoryDashboard })));
const ScheduleManager = lazy(() => import('./components/ScheduleManager').then((m) => ({ default: m.ScheduleManager })));
const DocumentLibrary = lazy(() => import('./components/DocumentLibrary').then((m) => ({ default: m.DocumentLibrary })));
const FileBrowser = lazy(() => import('./components/FileBrowser').then((m) => ({ default: m.FileBrowser })));
const MobileAccessModal = lazy(() => import('./components/MobileAccessModal').then((m) => ({ default: m.MobileAccessModal })));

/** `checking` waits for readiness; `upgrading` blocks while an embedded server is replaced;
 * `needed` is the actual first-run flow. */
type SetupGate =
    | { state: 'checking' }
    | { state: 'upgrading' }
    | { state: 'ready' }
    | { state: 'needed'; check: SetupCheck }
    /** Set up before, but its model server isn't answering: the app, with a notice. */
    | { state: 'serverDown' }
    | { state: 'addingLocal' }
    /** `upgradeFailed`: the check answered, but updating GAIA's own model server failed. */
    | { state: 'unknown'; error: string; upgradeFailed?: boolean };

const LEMONADE_FAIL_THRESHOLD = 3;
/** A backend that has answered no status request for this long is treated as gone. */
const BACKEND_SILENT_LOST_MS = 5 * 60_000;
const RESTORE_NOTICE_MS = 6000;
/** How long a refused setup check counts as "still starting"; about twice the desktop app's own startup limit. */
const BACKEND_START_WAIT_MS = 60_000;

// fetch rejects with a TypeError only when no response arrived: nothing is listening yet.
const isRefused = (err: unknown) => err instanceof TypeError;
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const UPGRADE_WAIT_MS = 5 * 60_000;

/** The embedded server's update ended in failure, as opposed to still running. */
class UpgradeFailed extends Error {}

/** The model in use could not be read, so a setup check can't be trusted to say "not set up". */
class ModelUnread extends Error {
    constructor(reason: string) {
        super(getT().bare('setup.unknown.modelUnread', { error: reason }));
    }
}

async function waitForEmbeddedUpgrade(): Promise<void> {
    const deadline = Date.now() + UPGRADE_WAIT_MS;
    for (;;) {
        const status = await api.getSetupStatus();
        if (status.state === 'ready') return;
        if (status.state === 'failed' || status.state === 'cancelled') {
            throw new UpgradeFailed(status.error || getT().bare('setup.upgrading.stopped'));
        }
        if (Date.now() >= deadline) {
            throw new Error(getT().bare('setup.upgrading.slow'));
        }
        await sleep(RETRY_INITIAL_MS);
    }
}

function Loading() {
    return <div className="loading-spinner" role="status" aria-label="Loading" />;
}

// First failure, then every 10th (~5 min at the 30 s cap), so a stuck read stays in the log.
const shouldLogRetry = (attempt: number) => attempt === 1 || attempt % 10 === 0;

function App() {
    const t = useT();
    const currentSessionId = useChatStore((s) => s.currentSessionId);
    const setSessions = useChatStore((s) => s.setSessions);
    const setCurrentSession = useChatStore((s) => s.setCurrentSession);
    const addSession = useChatStore((s) => s.addSession);
    const setMessages = useChatStore((s) => s.setMessages);
    const showDocLibrary = useChatStore((s) => s.showDocLibrary);
    const showFileBrowser = useChatStore((s) => s.showFileBrowser);
    const showMemoryDashboard = useChatStore((s) => s.showMemoryDashboard);
    const showSchedules = useChatStore((s) => s.showSchedules);
    const sidebarOpen = useChatStore((s) => s.sidebarOpen);
    const sidebarCollapsed = useChatStore((s) => s.sidebarCollapsed);
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
    const mainUiShownRef = useRef(false);
    const setShowNotificationPanel = useNotificationStore((s) => s.setShowPanel);

    // A successful restore is news for a moment; a failed one waits for the user to pick a model.
    useEffect(() => {
        if (!restoreNotice?.ok) return;
        const t = setTimeout(dismissRestoreNotice, RESTORE_NOTICE_MS);
        return () => clearTimeout(t);
    }, [restoreNotice, dismissRestoreNotice]);

    const [setup, setSetup] = useState<SetupGate>({ state: 'checking' });
    const setupAgain = useChatStore((s) => s.setupAgain);
    const runSetupAgain = useChatStore((s) => s.runSetupAgain);
    const setupRef = useRef(setup);
    setupRef.current = setup;
    // The agent list has loaded at least once; until then "no flagship" is unknown, not missing.
    const [agentsKnown, setAgentsKnown] = useState(false);
    const agentsKnownRef = useRef(agentsKnown);
    agentsKnownRef.current = agentsKnown;
    // A new chat was refused because the flagship is not installed.
    const [flagshipRefused, setFlagshipRefused] = useState(false);
    // The setup check found no GAIA agent build for this PC: nothing to install.
    const [noAgentBuild, setNoAgentBuild] = useState(false);
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
    const [tunnelSetup, setTunnelSetup] = useState<string[] | null>(null);

    // Set once the status poller and chat list exist further down.
    const refreshStatus = useRef<() => Promise<void>>(async () => {});
    const reloadSessions = useRef<() => Promise<void>>(async () => {});

    // ── First run: is GAIA set up? Same question the TUI asks. ──────────
    // Quiet: keep the current screen until the answer, so a re-check never blanks the app.
    // waitForStart: only the launch check treats a refused connection as "still starting".
    const checkSetup = useCallback(async (quiet = false, waitForStart = false) => {
        if (!quiet) setSetup({ state: 'checking' });
        let waited = 0;
        try {
            for (;;) {
                await useModelStore.getState().refresh();
                const active = useModelStore.getState().active;
                const remote = !!active?.remote;
                let check: SetupCheck;
                try {
                    check = await api.checkSetup(remote);
                    // Asked without the model, the answer is a local model's: a cloud install would read as not set up.
                    if (!active && !check.ready) {
                        throw new ModelUnread(useModelStore.getState().error ?? 'no answer');
                    }
                } catch (err) {
                    const starting = isRefused(err) || err instanceof ModelUnread;
                    if (!waitForStart || !starting || waited >= BACKEND_START_WAIT_MS) throw err;
                    // A refused local connection is cheap; poll steadily so the app opens as soon as it listens.
                    await sleep(RETRY_INITIAL_MS);
                    waited += RETRY_INITIAL_MS;
                    continue;
                }
                // A new pinned embedded server is an update, not first-run setup: the models stay.
                if (!check.ready && check.stage === 'upgrade') {
                    setSetup({ state: 'upgrading' });
                    // A re-check after the wait timed out finds that update still going: follow it.
                    const run = await api.getSetupStatus();
                    const updating = run.upgrade_embedded && (run.state === 'running' || run.state === 'verifying');
                    if (!updating) await api.startSetup(remote, false, { upgradeEmbedded: true });
                    await waitForEmbeddedUpgrade();
                    check = await api.checkSetup(remote);
                }
                // Read status and chats before showing the app, so it never flashes "lost connection" or "no chats".
                if (waited > 0) await Promise.all([refreshStatus.current(), reloadSessions.current()]);
                setNoAgentBuild(!!check.no_agent_build);
                // Seen ready before: an unreachable server is an outage, not a first run.
                const serverDown = !check.ready && check.stage === 'server' && !!check.initialized;
                setSetup(check.ready ? { state: 'ready' } : serverDown ? { state: 'serverDown' } : { state: 'needed', check });
                return;
            }
        } catch (err) {
            // Could not ask — not the same as "not set up"; the chat still works if it is.
            const error = err instanceof Error ? err.message : String(err);
            if (err instanceof UpgradeFailed) {
                log.system.warn('Updating the local model server failed', err);
                setSetup({ state: 'unknown', error, upgradeFailed: true });
                return;
            }
            log.system.warn('Setup check could not be answered', err);
            setSetup({ state: 'unknown', error });
        }
    }, []);

    // The status poll sees the model server again: ask whether GAIA is ready now.
    const lemonadeRunning = useChatStore((s) => s.systemStatus?.lemonade_running === true);
    useEffect(() => {
        if (setup.state === 'serverDown' && lemonadeRunning) void checkSetup(true);
    }, [setup.state, lemonadeRunning, checkSetup]);

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
        void checkSetup(false, true);
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
        void reloadSessions.current();
        // Not after a failed update: reconnecting must not start another one unasked.
        const gate = setupRef.current;
        if (gate.state === 'unknown' && !gate.upgradeFailed) void checkSetup(true);
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
            // The backend may be back on another port; the next check goes wherever it is.
            resyncBackend();
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
    refreshStatus.current = checkSystemStatus;

    useEffect(() => runWhileVisible(statusPoller.start), [statusPoller]);
    // The desktop app restarts a crashed backend on a new port; re-check once it's back.
    useEffect(() => followBackendLifecycle(() => { void checkSystemStatus(); }), [checkSystemStatus]);

    // ── Chats ───────────────────────────────────────────────────────────
    const sessionFingerprint = useRef('');
    useEffect(() => {
        const load = (initial: boolean) =>
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
        reloadSessions.current = () => load(false);
        void load(true);
        const t = setInterval(() => void load(false), 5_000);
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
    // Goals also run unattended: an unreadable goal store raises one notification.
    useEffect(() => {
        const check = () => { void useGoalHealthStore.getState().check(); };
        check();
        const t = setInterval(check, 30_000);
        return () => clearInterval(t);
    }, []);
    // Queued messages go out when their chat's turn ends, even with another chat open.
    useEffect(() => startQueueDispatcher(), []);

    useChatAlerts();

    // Open a chat named in the URL (?session= or #hash), on load and on back/forward.
    const urlResolved = useRef(false);
    const choseNewChatRef = useRef(false);
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
            if (navigate() || useChatStore.getState().currentSessionId) return;
            if (window.location.hash) {
                window.history.replaceState(null, '', window.location.pathname + window.location.search);
                return;
            }
            // No link: reopen the chat that was open when the app last closed —
            // unless the user already chose a new chat, or was composing one.
            if (choseNewChatRef.current || readSavedDraft(NEW_CHAT_DRAFT_KEY)) return;
            const { sessions } = useChatStore.getState();
            const last = resolveUrlNavTarget(lastSessionTarget(), null, sessions);
            if (last) {
                setCurrentSession(last);
                setMessages([]);
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
        if (urlResolved.current) rememberLastSession(currentSessionId);
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
        if (!(await ensureModel('chat'))) {
            setDraftText(text);
            return false;
        }
        let store = useChatStore.getState();
        let session;
        try {
            session = await api.createSession({
                title: 'New chat',
                agent_type: FLAGSHIP_AGENT_ID,
                document_ids: store.draftDocumentIds,
                private: store.draftPrivate,
                folders: store.draftFolder ? [store.draftFolder] : [],
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
        store.setDraftFolder(null);
        // Always sent, so the chat runs in exactly the mode its chip showed.
        // Set before the chat opens, so a refused message can wait in its box.
        let modeError: unknown = null;
        try {
            await api.setPermissionMode(session.id, store.draftPermissionMode);
        } catch (err) {
            modeError = err;
        }
        // The message now belongs to the chat: sent below, or waiting in its box.
        clearSavedDraft(NEW_CHAT_DRAFT_KEY);
        if (modeError && !session.private) writeSavedDraft(chatDraftKey(session.id), text);
        addSession(session);
        setCurrentSession(session.id);
        setMessages([]);
        setDraftText('');
        if (modeError) {
            setCreateError(
                `The chat was created, but its permission mode could not be set, so your message was not sent${
                    session.private ? '' : " — it is in the chat's box"}: ${modeError instanceof Error ? modeError.message : modeError}`,
            );
            return false;
        }
        store.resetDraftPermissionMode();
        setPendingPrompt({ sessionId: session.id, text });
        return true;
    }, [addSession, setCurrentSession, setMessages, setPendingPrompt, loadAgents, loadDefaultPermissionMode]);

    const newChat = useCallback(() => {
        const store = useChatStore.getState();
        const outgoing = store.currentSessionId;
        // A first message the chat had not sent yet comes back to the new chat's box.
        const unsent = store.pendingPrompt?.sessionId === outgoing ? store.pendingPrompt : null;
        if (unsent) {
            store.setPendingPrompt(null);
            setDraftText(unsent.text);
        }
        if (outgoing && isAbandonedDraft(outgoing, useChatStore.getState())) void cleanupAbandonedDraft(outgoing);
        store.setShowMemoryDashboard(false);
        store.setShowSchedules(false);
        choseNewChatRef.current = true;
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

    useEffect(() => {
        const onPersonalize = (e: Event) => {
            const req = (e as CustomEvent<PersonalizeRequest>).detail;
            useChatStore.getState().closeSettings();
            newChat();
            startChat(buildPersonalizePrompt(req))
                .catch((err) => setCreateError(err instanceof Error ? err.message : String(err)));
        };
        window.addEventListener(PERSONALIZE_EVENT, onPersonalize);
        return () => window.removeEventListener(PERSONALIZE_EVENT, onPersonalize);
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

    const leaveLocalSetup = useCallback(() => {
        setSetup({ state: 'ready' });
        void checkSystemStatus();
    }, [checkSystemStatus]);

    useEffect(() => {
        const open = () => {
            useChatStore.getState().closeSettings();
            setSetup({ state: 'addingLocal' });
        };
        window.addEventListener('gaia:set-up-local', open);
        return () => window.removeEventListener('gaia:set-up-local', open);
    }, []);

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

    // ── Mobile access ──────────────────────────────────────────────────
    useEffect(() => {
        api.getTunnelStatus()
            .then((s) => setTunnelActive(s.active === true))
            .catch((err) => log.system.debug('Mobile access unavailable', err));
    }, []);

    const openMobileAccess = useCallback(async () => {
        setShowMobileAccess(true);
        setTunnelError(null);
        if (tunnelActive) {
            setTunnelSetup(null);
            return;
        }
        try {
            const status = await api.startTunnel();
            // Kept until the retry answers, so the setup panel doesn't flash to "Starting".
            const notInstalled = status.reason === 'ngrok_not_installed';
            setTunnelSetup(notInstalled ? status.installCommands ?? [] : null);
            if (notInstalled) return;
            if (status.error) setTunnelError(status.error);
            else setTunnelActive(true);
        } catch (err) {
            setTunnelSetup(null);
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

    const isMobile = useMediaQuery('(max-width: 768px)');

    // A transient status loss must not replace a UI that was already shown.
    const showStartup = shouldShowStartupScreen(
        setup.state === 'checking',
        initialized,
        mainUiShownRef.current,
    );
    if (!showStartup && setup.state !== 'needed') mainUiShownRef.current = true;

    // A PC set up before can chat while the check runs; a new one waits, so
    // the chat screen never appears and is then replaced by setup.
    if (showStartup) {
        return (
            <div className="app">
                <main className="startup" role="status" aria-label="Starting GAIA">
                    <div className="loading-spinner" aria-hidden="true" />
                    <p className="startup-text">Starting GAIA…</p>
                </main>
            </div>
        );
    }

    if (setup.state === 'upgrading') {
        return (
            <div className="app">
                <main className="startup" role="status" aria-live="polite" aria-label={t('setup.upgrading.notice')}>
                    <div className="loading-spinner" aria-hidden="true" />
                    <p className="startup-text">{t('setup.upgrading.notice')}</p>
                </main>
            </div>
        );
    }

    if (setup.state === 'needed' || setup.state === 'addingLocal' || setupAgain) {
        // Settings can open setup two ways; closing it goes back the way it came.
        const close = setupAgain
            ? () => runSetupAgain(false)
            : setup.state === 'addingLocal' ? leaveLocalSetup : undefined;
        return (
            <div className="app">
                <SetupScreen
                    onReady={(first) => { runSetupAgain(false); finishSetup(first); }}
                    onClose={close}
                />
                <ModelNeededDialog />
                <UpdateIndicator />
            </div>
        );
    }

    const main = showMemoryDashboard ? (
        <Suspense fallback={<Loading />}><MemoryDashboard /></Suspense>
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

            <ErrorBoundary area="sidebar" variant="compact">
                <Sidebar
                    onNewChat={newChat}
                    onMobileAccess={isMobile ? undefined : () => void openMobileAccess()}
                    tunnelActive={tunnelActive}
                />
            </ErrorBoundary>

            <div className="main-content">
                {setup.state === 'ready' && <ConnectionBanner onRetry={checkSystemStatus} backendSlow={backendSlow} backendError={backendError} />}
                {setup.state === 'serverDown' && (
                    <div className="app-notice is-warning" role="status">
                        <span>{t('setup.serverDown.notice')}</span>
                        <button type="button" className="link-btn" onClick={() => void checkSetup(true)}>{t('setup.unknown.retry')}</button>
                        <button type="button" className="link-btn" onClick={() => runSetupAgain(true)}>{t('setup.serverDown.setup')}</button>
                    </div>
                )}
                {setup.state === 'unknown' && (setup.upgradeFailed ? (
                    <div className="app-notice is-warning" role="status">
                        <span>{t('setup.upgrading.failed', { error: setup.error })}</span>
                        <button type="button" className="link-btn" onClick={() => void checkSetup()}>{t('setup.upgrading.retry')}</button>
                    </div>
                ) : (
                    <div className="app-notice is-warning" role="status">
                        <span>{t('setup.unknown.failed', { error: setup.error })}</span>
                        <button type="button" className="link-btn" onClick={() => void checkSetup()}>{t('setup.unknown.retry')}</button>
                    </div>
                ))}
                {/* Model downloads, here when the sidebar that usually shows them is closed or collapsed. */}
                {(!sidebarOpen || sidebarCollapsed) && <DownloadChip placement="main" />}
                {flagshipMissing && <FlagshipAgentNotice onInstalled={loadAgents} noBuild={noAgentBuild} />}
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
                {/* Leaving the crashed area, or New chat again, clears the crash. */}
                <ErrorBoundary
                    resetKey={showMemoryDashboard ? 'memory' : (currentSessionId ?? `new-${newChatFocus}`)}
                    area="main"
                >
                    {main}
                </ErrorBoundary>
            </div>

            {showSchedules && <Suspense fallback={null}><ScheduleManager /></Suspense>}
            {showDocLibrary && <Suspense fallback={null}><DocumentLibrary /></Suspense>}
            {showFileBrowser && <Suspense fallback={null}><FileBrowser onStartChat={startChat} /></Suspense>}
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
                        setupCommands={tunnelSetup}
                        onRetry={() => void openMobileAccess()}
                    />
                </Suspense>
            )}
            {settingsSection && <SettingsDialog onUseSkill={useSkill} />}
            <ModelNeededDialog />
            <LogsDialog />
            <UpdateIndicator />
            {createError && (
                <div className="toast toast-dismissible" role="alert">
                    <span>{createError}</span>
                    <button
                        type="button"
                        className="toast-dismiss"
                        onClick={() => setCreateError(null)}
                        aria-label={t('banner.dismiss')}
                    >
                        <X size={14} aria-hidden="true" />
                    </button>
                </div>
            )}
        </div>
    );
}

export default App;
