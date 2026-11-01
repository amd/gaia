// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { AlertCircle, Check, ChevronRight, Cloud, Cpu, Info, Loader2 } from 'lucide-react';
import * as api from '../services/api';
import { shortModelName, useModelStore } from '../stores/modelStore';
import { lemonadeKeyRejected } from '../utils/lemonade';
import { CloudPrices } from './settings/CloudPrices';
import './settings/CloudSpending.css';
import { deviceKnown, ModelsStep, ReadySummary, SystemCheck } from './SetupSteps';
import { StartCards } from './StartCards';
import { useGatewayNetwork } from '../hooks/useGatewayNetwork';
import { useT, type Translate } from '../i18n/useT';
import { isMessageKey, type MessageKey } from '../i18n/registry';
import { ConfirmSheet } from './setup/ConfirmSheet';
import { DownloadProgress, downloadsActive } from './setup/DownloadProgress';
import { GpuMemoryPanel } from './setup/GpuMemoryPanel';
import { tooBigText } from './setup/ModelChecklist';
import {
    catalogOf, extraModels, initialSelection, overallFromPlan, showGpuStep, skipChanges, tooBig, type Selection,
} from './setup/selection';
import type {
    GpuMemoryStatus, LemonadeFound, LemonadeTarget, PreflightReport, ProviderId, SetupCatalogEntry,
    SetupConfirm, SetupModelProgress, SetupPlan, SetupProblem, SetupRunStatus, StartCard,
} from '../types';
import './SetupScreen.css';
import './setup/setup.css';

const CHOICES: Array<{ id: ProviderId; title: MessageKey | string; note: MessageKey }> = [
    { id: 'local', title: 'setup.where.local', note: 'setup.where.localNote' },
    { id: 'fireworks', title: 'Fireworks AI', note: 'setup.where.fireworksNote' },
    { id: 'amd', title: 'AMD LLM Gateway', note: 'setup.where.amdNote' },
];

/** A provider's name: translated for "On this PC", a brand name otherwise. */
function choiceTitle(c: (typeof CHOICES)[number], t: Translate): string {
    return isMessageKey(c.title) ? t(c.title) : c.title;
}

const POLL_MS = 1000;
const CHOICE_KEY = 'gaia-setup-choice';

/** The provider picked for this setup, kept across reloads, new tabs and restarts. */
function readChoice(): ProviderId | null {
    try {
        const v = localStorage.getItem(CHOICE_KEY);
        return v === 'local' || v === 'fireworks' || v === 'amd' ? v : null;
    } catch (err) {
        console.warn('[SetupScreen] could not read the saved provider choice', err);
        return null;
    }
}

function saveChoice(choice: ProviderId): void {
    try {
        localStorage.setItem(CHOICE_KEY, choice);
    } catch (err) {
        console.warn('[SetupScreen] could not save the provider choice; a reload will ask again', err);
    }
}

const OFFERED: ReadonlyArray<LemonadeFound['status']> = ['ok', 'needs_key', 'too_old'];

/**
 * First run on a PC that already runs Lemonade: offer it, so GAIA does not
 * install a second server and a second copy of every model.
 */
function ExistingLemonadeOffer({ onChosen }: { onChosen: (mode: 'external' | 'gaia') => void }) {
    const [offer, setOffer] = useState<{ target: LemonadeTarget; found: LemonadeFound } | null>(null);
    const [checkError, setCheckError] = useState<string | null>(null);
    // Something answered the connection but not the question: "couldn't tell", not "none".
    const [unchecked, setUnchecked] = useState<string[]>([]);
    const [checking, setChecking] = useState(false);
    const [key, setKey] = useState('');
    const [needsKey, setNeedsKey] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [chosen, setChosen] = useState<'external' | 'gaia' | null>(null);

    const live = useRef(true);
    const detect = useCallback(async () => {
        setCheckError(null);
        try {
            const target = await api.getLemonadeTarget();
            if (!target.offer) return;
            setChecking(true);
            const { found, unchecked: missed = [] } = await api.detectLemonade();
            if (!live.current) return;
            setUnchecked(found ? [] : missed);
            if (found && OFFERED.includes(found.status)) {
                setOffer({ target, found });
                setNeedsKey(found.status === 'needs_key');
            }
        } catch (err) {
            if (live.current) setCheckError(err instanceof Error ? err.message : String(err));
        } finally {
            if (live.current) setChecking(false);
        }
    }, []);

    useEffect(() => {
        live.current = true;
        void detect();
        return () => { live.current = false; };
    }, [detect]);

    const choose = useCallback(async (mode: 'external' | 'gaia') => {
        if (!offer) return;
        setBusy(true);
        setError(null);
        try {
            await api.setLemonadeTarget(mode === 'external'
                ? { mode, base_url: offer.found.url, api_key: key.trim() || undefined }
                : { mode });
            setKey('');
            setChosen(mode);
            onChosen(mode);
        } catch (err) {
            if (lemonadeKeyRejected(err)) setNeedsKey(true);
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setBusy(false);
        }
    }, [offer, key, onChosen]);

    if (checkError || unchecked.length > 0) {
        return (
            <div className="setup-row">
                <span className="setup-hint">
                    {checkError
                        ? `Couldn't check for a Lemonade Server you already run: ${checkError}`
                        : `Couldn't check whether your Lemonade Server at ${unchecked.join(' or ')} is running: it didn't answer in time.`}
                </span>
                <button type="button" className="btn-secondary" disabled={checking} onClick={() => void detect()}>
                    {checking && <Loader2 size={13} className="spin" />} Check again
                </button>
            </div>
        );
    }
    if (!offer) {
        return checking ? (
            <p className="setup-hint" role="status">
                <Loader2 size={13} className="spin" aria-hidden="true" /> Looking for a Lemonade Server you already run…
            </p>
        ) : null;
    }
    const { target, found } = offer;
    if (chosen) {
        return (
            <p className="setup-hint" role="status">
                {chosen === 'external'
                    ? `GAIA will use your Lemonade Server at ${found.url} and won't install its own.`
                    : 'GAIA will set up its own Lemonade Server.'}
            </p>
        );
    }
    if (found.status === 'too_old') {
        return <p className="setup-hint">{found.message}</p>;
    }
    return (
        <form className="setup-lemonade" aria-label="Use your Lemonade Server" onSubmit={(e) => { e.preventDefault(); void choose('external'); }}>
            <span className="setup-choice-title">
                Use the Lemonade you already run at {found.url}{found.version ? ` (v${found.version})` : ''}
            </span>
            <span className="setup-choice-note">{target.what_gaia_does}</span>
            {needsKey && (
                <input
                    className="settings-input"
                    type="password"
                    autoComplete="off"
                    value={key}
                    onChange={(e) => setKey(e.target.value)}
                    placeholder="The API key your Lemonade Server was started with"
                    aria-label="Lemonade API key"
                />
            )}
            <div className="setup-row">
                <button type="submit" className="btn-primary" disabled={busy || (needsKey && !key.trim())}>
                    {busy && <Loader2 size={13} className="spin" />} Use mine
                </button>
                <button type="button" className="btn-secondary" disabled={busy} onClick={() => void choose('gaia')}>
                    Use GAIA&rsquo;s own
                </button>
            </div>
            {error && <p className="setup-error" role="alert">{error}</p>}
        </form>
    );
}

/** The user's Lemonade isn't answering: start it, or set up GAIA's own instead. */
function ServerProblem({ problem, onRetry, onUseGaia }: { problem: SetupProblem; onRetry: () => void; onUseGaia: () => void }) {
    return (
        <>
            {problem.start && <p className="setup-hint">{problem.start}</p>}
            <div className="setup-row">
                <button type="button" className="btn-primary" onClick={onRetry}>Try again</button>
                {problem.use_gaia && (
                    <button type="button" className="btn-secondary" onClick={onUseGaia}>Use GAIA&rsquo;s own instead</button>
                )}
            </div>
            {problem.use_gaia && (
                <p className="setup-hint">GAIA&rsquo;s own Lemonade Server downloads its own copy of the models.</p>
            )}
            {problem.switch_hint && <p className="setup-hint">{problem.switch_hint}</p>}
        </>
    );
}

interface SetupScreenProps {
    /** Setup is done; `first` is a task the user picked to start with. */
    onReady: (first?: StartCard) => void;
}

/** Where the user is: each phase is one numbered step, with Back to the one before. */
type Phase = 'where' | 'server' | 'models' | 'gpu' | 'download';

type StepKey = 'check' | 'where' | 'server' | 'models' | 'gpu' | 'download' | 'connect';

/** What a confirmation sheet is asking. */
type Confirm =
    | { kind: 'no_fit'; models: SetupCatalogEntry[] }
    | { kind: 'server'; detail: SetupConfirm; then: 'chat' | 'run' }
    | { kind: 'server'; detail: SetupConfirm; then: 'retry'; id: string };

function errorText(err: unknown): string {
    return err instanceof Error ? err.message : String(err);
}

interface StepProps {
    n: number;
    status: 'done' | 'current' | 'pending' | 'failed' | 'active';
    label: ReactNode;
    labelId?: string;
    children?: ReactNode;
}

function Step({ n, status, label, labelId, children }: StepProps) {
    const icon = status === 'done' ? <Check size={14} aria-hidden="true" />
        : status === 'active' ? <Loader2 size={14} className="spin" aria-hidden="true" />
            : status === 'failed' ? <AlertCircle size={14} aria-hidden="true" />
                : n;
    return (
        <li className={`setup-step is-${status}`} aria-current={status === 'current' || status === 'active' ? 'step' : undefined}>
            <div className="setup-step-head">
                <span className="setup-step-num">{icon}</span>
                <span className="setup-step-label" id={labelId}>{label}</span>
            </div>
            {children && <div className="setup-step-body">{children}</div>}
        </li>
    );
}

/**
 * First run, the same steps as the TUI: check this PC, choose where GAIA runs,
 * start the model server when there is none yet, choose the models (and, on an
 * integrated GPU, its memory share), then download them and check they load.
 * Nothing downloads until the user has seen the models and the totals. A cloud
 * choice downloads only the embedding model. Ends on a first task.
 */
export function SetupScreen({ onReady }: SetupScreenProps) {
    const t = useT();
    const refreshModel = useModelStore((s) => s.refresh);
    const selectModel = useModelStore((s) => s.select);
    const [choice, setChoice] = useState<ProviderId>(() => readChoice() ?? 'local');
    const [phase, setPhase] = useState<Phase>('where');
    const [hardware, setHardware] = useState<PreflightReport | null>(null);
    const [hardwareError, setHardwareError] = useState<string | null>(null);
    const [run, setRun] = useState<SetupRunStatus | null>(null);
    const [lostContact, setLostContact] = useState<string | null>(null);
    const [startError, setStartError] = useState<string | null>(null);
    const [key, setKey] = useState('');
    const [keyState, setKeyState] = useState<{ busy: boolean; error: string | null; done: boolean; waiting?: string | null }>({ busy: false, error: null, done: false });
    const [showLog, setShowLog] = useState(false);
    const [plan, setPlan] = useState<SetupPlan | null>(null);
    const [planError, setPlanError] = useState<string | null>(null);
    const [selection, setSelection] = useState<Selection | null>(null);
    // The plan the download started from: the download step's totals come from it,
    // as the models step's did, so the two screens show the same numbers.
    const [startPlan, setStartPlan] = useState<SetupPlan | null>(null);
    // The plan read after setup finished: what is really on disk, for the summary.
    const [finalPlan, setFinalPlan] = useState<SetupPlan | null>(null);
    const [gpu, setGpu] = useState<GpuMemoryStatus | null>(null);
    const [keepTrying, setKeepTrying] = useState(false);
    const [confirm, setConfirm] = useState<Confirm | null>(null);
    const [noFitAcked, setNoFitAcked] = useState(false);
    const [busy, setBusy] = useState(false);
    const [actionError, setActionError] = useState<string | null>(null);
    const [resumeOffer, setResumeOffer] = useState(false);
    const [serverStep, setServerStep] = useState(false);
    const [cloudModel, setCloudModel] = useState<string | null>(null);
    const [firstTasks, setFirstTasks] = useState<StartCard[]>([]);
    // GAIA uses a Lemonade the user runs, so the Connect step changes nothing there.
    const [usersServer, setUsersServer] = useState(false);
    // What the last run was started with, so Resume and Retry repeat it.
    const lastRun = useRef<{ gaiaLemonade: boolean; options: api.SetupRunOptions }>({ gaiaLemonade: false, options: {} });
    const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
    // A connect waiting on an OS prompt stops asking once setup is gone.
    const mounted = useRef(true);
    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; };
    }, []);
    const { network: gateway, recheck: recheckGateway } = useGatewayNetwork();
    const unreachable = (id: ProviderId) => id === 'amd' && gateway?.reachable === false;

    const cloud = choice !== 'local';
    const serverRun = !!run?.server_only;
    const fullRun = run && !run.server_only ? run : null;
    const running = run?.state === 'running' || run?.state === 'verifying';
    const installed = fullRun?.state === 'ready';
    const moving = downloadsActive(run);

    const loadHardware = useCallback(() => {
        setHardwareError(null);
        api.getOnboardingPreflight()
            .then(setHardware)
            .catch((err) => setHardwareError(errorText(err)));
    }, []);

    // A cold PC's first plan can answer after the one asked for once "Use mine" is picked.
    const planRequest = useRef(0);
    const loadPlan = useCallback(async (): Promise<SetupPlan | null> => {
        const request = ++planRequest.current;
        setPlanError(null);
        try {
            const p = await api.getSetupPlan();
            if (request !== planRequest.current) return null;
            setPlan(p);
            return p;
        } catch (err) {
            if (request === planRequest.current) setPlanError(errorText(err));
            return null;
        }
    }, []);

    const loadGpu = useCallback(async () => {
        try {
            const g = await api.getGpuMemory();
            if (mounted.current) setGpu(g ?? null);
        } catch (err) {
            // Optional step: without a reading it stays hidden, and Settings shows the error.
            console.warn('[SetupScreen] GPU memory could not be read; the step stays hidden', err);
        }
    }, []);

    useEffect(() => {
        api.getLemonadeTarget()
            .then((tg) => setUsersServer(tg.source === 'config'))
            .catch((err) => console.warn('[SetupScreen] could not read which Lemonade GAIA uses', err));
        loadHardware();
        void loadPlan();
        // Pick up a setup another tab (or a reload) already started.
        api.getSetupStatus()
            .then((s) => {
                if (s.state === 'idle' && !s.models?.length) return;
                setRun(s);
                if (s.server_only) {
                    setServerStep(true);
                    if (s.state !== 'ready') setPhase('server');
                } else {
                    setPhase('download');
                }
                // A cloud run with no saved choice can't say which provider; the
                // Connect step lets the user switch if this guess is wrong.
                if (s.skip_chat_model && !readChoice()) setChoice('fireworks');
            })
            .catch((err) => setStartError(`Could not read setup progress: ${errorText(err)}`));
    }, [loadHardware, loadPlan]);

    // The plan seeds the checklist once, from what is planned and on disk.
    useEffect(() => {
        if (!plan?.catalog || !plan.lemonade.reachable) return;
        setSelection((s) => s ?? initialSelection(plan));
        void loadGpu();
        // A download that didn't finish last time: offer to resume, never resume on its own.
        if (!run && plan.catalog.some((e) => e.partial && e.selected)) setResumeOffer(true);
    }, [plan, loadGpu, run]);

    const stopPolling = useCallback(() => {
        if (pollRef.current) clearInterval(pollRef.current);
        pollRef.current = null;
    }, []);

    // A full disk: read the free space again, so "Free N GB" is what is needed now.
    const diskFull = (run?.models ?? []).filter((m) => m.state === 'failed' && m.error_kind === 'disk_full').length;
    useEffect(() => {
        if (diskFull > 0) void loadPlan();
    }, [diskFull, loadPlan]);

    const poll = useCallback(async () => {
        try {
            const s = await api.getSetupStatus();
            setLostContact(null);
            setRun(s);
        } catch (err) {
            setLostContact(errorText(err));
        }
    }, []);

    useEffect(() => {
        if (!moving && !lostContact) return stopPolling;
        pollRef.current = setInterval(() => { void poll(); }, POLL_MS);
        return stopPolling;
    }, [moving, lostContact, poll, stopPolling]);

    // The server came up: read the device and the models against it, then choose.
    useEffect(() => {
        if (!serverRun || run?.state !== 'ready') return;
        loadHardware();
        void loadPlan().then((p) => { if (p?.lemonade.reachable) setPhase((ph) => (ph === 'server' ? 'models' : ph)); });
    }, [serverRun, run?.state, loadHardware, loadPlan]);

    // Setup finished: read what is on disk now, for the summary.
    useEffect(() => {
        if (!installed) return;
        loadHardware();
        void loadPlan().then((p) => { if (p) setFinalPlan(p); });
        void loadGpu();
    }, [installed, loadHardware, loadPlan, loadGpu]);

    const start = useCallback(async (gaiaLemonade = false, options: api.SetupRunOptions = {}) => {
        setStartError(null);
        setActionError(null);
        setResumeOffer(false);
        try {
            saveChoice(choice);
            lastRun.current = { gaiaLemonade, options };
            const s = await api.startSetup(cloud, gaiaLemonade, options);
            setRun(s);
            if (options.serverOnly) {
                setServerStep(true);
                setPhase('server');
            } else {
                setPhase('download');
            }
            if (gaiaLemonade) setUsersServer(false);
        } catch (err) {
            const detail = api.setupConfirmFrom(err);
            if (detail) {
                setConfirm({ kind: 'server', detail, then: 'run' });
                return;
            }
            setStartError(errorText(err));
        }
    }, [choice, cloud]);

    const retryRun = useCallback(() => {
        const { gaiaLemonade, options } = lastRun.current;
        void start(gaiaLemonade, options);
    }, [start]);

    /** Download with the selection: the plan's models, minus skips, plus ticked extras. */
    const download = useCallback((acknowledgeLowDisk: boolean, latest: SetupPlan | null = plan) => {
        setStartPlan(latest);
        const extras = latest && selection && !cloud ? extraModels(latest, selection) : [];
        void start(false, { acknowledgeLowDisk, keepTrying, extraModels: extras });
    }, [plan, selection, cloud, keepTrying, start]);

    const [ackDisk, setAckDisk] = useState(false);

    /** Save the choices (chat model, skips), then the GPU step or the download. */
    const commit = useCallback(async (acknowledgeLowDisk: boolean, acknowledgeNoFit = noFitAcked) => {
        if (!plan || !selection) return;
        setActionError(null);
        setAckDisk(acknowledgeLowDisk);
        const catalog = catalogOf(plan);
        if (!cloud && !acknowledgeNoFit) {
            const big = tooBig(catalog, selection).filter((e) => !e.downloaded || e.id === selection.defaultChat);
            if (big.length > 0 && big.every((e) => e.need_gb != null)) {
                setConfirm({ kind: 'no_fit', models: big });
                return;
            }
        }
        setBusy(true);
        try {
            let latest = plan;
            if (!cloud && selection.defaultChat && selection.defaultChat !== latest.chat_model.id) {
                try {
                    latest = await api.chooseSetupChatModel(selection.defaultChat, acknowledgeNoFit);
                } catch (err) {
                    const detail = api.setupConfirmFrom(err);
                    if (detail) {
                        setConfirm({ kind: 'server', detail, then: 'chat' });
                        return;
                    }
                    throw err;
                }
            }
            if (!cloud) {
                for (const change of skipChanges(latest, selection)) {
                    latest = await api.skipSetupModel(change.id, change.skipped);
                }
            }
            setPlan(latest);
            if (!cloud && showGpuStep(gpu, selection)) {
                setPhase('gpu');
                return;
            }
            download(acknowledgeLowDisk, latest);
        } catch (err) {
            setActionError(errorText(err));
        } finally {
            setBusy(false);
        }
    }, [plan, selection, cloud, noFitAcked, gpu, download]);

    const continueFromWhere = useCallback(() => {
        saveChoice(choice);
        setActionError(null);
        if (!cloud && plan && !plan.lemonade.reachable) {
            void start(false, { serverOnly: true });
            return;
        }
        setPhase('models');
    }, [choice, cloud, plan, start]);

    const back = useCallback(() => {
        setActionError(null);
        setPhase((p) => (p === 'gpu' ? 'models' : p === 'models' ? 'where' : p));
    }, []);

    // Alt+Left goes back a step, like the Back button.
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if (!e.altKey || e.key !== 'ArrowLeft' || confirm) return;
            if (phase === 'models' || phase === 'gpu') {
                e.preventDefault();
                back();
            }
        };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [phase, back, confirm]);

    const cancel = useCallback(async () => {
        try {
            await api.cancelSetup();
            await poll();
        } catch (err) {
            setActionError(errorText(err));
        }
    }, [poll]);

    /** One model again: Retry and Resume continue from the bytes on disk; a failed load re-downloads. */
    const retryModel = useCallback(async (id: string, kind: SetupModelProgress['error_kind']) => {
        setActionError(null);
        setBusy(true);
        try {
            if (kind === 'verify') await api.removeSetupModel(id);
            if (kind === 'disk_full') void loadPlan();
            try {
                setRun(await api.downloadSetupModel(id, { keepTrying }));
            } catch (err) {
                const detail = api.setupConfirmFrom(err);
                if (!detail) throw err;
                setConfirm({ kind: 'server', detail, then: 'retry', id });
            }
        } catch (err) {
            setActionError(errorText(err));
        } finally {
            setBusy(false);
        }
    }, [keepTrying, loadPlan]);

    const skipModel = useCallback(async (id: string) => {
        setActionError(null);
        setBusy(true);
        try {
            const entry = plan?.catalog?.find((e) => e.id === id);
            if (entry?.planned) setPlan(await api.skipSetupModel(id, true));
            setRun((r) => r && { ...r, models: r.models?.map((m) => (m.id === id ? { ...m, state: 'skipped' } : m)) });
        } catch (err) {
            setActionError(errorText(err));
        } finally {
            setBusy(false);
        }
    }, [plan]);

    const restartDownloads = useCallback(async () => {
        await cancel();
        retryRun();
    }, [cancel, retryRun]);

    const connect = useCallback(async () => {
        setKeyState({ busy: true, error: null, done: false });
        try {
            await api.connectProviderWaitingForOs(
                choice,
                { api_key: (!usersServer && key) || undefined },
                (waiting) => setKeyState({ busy: true, error: null, done: false, waiting }),
                () => !mounted.current,
            );
            const { models } = await api.listProviderModels(choice);
            const pick = models.find((m) => m.rank === 1) ?? models[0];
            if (!pick) throw new Error('Connected, but the provider listed no chat models.');
            await selectModel(pick.id);
            setCloudModel(pick.id);
            setKey('');
            setKeyState({ busy: false, error: null, done: true });
        } catch (err) {
            setKeyState({ busy: false, error: errorText(err), done: false });
        }
    }, [choice, key, selectModel, usersServer]);

    const switchCloud = useCallback((id: ProviderId) => {
        saveChoice(id);
        setChoice(id);
        setKeyState({ busy: false, error: null, done: false });
    }, []);

    const finish = useCallback(async (first?: StartCard) => {
        await refreshModel();
        onReady(first);
    }, [onReady, refreshModel]);

    const canFinish = installed && (!cloud || keyState.done);
    const answering = cloud ? cloudModel : fullRun?.chat_model ?? (plan && !plan.decided_after_server ? plan.chat_model.id : null);

    useEffect(() => {
        if (!canFinish) return;
        api.getStartScreen()
            .then((s) => setFirstTasks(s.cards))
            .catch((err) => console.warn('[SetupScreen] no first tasks to offer', err));
    }, [canFinish]);

    const names = useMemo(() => {
        const out: Record<string, string> = {};
        for (const e of plan?.catalog ?? []) out[e.id] = e.name;
        return out;
    }, [plan]);

    // The steps, numbered as shown: the server and GPU steps appear only when they apply.
    const gpuStep = !cloud && (phase === 'gpu' || (!!selection && showGpuStep(gpu, selection)));
    const serverShown = !cloud && (serverStep || phase === 'server' || (!!plan && !plan.lemonade.reachable && !fullRun));
    const keys: StepKey[] = ['check', 'where', ...(serverShown ? ['server' as const] : []), 'models', ...(gpuStep ? ['gpu' as const] : []), 'download', ...(cloud ? ['connect' as const] : [])];
    const num = (k: StepKey) => keys.indexOf(k) + 1;
    const order: StepKey[] = ['where', 'server', 'models', 'gpu', 'download', 'connect'];
    const at = order.indexOf(phase === 'download' && installed && cloud ? 'connect' : phase);
    const stepStatus = (k: StepKey): StepProps['status'] => {
        const i = order.indexOf(k);
        if (installed && k !== 'connect') return 'done';
        if (i < at) return 'done';
        if (i === at) return 'current';
        return 'pending';
    };
    const checkOpen = !!hardwareError || (!!hardware && !deviceKnown(hardware));
    const runFailed = run?.state === 'failed' || run?.state === 'cancelled';
    const downloadDone = fullRun && (fullRun.state === 'failed' || fullRun.state === 'cancelled')
        && (fullRun.models ?? []).length > 0
        && (fullRun.models ?? []).every((m) => m.state === 'ready' || m.state === 'skipped' || m.state === 'verifying')
        && !moving;

    const lede = canFinish ? t('setup.lede.done') : run && running ? t('setup.lede.running') : t('setup.lede.start');

    return (
        <main className="setup" aria-labelledby="setup-title">
            <div className="setup-inner">
                <h1 id="setup-title" className="setup-title">{t('setup.title')}</h1>
                <p className="setup-lede">{lede}</p>

                {resumeOffer && phase !== 'download' && (
                    <div className="setup-banner" role="status">
                        <Info size={14} aria-hidden="true" />
                        <span>{t('setup.resume.banner')}</span>
                        <span className="setup-row">
                            <button type="button" className="btn-primary" onClick={() => download(false)}>{t('setup.resume.resume')}</button>
                            <button type="button" className="btn-secondary" onClick={() => { setResumeOffer(false); setPhase('models'); }}>{t('setup.models.change')}</button>
                        </span>
                    </div>
                )}

                <ol className="setup-steps">
                    <li className={`setup-step${checkOpen ? '' : hardware ? ' is-done' : ' is-current'}`}>
                        <div className="setup-step-head">
                            <span className="setup-step-num">
                                {checkOpen ? <Info size={14} aria-hidden="true" /> : hardware ? <Check size={14} aria-hidden="true" /> : num('check')}
                            </span>
                            <span className="setup-step-label">{t('setup.check.title')}</span>
                        </div>
                        {phase === 'where' && (
                            <div className="setup-step-body">
                                <SystemCheck hardware={hardware} error={hardwareError} />
                            </div>
                        )}
                    </li>

                    <Step n={num('where')} status={stepStatus('where')} label={t('setup.where.title')}>
                        {phase === 'where' && (
                            <>
                                <ExistingLemonadeOffer onChosen={(mode) => { setUsersServer(mode === 'external'); loadHardware(); void loadPlan(); }} />
                                <div className="setup-choices" role="radiogroup" aria-label="Where GAIA runs">
                                    {CHOICES.map((c) => (
                                        <button
                                            key={c.id}
                                            type="button"
                                            role="radio"
                                            aria-checked={choice === c.id}
                                            className={`setup-choice${choice === c.id ? ' is-active' : ''}${unreachable(c.id) ? ' is-disabled' : ''}`}
                                            onClick={() => setChoice(c.id)}
                                            disabled={unreachable(c.id)}
                                        >
                                            {c.id === 'local' ? <Cpu size={16} aria-hidden="true" /> : <Cloud size={16} aria-hidden="true" />}
                                            <span>
                                                <span className="setup-choice-title">{choiceTitle(c, t)}</span>
                                                <span className="setup-choice-note">{unreachable(c.id) ? gateway?.detail : t(c.note)}</span>
                                            </span>
                                        </button>
                                    ))}
                                </div>
                                {gateway?.reachable === false && (
                                    <button type="button" className="btn-secondary setup-start" onClick={recheckGateway}>
                                        Check the AMD gateway again
                                    </button>
                                )}
                                <div className="setup-row setup-nav">
                                    <button type="button" className="btn-primary setup-start" onClick={continueFromWhere} disabled={unreachable(choice)}>
                                        {t('setup.continue')}
                                    </button>
                                </div>
                                {unreachable(choice) && <p className="setup-error" role="alert">{gateway?.detail}. Choose another option, or connect and check again.</p>}
                                {startError && <p className="setup-error" role="alert">{startError}</p>}
                            </>
                        )}
                    </Step>

                    {serverShown && (
                        <Step
                            n={num('server')}
                            status={serverRun && run?.state === 'ready' || (plan?.lemonade.reachable && phase !== 'server') ? 'done'
                                : serverRun && runFailed ? 'failed' : phase === 'server' ? 'active' : 'pending'}
                            label={usersServer
                                ? <>{t('setup.server.yours')}{plan?.lemonade.url && <span className="setup-step-detail"> — <bdi>{plan.lemonade.url}</bdi></span>}</>
                                : <>{t('setup.server.install')}<span className="setup-step-detail"> — {t('setup.server.aboutMinute')}</span></>}
                        >
                            {phase === 'server' && serverRun && running && (
                                <p className="setup-hint" role="status">{run?.text}</p>
                            )}
                            {phase === 'server' && serverRun && runFailed && run && (
                                <>
                                    <p className="setup-error" role="alert">{run.error ?? 'Setup stopped.'}</p>
                                    {run.problem ? (
                                        <ServerProblem problem={run.problem} onRetry={() => void start(false, { serverOnly: true })} onUseGaia={() => void start(true, { serverOnly: true })} />
                                    ) : (
                                        <div className="setup-row">
                                            <button type="button" className="btn-primary" onClick={() => void start(!!run.gaia_lemonade, { serverOnly: true })}>Try again</button>
                                            <span className="setup-hint">or run <code>{run.command}</code> in a terminal.</span>
                                        </div>
                                    )}
                                </>
                            )}
                            {phase === 'server' && serverRun && run?.state === 'ready' && (
                                <p className="setup-hint" role="status"><Loader2 size={13} className="spin" aria-hidden="true" /> {t('setup.models.loading')}</p>
                            )}
                        </Step>
                    )}

                    <Step n={num('models')} status={stepStatus('models')} label={t('setup.models.title')} labelId="setup-models-title">
                        {phase === 'models' && (
                            <ModelsStep
                                plan={plan}
                                planError={planError}
                                onRetryPlan={() => void loadPlan()}
                                onUseRecommended={() => void start(false, { keepTrying })}
                                selection={selection}
                                onSelection={setSelection}
                                cloud={cloud}
                                keepTrying={keepTrying}
                                onKeepTrying={setKeepTrying}
                                labelledBy="setup-models-title"
                                onBack={back}
                                onContinue={(ack) => void commit(ack)}
                                busy={busy}
                                error={actionError ?? startError}
                            />
                        )}
                    </Step>

                    {gpuStep && (
                        <Step n={num('gpu')} status={stepStatus('gpu')} label={t('gpu.title')}>
                            {phase === 'gpu' && gpu && (
                                <>
                                    <GpuMemoryPanel
                                        status={gpu}
                                        onStatus={setGpu}
                                        mode="setup"
                                        names={names}
                                        onBack={back}
                                        onSkip={() => download(ackDisk)}
                                        onContinue={() => download(ackDisk)}
                                    />
                                    {startError && <p className="setup-error" role="alert">{startError}</p>}
                                </>
                            )}
                        </Step>
                    )}

                    <Step
                        n={num('download')}
                        status={installed ? 'done' : phase === 'download' ? (runFailed && !downloadDone ? 'failed' : running ? 'active' : 'current') : 'pending'}
                        label={t('setup.download.title')}
                        labelId="setup-download-title"
                    >
                        {phase === 'download' && fullRun && (
                            <>
                                {(fullRun.models?.length ?? 0) > 0 ? (
                                    <div className={lostContact ? 'is-stale' : undefined}>
                                        <DownloadProgress
                                            status={{ ...fullRun, overall: overallFromPlan(fullRun, startPlan ?? plan) }}
                                            names={names}
                                            freeGb={plan?.disk?.free_gb ?? null}
                                            onCancel={() => void cancel()}
                                            onRetry={(id, kind) => void retryModel(id, kind)}
                                            onResume={(id) => void retryModel(id, null)}
                                            onResumeAll={retryRun}
                                            onSkip={(id) => void skipModel(id)}
                                            onRestart={() => void restartDownloads()}
                                            busy={busy || !!lostContact}
                                            retryLater={running}
                                            labelledBy="setup-download-title"
                                        />
                                    </div>
                                ) : running ? (
                                    <p className="setup-hint" role="status"><Loader2 size={13} className="spin" aria-hidden="true" /> {fullRun.text}</p>
                                ) : null}
                                {fullRun.state === 'verifying' && (
                                    <p className="setup-hint" role="status"><Loader2 size={13} className="spin" aria-hidden="true" /> {t('setup.download.verifying')}</p>
                                )}
                                {lostContact && (
                                    <div className="setup-row" role="alert">
                                        <span className="setup-error">Lost contact with GAIA: {lostContact}</span>
                                        <button type="button" className="btn-secondary" onClick={() => void poll()}>{t('setup.reconnect')}</button>
                                    </div>
                                )}
                                {runFailed && downloadDone && (
                                    <div className="setup-row">
                                        <button type="button" className="btn-primary" onClick={retryRun} disabled={busy}>{t('setup.finish')}</button>
                                        <span className="setup-hint">{t('setup.download.finishNote')}</span>
                                    </div>
                                )}
                                {runFailed && !downloadDone && !(fullRun.models ?? []).some((m) => m.state === 'failed' || m.state === 'cancelled') && (
                                    <>
                                        <p className="setup-error" role="alert">{fullRun.error ?? 'Setup stopped.'}</p>
                                        {fullRun.problem ? (
                                            <ServerProblem problem={fullRun.problem} onRetry={retryRun} onUseGaia={() => void start(true, lastRun.current.options)} />
                                        ) : (
                                            <div className="setup-row">
                                                <button type="button" className="btn-primary" onClick={() => void start(!!fullRun.gaia_lemonade, lastRun.current.options)}>Try again</button>
                                                <span className="setup-hint">or run <code>{fullRun.command}</code> in a terminal.</span>
                                            </div>
                                        )}
                                    </>
                                )}
                                {(actionError || startError) && <p className="setup-error" role="alert">{actionError ?? startError}</p>}
                            </>
                        )}
                    </Step>

                    {cloud && (
                        <li className={`setup-step${keyState.done ? ' is-done' : installed ? ' is-current' : ' is-pending'}`}>
                            <div className="setup-step-head">
                                <span className="setup-step-num">{keyState.done ? <Check size={14} aria-hidden="true" /> : num('connect')}</span>
                                <span className="setup-step-label">Connect {choiceTitle(CHOICES.find((c) => c.id === choice) ?? CHOICES[1], t)}</span>
                            </div>
                            {installed && !keyState.done && (
                                <form className="setup-step-body" onSubmit={(e) => { e.preventDefault(); void connect(); }}>
                                    <div className="setup-row" role="radiogroup" aria-label="Cloud provider">
                                        {CHOICES.filter((c) => c.id !== 'local').map((c) => (
                                            <button
                                                key={c.id}
                                                type="button"
                                                role="radio"
                                                aria-checked={choice === c.id}
                                                className={choice === c.id ? 'btn-primary' : 'btn-secondary'}
                                                onClick={() => switchCloud(c.id)}
                                                disabled={keyState.busy || unreachable(c.id)}
                                                title={unreachable(c.id) ? gateway?.detail ?? undefined : undefined}
                                            >
                                                {choiceTitle(c, t)}
                                            </button>
                                        ))}
                                    </div>
                                    <CloudPrices provider={choice} limit={3} />
                                    {usersServer ? (
                                        <p className="setup-hint">
                                            GAIA uses this provider as your Lemonade Server already has it set up. It doesn&rsquo;t send your server a key or change its setup.
                                        </p>
                                    ) : (
                                        <p className="setup-hint">The key goes to GAIA&rsquo;s local model server and your OS credential store, nowhere else.</p>
                                    )}
                                    <div className="setup-row">
                                        {!usersServer && <input
                                            className="settings-input"
                                            type="password"
                                            autoComplete="off"
                                            value={key}
                                            onChange={(e) => setKey(e.target.value)}
                                            placeholder="Paste API key (blank uses a key already configured)"
                                            aria-label="API key"
                                        />}
                                        <button type="submit" className="btn-primary" disabled={keyState.busy}>
                                            {keyState.busy && <Loader2 size={13} className="spin" />} Connect
                                        </button>
                                    </div>
                                    {keyState.busy && keyState.waiting && <p className="setup-hint" role="status">{keyState.waiting}</p>}
                                    {keyState.error && <p className="setup-error" role="alert">{keyState.error}</p>}
                                </form>
                            )}
                        </li>
                    )}
                </ol>

                {run && run.log_tail && run.log_tail.length > 0 && (
                    <div className="setup-log">
                        <button type="button" className="setup-log-toggle" onClick={() => setShowLog((v) => !v)} aria-expanded={showLog}>
                            <ChevronRight size={13} className={showLog ? 'is-open' : ''} aria-hidden="true" /> Details
                        </button>
                        {showLog && <pre className="setup-log-pre">{run.log_tail.join('\n')}</pre>}
                    </div>
                )}

                {canFinish && (
                    <div className="setup-done" role="status">
                        <div className="setup-done-head">
                            <p>
                                {t.compose((say) => [
                                    say('setup.ready.title'),
                                    answering ? say(cloud ? 'setup.ready.answersCloud' : 'setup.ready.answersLocal', { model: shortModelName(answering) }) : null,
                                ].filter(Boolean).join(' '))}
                            </p>
                            <button type="button" className="btn-primary" onClick={() => void finish()} autoFocus>
                                {t('setup.ready.start')}
                            </button>
                        </div>
                        {!cloud && finalPlan && <ReadySummary plan={finalPlan} chatModel={answering} gpu={gpu} />}
                        {firstTasks.length > 0 && (
                            <>
                                <p className="setup-hint">Or start with one of these:</p>
                                <StartCards cards={firstTasks} onPick={(card) => void finish(card)} />
                            </>
                        )}
                    </div>
                )}
            </div>

            {confirm?.kind === 'no_fit' && (
                <ConfirmSheet
                    title={confirm.models.length === 1
                        ? t('setup.confirm.noFitOne', { name: confirm.models[0].name })
                        : t('setup.confirm.noFitMany')}
                    confirmLabel={t('setup.confirm.downloadAnyway')}
                    cancelLabel={t('setup.confirm.pickSmaller')}
                    onConfirm={() => { setConfirm(null); setNoFitAcked(true); void commit(ackDisk, true); }}
                    onCancel={() => setConfirm(null)}
                >
                    {confirm.models.map((e) => <p key={e.id}><bdi>{e.name}</bdi>: {tooBigText(e, t)}</p>)}
                </ConfirmSheet>
            )}
            {confirm?.kind === 'server' && (
                <ConfirmSheet
                    title={confirm.detail.code === 'low_disk' ? t('setup.confirm.lowDiskTitle') : t('setup.confirm.noFitServerTitle')}
                    confirmLabel={confirm.detail.code === 'low_disk' ? t('setup.confirm.downloadAnyway') : t('setup.confirm.useAnyway')}
                    cancelLabel={t('setup.confirm.changeModels')}
                    onConfirm={() => {
                        const c = confirm;
                        setConfirm(null);
                        if (c.then === 'chat') {
                            setNoFitAcked(true);
                            void commit(ackDisk, true);
                        } else if (c.then === 'retry') {
                            void api.downloadSetupModel(c.id, { keepTrying, acknowledgeLowDisk: true })
                                .then(setRun)
                                .catch((err) => setActionError(errorText(err)));
                        } else {
                            const { gaiaLemonade, options } = lastRun.current;
                            void start(gaiaLemonade, { ...options, acknowledgeLowDisk: true });
                        }
                    }}
                    onCancel={() => { const c = confirm; setConfirm(null); if (c.then !== 'retry') setPhase('models'); }}
                >
                    <p>{confirm.detail.message}</p>
                </ConfirmSheet>
            )}
        </main>
    );
}
