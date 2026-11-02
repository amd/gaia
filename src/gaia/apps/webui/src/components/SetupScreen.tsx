// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronRight, Cloud, Info, Loader2 } from 'lucide-react';
import * as api from '../services/api';
import { shortModelName, useModelStore } from '../stores/modelStore';
import { lemonadeKeyRejected } from '../utils/lemonade';
import { CloudPrices } from './settings/CloudPrices';
import './settings/CloudSpending.css';
import { ModelsStep, ReadySummary, SystemCheck } from './SetupSteps';
import { StartCards } from './StartCards';
import { useGatewayNetwork } from '../hooks/useGatewayNetwork';
import { useT, type Translate } from '../i18n/useT';
import { isMessageKey, type MessageKey } from '../i18n/registry';
import { ConfirmSheet } from './setup/ConfirmSheet';
import { DownloadContents } from './setup/DownloadContents';
import { DownloadProgress, downloadsActive } from './setup/DownloadProgress';
import { formatFitGb, formatSize } from './setup/format';
import { GpuMemoryPanel } from './setup/GpuMemoryPanel';
import { tooBigText } from './setup/ModelChecklist';
import { SetupChecks } from './setup/SetupChecks';
import { SetupRail, type RailStep } from './setup/SetupRail';
import { TestStep } from './setup/TestStep';
import {
    canCreateImages, catalogOf, extraModels, initialSelection, isEmbedding, isOptional, overallFromPlan,
    pendingDownloads, resolveVision, savedVision, shortfall, skipChanges, toggle, tooBig, totals, type Selection,
} from './setup/selection';
import type {
    GpuMemoryStatus, LemonadeFound, LemonadeTarget, PreflightReport, ProviderId, SetupCatalogEntry,
    SetupConfirm, SetupModelProgress, SetupPlan, SetupProblem, SetupRunStatus, SetupTest, StartCard,
} from '../types';
import './SetupScreen.css';
import './setup/setup.css';

const FIREWORKS_KEYS_URL = 'https://app.fireworks.ai/settings/users/api-keys';
const GATEWAY_GUIDE_URL = 'https://amd-gaia.ai/docs/guides/llm-gateway';

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
function ExistingLemonadeOffer({ onChosen, onNothingToOffer }: { onChosen: (mode: 'external' | 'gaia') => void; onNothingToOffer?: () => void }) {
    const t = useT();
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
            if (!target.offer) {
                onNothingToOffer?.();
                return;
            }
            setChecking(true);
            const { found, unchecked: missed = [] } = await api.detectLemonade();
            if (!live.current) return;
            setUnchecked(found ? [] : missed);
            if (found && OFFERED.includes(found.status)) {
                setOffer({ target, found });
                setNeedsKey(found.status === 'needs_key');
                if (found.status === 'too_old') onNothingToOffer?.();
            } else {
                onNothingToOffer?.();
            }
        } catch (err) {
            if (live.current) setCheckError(err instanceof Error ? err.message : String(err));
            onNothingToOffer?.();
        } finally {
            if (live.current) setChecking(false);
        }
    }, [onNothingToOffer]);

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
                        ? <>{t('setup.offer.checkFailed')} <bdi dir="ltr">{checkError}</bdi></>
                        : t('setup.offer.noAnswer', { url: unchecked.join(', ') })}
                </span>
                <button type="button" className="btn-secondary" disabled={checking} onClick={() => void detect()}>
                    {checking && <Loader2 size={13} className="spin" />} {t('setup.offer.checkAgain')}
                </button>
            </div>
        );
    }
    if (!offer) {
        return checking ? (
            <p className="setup-hint" role="status">
                <Loader2 size={13} className="spin" aria-hidden="true" /> {t('setup.offer.looking')}
            </p>
        ) : null;
    }
    const { target, found } = offer;
    if (chosen) {
        return (
            <p className="setup-hint" role="status">
                {chosen === 'external' ? t('setup.offer.usingYours', { url: found.url }) : t('setup.offer.usingOwn')}
            </p>
        );
    }
    if (found.status === 'too_old') {
        return <p className="setup-hint"><bdi dir="ltr">{found.message}</bdi></p>;
    }
    return (
        <form className="setup-lemonade" aria-label={t('setup.offer.label')} onSubmit={(e) => { e.preventDefault(); void choose('external'); }}>
            <span className="setup-choice-title">
                {t('setup.offer.title')} <bdi dir="ltr">{found.url}{found.version ? ` (v${found.version})` : ''}</bdi>
            </span>
            <span className="setup-choice-note"><bdi dir="ltr">{target.what_gaia_does}</bdi></span>
            {needsKey && (
                <input
                    className="settings-input"
                    type="password"
                    autoComplete="off"
                    value={key}
                    onChange={(e) => setKey(e.target.value)}
                    placeholder={t('setup.offer.keyPlaceholder')}
                    aria-label={t('setup.offer.keyLabel')}
                />
            )}
            <div className="setup-row">
                <button type="submit" className="btn-primary" disabled={busy || (needsKey && !key.trim())}>
                    {busy && <Loader2 size={13} className="spin" />} {t('setup.offer.useMine')}
                </button>
                <button type="button" className="btn-secondary" disabled={busy} onClick={() => void choose('gaia')}>
                    {t('setup.offer.useOwn')}
                </button>
            </div>
            {error && <p className="setup-error" role="alert">{error}</p>}
        </form>
    );
}

/** The user's Lemonade isn't answering: start it, or set up GAIA's own instead. */
function ServerProblem({ problem, onRetry, onUseGaia }: { problem: SetupProblem; onRetry: () => void; onUseGaia: () => void }) {
    const t = useT();
    return (
        <>
            {problem.start && <p className="setup-hint"><bdi dir="ltr">{problem.start}</bdi></p>}
            <div className="setup-row">
                <button type="button" className="btn-primary" onClick={onRetry}>{t('setup.tryAgain')}</button>
                {problem.use_gaia && (
                    <button type="button" className="btn-secondary" onClick={onUseGaia}>{t('setup.server.useGaia')}</button>
                )}
            </div>
            {problem.use_gaia && (
                <p className="setup-hint">{t('setup.server.useGaiaNote')}</p>
            )}
            {problem.switch_hint && <p className="setup-hint"><bdi dir="ltr">{problem.switch_hint}</bdi></p>}
        </>
    );
}

/** A stopped run: the words for a problem GAIA understands, the raw output behind Details. */
function RunError({ run }: { run: SetupRunStatus }) {
    const t = useT();
    if (run.problem && run.error) return <p className="setup-error" role="alert"><bdi dir="ltr">{run.error}</bdi></p>;
    return (
        <div className="setup-error" role="alert">
            <p>{t('setup.stopped')}</p>
            {run.error && (
                <details className="dl-details">
                    <summary>{t('setup.progress.details')}</summary>
                    <pre dir="ltr">{run.error}</pre>
                </details>
            )}
        </div>
    );
}

interface SetupScreenProps {
    /** Setup is done; `first` is a task the user picked to start with. */
    onReady: (first?: StartCard) => void;
    /** Set when setup runs again from Settings: leaves it with nothing changed. */
    onClose?: () => void;
}

/** Where the user is. Download and Test are one run; the run's rows say which. */
type Phase = 'check' | 'plan' | 'pc' | 'gpu' | 'models' | 'key' | 'download';

/** What a confirmation sheet is asking. */
type Confirm =
    | { kind: 'no_fit'; models: SetupCatalogEntry[] }
    | { kind: 'low_disk' }
    | { kind: 'server'; detail: SetupConfirm; then: 'chat' | 'run' }
    | { kind: 'server'; detail: SetupConfirm; then: 'retry'; id: string };

function errorText(err: unknown): string {
    return err instanceof Error ? err.message : String(err);
}

/**
 * First run: GAIA checks this PC by itself and recommends a setup for it. One
 * button takes that setup; Customize opens every choice as extra steps in the
 * rail. Nothing downloads until the user has said so, and setup is ready only
 * when the Test step has run everything for real.
 */
export function SetupScreen({ onReady, onClose }: SetupScreenProps) {
    const t = useT();
    const refreshModel = useModelStore((s) => s.refresh);
    const selectModel = useModelStore((s) => s.select);
    const [choice, setChoice] = useState<ProviderId>('local');
    const [phase, setPhase] = useState<Phase>('check');
    const [customizing, setCustomizing] = useState(false);
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
    const [cloudModel, setCloudModel] = useState<string | null>(null);
    const [firstTasks, setFirstTasks] = useState<StartCard[]>([]);
    // Optional models added from the Download step, fetched and tested after the run.
    const [queued, setQueued] = useState<string[]>([]);
    // GAIA uses a Lemonade the user runs, so the Connect step changes nothing there.
    const [usersServer, setUsersServer] = useState(false);
    // What the last run was started with, so Resume and Retry repeat it.
    const lastRun = useRef<{ gaiaLemonade: boolean; options: api.SetupRunOptions }>({ gaiaLemonade: false, options: {} });
    const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const serverAsked = useRef(false);
    const extending = useRef(false);
    const [statusRead, setStatusRead] = useState(false);
    const [lemonadeSettled, setLemonadeSettled] = useState(false);
    const lemonadeNothing = useCallback(() => setLemonadeSettled(true), []);
    // A connect waiting on an OS prompt stops asking once setup is gone.
    const mounted = useRef(true);
    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; };
    }, []);
    const { network: gateway, recheck: recheckGateway } = useGatewayNetwork();
    const unreachable = (id: ProviderId) => id === 'amd' && gateway?.reachable === false;

    const cloud = choice !== 'local';
    const serverRun = run?.server_only ? run : null;
    const fullRun = run && !run.server_only ? run : null;
    const running = run?.state === 'running' || run?.state === 'verifying';
    const installed = fullRun?.state === 'ready';
    const moving = downloadsActive(run);
    const testing = (fullRun?.tests?.length ?? 0) > 0 || fullRun?.state === 'verifying';

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
                // Run again starts over; only a run still going is picked up.
                if (onClose && s.state !== 'running' && s.state !== 'verifying') return;
                setRun(s);
                if (!s.server_only) setPhase('download');
                // A cloud run with no saved choice can't say which provider; the
                // key step lets the user switch if this guess is wrong.
                if (s.skip_chat_model) setChoice(readChoice() === 'amd' ? 'amd' : 'fireworks');
            })
            .catch((err) => setStartError(`Could not read setup progress: ${errorText(err)}`))
            .finally(() => setStatusRead(true));
    }, [loadHardware, loadPlan, onClose]);

    // The plan seeds the selection once, from what is planned and on disk.
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

    // The server came up: read the device and the models against it.
    useEffect(() => {
        if (!serverRun || serverRun.state !== 'ready') return;
        loadHardware();
        void loadPlan();
    }, [serverRun, serverRun?.state, loadHardware, loadPlan]);

    // Setup finished: read what is on disk now, for the summary.
    useEffect(() => {
        if (!installed) return;
        loadHardware();
        void loadPlan().then((p) => { if (p) setFinalPlan(p); });
        void loadGpu();
    }, [installed, loadHardware, loadPlan, loadGpu]);

    const start = useCallback(async (gaiaLemonade = false, options: api.SetupRunOptions = {}): Promise<boolean> => {
        setStartError(null);
        setActionError(null);
        setResumeOffer(false);
        try {
            saveChoice(choice);
            lastRun.current = { gaiaLemonade, options };
            const s = await api.startSetup(cloud, gaiaLemonade, options);
            setRun(s);
            if (!options.serverOnly) setPhase('download');
            if (gaiaLemonade) setUsersServer(false);
            return true;
        } catch (err) {
            const detail = api.setupConfirmFrom(err);
            if (detail) {
                setConfirm({ kind: 'server', detail, then: 'run' });
                return false;
            }
            setStartError(errorText(err));
            return false;
        }
    }, [choice, cloud]);

    // The checks start the model server themselves: it downloads no model.
    useEffect(() => {
        if (phase !== 'check' || !statusRead || !lemonadeSettled || !plan || plan.lemonade.reachable || run || serverAsked.current) return;
        serverAsked.current = true;
        void start(false, { serverOnly: true });
    }, [phase, statusRead, lemonadeSettled, plan, run, start]);

    // The checks finished: on to the recommendation, with nothing to click.
    useEffect(() => {
        if (phase === 'check' && selection && plan?.catalog && plan.lemonade.reachable && (hardware || hardwareError)) setPhase('plan');
    }, [phase, selection, plan, hardware, hardwareError]);

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

    /** Save the choices (chat model, vision model, skips), then download. */
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
                const vision = savedVision(catalog, selection);
                if (vision !== (latest.vision_model ?? null)) latest = await api.chooseSetupVisionModel(vision);
                for (const change of skipChanges(latest, selection)) {
                    latest = await api.skipSetupModel(change.id, change.skipped);
                }
            }
            setPlan(latest);
            download(acknowledgeLowDisk, latest);
        } catch (err) {
            setActionError(errorText(err));
        } finally {
            setBusy(false);
        }
    }, [plan, selection, cloud, noFitAcked, download]);

    const backToPlan = useCallback(() => {
        setActionError(null);
        setCustomizing(false);
        setChoice('local');
        setPhase('plan');
    }, []);

    const customize = useCallback(() => {
        setActionError(null);
        setChoice('local');
        setCustomizing(true);
        setPhase('pc');
    }, []);

    const useCloud = useCallback(() => {
        setActionError(null);
        setCustomizing(false);
        setChoice(readChoice() === 'amd' ? 'amd' : 'fireworks');
        setPhase('key');
    }, []);

    const showGpu = !!gpu?.applies;
    const backOne = useCallback(() => {
        setActionError(null);
        setPhase((p) => (p === 'models' ? (showGpu ? 'gpu' : 'pc') : p === 'gpu' ? 'pc' : 'plan'));
    }, [showGpu]);

    // Alt+Left goes back a step, like the Back button.
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if (!e.altKey || e.key !== 'ArrowLeft' || confirm) return;
            if (phase === 'pc' || phase === 'gpu' || phase === 'models' || phase === 'key') {
                e.preventDefault();
                if (phase === 'pc' || phase === 'key') backToPlan();
                else backOne();
            }
        };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [phase, backOne, backToPlan, confirm]);

    const cancel = useCallback(async () => {
        try {
            await api.cancelSetup();
            await poll();
        } catch (err) {
            setActionError(errorText(err));
        }
    }, [poll]);

    /** One model again: Retry and Resume continue from the bytes on disk; a failed load
     *  re-downloads, once the user confirmed it in the row's sheet. */
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
            // Finish repeats the last run, which must not fetch this one again.
            const { options } = lastRun.current;
            lastRun.current.options = { ...options, extraModels: options.extraModels?.filter((m) => m !== id) };
            setSelection((s) => (s && s.ticked.has(id) ? toggle(s, catalogOf(plan), id, false) : s));
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
            download(false);
        } catch (err) {
            setKeyState({ busy: false, error: errorText(err), done: false });
        }
    }, [choice, key, selectModel, usersServer, download]);

    const switchCloud = useCallback((id: ProviderId) => {
        saveChoice(id);
        setChoice(id);
        setKeyState({ busy: false, error: null, done: false });
    }, []);

    const finish = useCallback(async (first?: StartCard) => {
        await refreshModel();
        onReady(first);
    }, [onReady, refreshModel]);

    // Extras added during the run: fetched and tested the same way, once it ends.
    useEffect(() => {
        if (!installed || queued.length === 0 || extending.current) return;
        const extras = queued;
        extending.current = true;
        void start(false, { keepTrying, extraModels: extras }).then((started) => {
            extending.current = false;
            // Unstarted, they stay listed, and the error says why.
            setQueued((q) => q.filter((id) => !extras.includes(id)));
        });
    }, [installed, queued, keepTrying, start]);

    const canFinish = installed && queued.length === 0;
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

    const runFailed = run?.state === 'failed' || run?.state === 'cancelled';
    const downloadDone = fullRun && (fullRun.state === 'failed' || fullRun.state === 'cancelled')
        && (fullRun.models ?? []).length > 0
        && (fullRun.models ?? []).every((m) => m.state === 'ready' || m.state === 'skipped' || m.state === 'verifying' || m.state === 'unchecked')
        && !moving;

    const catalog = catalogOf(plan);
    const pending = selection ? pendingDownloads(catalog, selection) : [];
    const short = plan && selection ? shortfall(plan, selection) : null;
    const chatEntry = catalog.find((e) => e.id === selection?.defaultChat);

    const blocked = !!hardware && !hardware.compatible && hardware.blockers.length > 0;

    const stage: 'check' | 'plan' | 'pc' | 'gpu' | 'models' | 'key' | 'download' | 'test' | 'ready' =
        canFinish ? 'ready' : phase === 'download' && testing ? 'test' : phase;
    const trail: Array<{ id: typeof stage; label: string; sub?: boolean }> = [
        { id: 'check', label: t('setup.check.title') },
        { id: 'plan', label: t('setup.plan.step') },
        ...(customizing ? [
            { id: 'pc' as const, label: t('setup.pc.step'), sub: true },
            ...(showGpu ? [{ id: 'gpu' as const, label: t('setup.gpu.step'), sub: true }] : []),
            { id: 'models' as const, label: t('setup.models.step'), sub: true },
        ] : []),
        ...(cloud ? [{ id: 'key' as const, label: t('setup.key.step'), sub: true }] : []),
        { id: 'download', label: t('setup.download.step') },
        { id: 'test', label: t('setup.test.step') },
        { id: 'ready', label: t('setup.ready.step') },
    ];
    const at = trail.findIndex((s) => s.id === stage);
    const reopenable = ['plan', 'pc', 'gpu', 'models', 'key'];
    const railSteps: RailStep[] = trail.map((s, i) => ({
        id: s.id,
        label: s.label,
        sub: s.sub,
        status: i < at ? 'done'
            : i > at ? 'pending'
                : stage === 'ready' ? 'done'
                    : runFailed && (stage === 'download' || stage === 'test' || stage === 'check') ? 'failed'
                        : running && (stage === 'download' || stage === 'test') ? 'active' : 'current',
        onOpen: i < at && phase !== 'download' && reopenable.includes(s.id) ? () => setPhase(s.id as Phase) : undefined,
    }));

    const expectedTests: SetupTest['job'][] = ['server'];
    if (!cloud) expectedTests.push('chat');
    if (!cloud && selection && resolveVision(catalog, selection)) expectedTests.push('vision');
    if (selection && catalog.some((e) => isEmbedding(e) && selection.ticked.has(e.id))) expectedTests.push('embedding');
    for (const e of catalog) {
        if (selection && isOptional(e) && selection.ticked.has(e.id) && !e.downloaded) expectedTests.push(e.roles[0]);
    }
    if (!cloud) expectedTests.push('agent');

    const optional = selection && !cloud ? catalog.filter((e) => isOptional(e) && !selection.ticked.has(e.id) && !e.downloaded
        && (canCreateImages(chatEntry) || !e.roles.includes('image_creation'))) : [];
    const biggerOption = catalog.find((e) => e.fits === false && e.chat_choice && e.support === 'verified');

    return (
        <main className="setup" aria-labelledby="setup-title">
            <SetupRail steps={railSteps} />
            <div className="setup-inner">
                {resumeOffer && phase !== 'download' && (
                    <div className="setup-banner" role="status">
                        <Info size={14} aria-hidden="true" />
                        <span>{t('setup.resume.banner')}</span>
                        <span className="setup-row">
                            <button type="button" className="btn-primary" onClick={() => download(false)}>{t('setup.resume.resume')}</button>
                            <button type="button" className="btn-secondary" onClick={() => { setResumeOffer(false); customize(); }}>{t('setup.models.change')}</button>
                        </span>
                    </div>
                )}

                {stage === 'check' && (
                    <>
                        <h1 id="setup-title" className="setup-title">{t('setup.check.heading')}</h1>
                        <p className="setup-lede">{t('setup.check.lede')}</p>
                        <SetupChecks hardware={hardware} hardwareError={hardwareError} plan={plan} serverRun={serverRun} />
                        {statusRead && plan && !plan.lemonade.reachable && !serverRun && (
                            <ExistingLemonadeOffer
                                onNothingToOffer={lemonadeNothing}
                                onChosen={(mode) => { setUsersServer(mode === 'external'); setLemonadeSettled(true); loadHardware(); void loadPlan(); }}
                            />
                        )}
                        {serverRun && runFailed && (
                            <>
                                <RunError run={serverRun} />
                                {serverRun.problem
                                    ? <ServerProblem problem={serverRun.problem} onRetry={() => void start(false, { serverOnly: true })} onUseGaia={() => void start(true, { serverOnly: true })} />
                                    : <button type="button" className="btn-primary setup-start" onClick={() => void start(!!serverRun.gaia_lemonade, { serverOnly: true })}>{t('setup.tryAgain')}</button>}
                            </>
                        )}
                        {planError && !plan && (
                            <>
                                <p className="setup-error" role="alert">{planError}</p>
                                <div className="setup-row">
                                    <button type="button" className="btn-primary" onClick={() => void loadPlan()}>{t('setup.tryAgain')}</button>
                                    <button type="button" className="btn-secondary" onClick={() => void start(false, { keepTrying })}>{t('setup.models.useRecommended')}</button>
                                </div>
                                <p className="setup-hint">{t('setup.models.useRecommendedNote')}</p>
                            </>
                        )}
                        {startError && <p className="setup-error" role="alert">{startError}</p>}
                    </>
                )}

                {stage === 'plan' && plan && selection && (
                    <>
                        <h1 id="setup-title" className="setup-title">{t('setup.plan.title')}</h1>
                        <p className="setup-lede">{t('setup.plan.lede')}</p>
                        <section className="setup-hero" aria-label={t('setup.plan.label')}>
                            <p className="setup-eyebrow">{selection.chatChosen ? t('setup.plan.yours') : t('setup.plan.recommended')}</p>
                            <p className="setup-hero-title"><bdi>{t('setup.plan.run', { name: chatEntry?.name ?? '' })}</bdi></p>
                            <dl className="setup-facts">
                                <div><dt>{t('setup.summary.download')}</dt><dd><bdi dir="ltr">{formatSize(totals(plan, selection).downloadGb ?? 0)}</bdi></dd></div>
                                <div><dt>{t('setup.plan.runsOn')}</dt><dd><bdi>{hardware?.gpu_name ?? t('setup.plan.cpu')}</bdi></dd></div>
                                <div><dt>{t('setup.plan.diskAfter')}</dt><dd><bdi dir="ltr">{totals(plan, selection).afterGb != null ? formatSize(Math.max(0, totals(plan, selection).afterGb as number)) : '—'}</bdi></dd></div>
                            </dl>
                            <ul className="setup-reasons">
                                {!selection.chatChosen && <li>{t('setup.plan.why.evaluated')}</li>}
                                {chatEntry?.need_gb != null && chatEntry.capacity_gb != null && chatEntry.fits && (
                                    <li>{t('setup.plan.why.fits', { need: formatFitGb(chatEntry.need_gb), have: formatFitGb(chatEntry.capacity_gb) })}</li>
                                )}
                                <li>{t('setup.plan.why.private')}</li>
                                {biggerOption && biggerOption.id !== chatEntry?.id && <li><bdi>{biggerOption.name}</bdi>: {tooBigText(biggerOption, t)}</li>}
                            </ul>
                            <div className="setup-row">
                                <button type="button" className="btn-primary" onClick={() => (short ? setConfirm({ kind: 'low_disk' }) : void commit(false))} disabled={busy || blocked}>
                                    {busy && <Loader2 size={13} className="spin" aria-hidden="true" />} {pending.length ? t('setup.plan.start') : t('setup.plan.startNoDownload')}
                                </button>
                                <button type="button" className="link-btn" onClick={customize}>{t('setup.plan.customize')}</button>
                                <button type="button" className="link-btn" onClick={useCloud}>{t('setup.plan.cloud')}</button>
                                {onClose && <button type="button" className="link-btn" onClick={onClose}>{t('setup.again.notNow')}</button>}
                            </div>
                            {blocked && <p className="setup-error" role="alert"><bdi dir="ltr">{hardware?.blockers.join(' ')}</bdi></p>}
                            {short && <p className="setup-error" role="alert">{t('setup.summary.short', { need: formatSize(short.needGb), free: formatSize(short.freeGb), drive: t('setup.summary.theDrive'), freeUp: formatSize(short.freeUpGb) })}</p>}
                            {(actionError ?? startError) && <p className="setup-error" role="alert">{actionError ?? startError}</p>}
                        </section>
                        <details className="setup-disclosure">
                            <summary>{t('setup.plan.whatDownloads')}</summary>
                            <DownloadContents plan={plan} models={pending} />
                        </details>
                    </>
                )}

                {stage === 'pc' && (
                    <>
                        <h1 id="setup-title" className="setup-title">{t('setup.pc.title')}</h1>
                        <p className="setup-lede">{t('setup.pc.lede')}</p>
                        <section className="setup-card"><SystemCheck hardware={hardware} error={hardwareError} /></section>
                        {(plan?.backends?.length ?? 0) > 0 && (
                            <section className="setup-card" aria-label={t('setup.pc.backends')}>
                                <h3 className="mc-job-title">{t('setup.pc.backends')}</h3>
                                <ul className="setup-rows">
                                    {plan?.backends?.map((b) => (
                                        <li key={`${b.recipe}:${b.backend}`} className={`setup-result is-${b.state === 'unsupported' ? 'waiting' : 'done'}`}>
                                            <span className="setup-result-label"><bdi dir="ltr">{b.recipe}</bdi> <bdi dir="ltr">{b.backend}</bdi>{b.default && <span className="mc-pill">{t('setup.models.recommended')}</span>}</span>
                                            <span className="setup-result-value">{t(`setup.pc.backend.${b.state === 'installed' ? 'installed' : b.state === 'unsupported' ? 'unsupported' : 'available'}`)}</span>
                                        </li>
                                    ))}
                                </ul>
                            </section>
                        )}
                        <ExistingLemonadeOffer onChosen={(mode) => { setUsersServer(mode === 'external'); loadHardware(); void loadPlan(); }} />
                        <div className="setup-row setup-nav">
                            <button type="button" className="btn-secondary" onClick={backToPlan}>{t('setup.back')}</button>
                            <button type="button" className="btn-primary" onClick={() => setPhase(showGpu ? 'gpu' : 'models')}>{t('setup.continue')}</button>
                        </div>
                    </>
                )}

                {stage === 'gpu' && gpu && (
                    <>
                        <h1 id="setup-title" className="setup-title">{t('setup.gpu.title')}</h1>
                        <p className="setup-lede">{t('setup.gpu.lede')}</p>
                        <GpuMemoryPanel
                            status={gpu}
                            onStatus={setGpu}
                            mode="setup"
                            headingId="setup-title"
                            names={names}
                            onBack={backOne}
                            onSkip={() => setPhase('models')}
                            onContinue={() => setPhase('models')}
                        />
                    </>
                )}

                {stage === 'models' && (
                    <>
                        <h1 id="setup-title" className="setup-title">{t('setup.models.title')}</h1>
                        <ModelsStep
                            plan={plan}
                            planError={planError}
                            onRetryPlan={() => void loadPlan()}
                            onUseRecommended={() => void start(false, { keepTrying })}
                            selection={selection}
                            onSelection={setSelection}
                            cloud={false}
                            keepTrying={keepTrying}
                            onKeepTrying={setKeepTrying}
                            onBack={backOne}
                            onContinue={(ack) => void commit(ack)}
                            busy={busy}
                            error={actionError ?? startError}
                        />
                    </>
                )}

                {stage === 'key' && (
                    <form onSubmit={(e) => { e.preventDefault(); void connect(); }} className="setup-form">
                        <h1 id="setup-title" className="setup-title">{t('setup.key.title')}</h1>
                        <div className="setup-choices" role="radiogroup" aria-label={t('setup.key.provider')}>
                            {CHOICES.filter((c) => c.id !== 'local').map((c) => (
                                <button
                                    key={c.id}
                                    type="button"
                                    role="radio"
                                    aria-checked={choice === c.id}
                                    className={`setup-choice${choice === c.id ? ' is-active' : ''}${unreachable(c.id) ? ' is-disabled' : ''}`}
                                    onClick={() => switchCloud(c.id)}
                                    disabled={keyState.busy || unreachable(c.id)}
                                >
                                    <Cloud size={16} aria-hidden="true" />
                                    <span>
                                        <span className="setup-choice-title"><bdi>{choiceTitle(c, t)}</bdi></span>
                                        <span className="setup-choice-note">{unreachable(c.id) ? t('setup.key.gatewayOff') : t(c.note)}</span>
                                    </span>
                                </button>
                            ))}
                        </div>
                        {gateway?.reachable === false && (
                            <button type="button" className="link-btn setup-start" onClick={recheckGateway}>{t('setup.key.checkAgain')}</button>
                        )}
                        {choice === 'fireworks' && !usersServer && (
                            <section className="setup-card">
                                <h3 className="mc-job-title">{t('setup.key.getOne')}</h3>
                                <ol className="setup-key-steps">
                                    <li><a href={FIREWORKS_KEYS_URL} target="_blank" rel="noreferrer">{t('setup.key.step1')}</a></li>
                                    <li>{t('setup.key.step2')}</li>
                                    <li>{t('setup.key.step3')}</li>
                                </ol>
                            </section>
                        )}
                        {choice === 'amd' && !usersServer && (
                            <section className="setup-card">
                                <p className="setup-hint"><a href={GATEWAY_GUIDE_URL} target="_blank" rel="noreferrer">{t('setup.key.gatewayHelp')}</a></p>
                            </section>
                        )}
                        <CloudPrices provider={choice} limit={3} />
                        <p className="setup-hint">{usersServer ? t('setup.key.usersServer') : t('setup.key.where')}</p>
                        <div className="setup-row">
                            {!usersServer && <input
                                className="settings-input"
                                type="password"
                                autoComplete="off"
                                value={key}
                                onChange={(e) => setKey(e.target.value)}
                                placeholder={t('setup.key.placeholder')}
                                aria-label={t('setup.key.label')}
                            />}
                            <button type="submit" className="btn-primary" disabled={keyState.busy || unreachable(choice)}>
                                {keyState.busy && <Loader2 size={13} className="spin" />} {t('setup.key.connect')}
                            </button>
                        </div>
                        {keyState.busy && !keyState.waiting && <p className="setup-hint" role="status">{t('setup.key.testing')}</p>}
                        {keyState.busy && keyState.waiting && <p className="setup-hint" role="status">{keyState.waiting}</p>}
                        {keyState.error && <p className="setup-error" role="alert">{keyState.error}</p>}
                        <div className="setup-row setup-nav">
                            <button type="button" className="btn-secondary" onClick={backToPlan} disabled={keyState.busy}>{t('setup.back')}</button>
                        </div>
                    </form>
                )}

                {stage === 'download' && fullRun && (
                    <>
                        <h1 id="setup-title" className="setup-title">{t('setup.download.title')}</h1>
                        <p className="setup-lede">{t('setup.download.lede')}</p>
                        {plan && <section className="setup-card" aria-label={t('setup.contents.title')}><DownloadContents plan={plan} run={fullRun} /></section>}
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
                                    labelledBy="setup-title"
                                />
                            </div>
                        ) : running ? (
                            <p className="setup-hint" role="status"><Loader2 size={13} className="spin" aria-hidden="true" /> {fullRun.text}</p>
                        ) : null}
                        {optional.length > 0 && (
                            <section className="setup-card" aria-label={t('setup.optional.title')}>
                                <h3 className="mc-job-title">{t('setup.optional.title')}</h3>
                                <p className="setup-hint">{t('setup.optional.lede')}</p>
                                <ul className="setup-rows">
                                    {optional.map((e) => {
                                        const added = queued.includes(e.id);
                                        return (
                                            <li key={e.id} className="setup-result is-waiting">
                                                <span className="setup-result-label">{t(`setup.purpose.${e.roles[0]}`)}</span>
                                                <span className="setup-result-value">
                                                    <bdi dir="ltr">{e.size_gb != null ? formatSize(e.size_gb) : t('setup.contents.sizeOnDownload')}</bdi>
                                                    <button type="button" className="btn-secondary" aria-pressed={added} onClick={() => setQueued((q) => (added ? q.filter((id) => id !== e.id) : [...q, e.id]))}>
                                                        {added ? t('setup.optional.added') : t('setup.optional.add')}
                                                    </button>
                                                </span>
                                            </li>
                                        );
                                    })}
                                </ul>
                            </section>
                        )}
                        {lostContact && (
                            <div className="setup-row" role="alert">
                                <span className="setup-error">Lost contact with GAIA: {lostContact}</span>
                                <button type="button" className="btn-secondary" onClick={() => void poll()}>{t('setup.reconnect')}</button>
                            </div>
                        )}
                        {runFailed && downloadDone && fullRun.state === 'failed' && !fullRun.model_failed && (
                            fullRun.problem
                                ? <ServerProblem problem={fullRun.problem} onRetry={retryRun} onUseGaia={() => void start(true, lastRun.current.options)} />
                                : (
                                    <div className="setup-error" role="alert">
                                        <p>{t('setup.download.stoppedBeforeCheck')}</p>
                                        {fullRun.error && (
                                            <details className="dl-details">
                                                <summary>{t('setup.progress.details')}</summary>
                                                <pre dir="ltr">{fullRun.error}</pre>
                                            </details>
                                        )}
                                    </div>
                                )
                        )}
                        {runFailed && downloadDone && (
                            <div className="setup-row">
                                <button type="button" className="btn-primary" onClick={retryRun} disabled={busy}>{t('setup.finish')}</button>
                                <span className="setup-hint">{t('setup.download.finishNote')}</span>
                            </div>
                        )}
                        {runFailed && !downloadDone && !(fullRun.models ?? []).some((m) => m.state === 'failed' || m.state === 'cancelled') && (
                            <>
                                <RunError run={fullRun} />
                                {fullRun.problem
                                    ? <ServerProblem problem={fullRun.problem} onRetry={retryRun} onUseGaia={() => void start(true, lastRun.current.options)} />
                                    : <button type="button" className="btn-primary setup-start" onClick={() => void start(!!fullRun.gaia_lemonade, lastRun.current.options)}>{t('setup.tryAgain')}</button>}
                            </>
                        )}
                        {(actionError || startError) && <p className="setup-error" role="alert">{actionError ?? startError}</p>}
                    </>
                )}

                {stage === 'test' && fullRun && (
                    <>
                        <h1 id="setup-title" className="setup-title">{t('setup.test.title')}</h1>
                        <p className="setup-lede">{t('setup.test.lede')}</p>
                        <TestStep
                            run={fullRun}
                            expected={expectedTests}
                            names={names}
                            catalog={catalog}
                            onRetry={retryRun}
                            onChooseAnother={customize}
                            onDownloadAgain={(id) => void retryModel(id, 'verify')}
                            busy={busy}
                        />
                        {runFailed && fullRun.error && (
                            <details className="dl-details">
                                <summary>{t('setup.progress.details')}</summary>
                                <pre dir="ltr">{fullRun.error}</pre>
                            </details>
                        )}
                        {(actionError || startError) && <p className="setup-error" role="alert">{actionError ?? startError}</p>}
                    </>
                )}

                {stage === 'ready' && (
                    <>
                        <h1 id="setup-title" className="setup-title">{t('setup.ready.heading')}</h1>
                        <div className="setup-done" role="status">
                            <div className="setup-done-head">
                                <p>
                                    {t.compose((say) => [
                                        say('setup.ready.title'),
                                        answering ? say(cloud ? 'setup.ready.answersCloud' : 'setup.ready.answersLocal', { model: cloud ? shortModelName(answering) : (names[answering] ?? finalPlan?.catalog?.find((e) => e.id === answering)?.name ?? shortModelName(answering)) }) : null,
                                    ].filter(Boolean).join(' '))}
                                </p>
                                <button type="button" className="btn-primary" onClick={() => void finish()} autoFocus>
                                    {t('setup.ready.start')}
                                </button>
                            </div>
                            {!cloud && finalPlan && <ReadySummary plan={finalPlan} chatModel={answering} gpu={gpu} />}
                            {startError && <p className="setup-error" role="alert">{startError}</p>}
                            {(fullRun?.tests ?? []).filter((r) => r.state === 'failed').map((r) => (
                                <p key={`${r.job}:${r.subject ?? ''}`} className="setup-failed-extra" role="alert">
                                    {t('setup.ready.extraFailed', { job: t(`setup.test.job.${r.job}`) })}
                                </p>
                            ))}
                            {firstTasks.length > 0 && (
                                <>
                                    <p className="setup-hint">{t('setup.ready.tasks')}</p>
                                    <StartCards cards={firstTasks} onPick={(card) => void finish(card)} />
                                </>
                            )}
                        </div>
                    </>
                )}

                {run && run.log_tail && run.log_tail.length > 0 && stage !== 'ready' && (
                    <div className="setup-log">
                        <button type="button" className="setup-log-toggle" onClick={() => setShowLog((v) => !v)} aria-expanded={showLog}>
                            <ChevronRight size={13} className={showLog ? 'is-open' : ''} aria-hidden="true" /> {t('setup.progress.details')}
                        </button>
                        {showLog && <pre className="setup-log-pre" dir="ltr">{run.log_tail.join('\n')}</pre>}
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
            {confirm?.kind === 'low_disk' && short && (
                <ConfirmSheet
                    title={t('setup.confirm.lowDiskTitle')}
                    confirmLabel={t('setup.confirm.downloadAnyway')}
                    cancelLabel={t('setup.confirm.changeModels')}
                    onConfirm={() => { setConfirm(null); void commit(true); }}
                    onCancel={() => { setConfirm(null); customize(); }}
                >
                    <p>{t('setup.summary.short', { need: formatSize(short.needGb), free: formatSize(short.freeGb), drive: t('setup.summary.theDrive'), freeUp: formatSize(short.freeUpGb) })}</p>
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
                    onCancel={() => { const c = confirm; setConfirm(null); if (c.then !== 'retry' && !installed) customize(); }}
                >
                    <p>{confirm.detail.message}</p>
                </ConfirmSheet>
            )}
        </main>
    );
}
