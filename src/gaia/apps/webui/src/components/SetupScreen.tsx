// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { RadioGroup } from './RadioGroup';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Check, ChevronLeft, ChevronRight, Cloud, Eye, EyeOff, Info, Loader2 } from 'lucide-react';
import * as api from '../services/api';
import { displayModelName, shortModelName, useModelStore } from '../stores/modelStore';
import { lemonadeKeyRejected } from '../utils/lemonade';
import { CloudPrices } from './settings/CloudPrices';
import './settings/CloudSpending.css';
import { ModelsStep, ReadySummary } from './SetupSteps';
import { StartCards } from './StartCards';
import { useGatewayNetwork } from '../hooks/useGatewayNetwork';
import { useT, type Translate } from '../i18n/useT';
import { isMessageKey, type MessageKey } from '../i18n/registry';
import { ConfirmSheet } from './setup/ConfirmSheet';
import { DownloadContents } from './setup/DownloadContents';
import { DownloadProgress, downloadsActive } from './setup/DownloadProgress';
import { RunActivity } from './setup/RunActivity';
import { formatFitGb, formatSize, totalMemory } from './setup/format';
import { GpuMemoryPanel } from './setup/GpuMemoryPanel';
import { tooBigText } from './setup/ModelChecklist';
import { StageLogs } from './setup/StageLogs';
import { SetupFooter, SetupFooterSlot } from './setup/SetupFooter';
import { LanguagePicker } from './setup/LanguagePicker';
import { BackendChoice, applyTestedBackend } from './setup/BackendChoice';
import { DetectedSummary } from './setup/DetectedSummary';
import { SetupChecks } from './setup/SetupChecks';
import { SetupRail, type RailStep } from './setup/SetupRail';
import { TestStep } from './setup/TestStep';
import {
    canCreateImages, cantRunHere, catalogOf, extraModels, initialSelection, isEmbedding, isNpu, isOptional, overallFromPlan,
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
function ExistingLemonadeOffer({ onChosen, onNothingToOffer, onFound, canInstallOwn = true }: { onChosen: (mode: 'external' | 'gaia') => void; onNothingToOffer?: () => void; onFound?: (found: LemonadeFound) => void; canInstallOwn?: boolean }) {
    const t = useT();
    const [offer, setOffer] = useState<{ target: LemonadeTarget; found: LemonadeFound } | null>(null);
    const [checkError, setCheckError] = useState<string | null>(null);
    // Something answered the connection but not the question: "couldn't tell", not "none".
    const [unchecked, setUnchecked] = useState<string[]>([]);
    const [checking, setChecking] = useState(false);
    const [key, setKey] = useState('');
    const [needsKey, setNeedsKey] = useState(false);
    const [more, setMore] = useState(false);
    const [busy, setBusy] = useState<'external' | 'gaia' | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [chosen, setChosen] = useState<'external' | 'gaia' | null>(null);
    // GAIA's own runs the version GAIA is tested with, so it is the one preselected.
    const [pick, setPick] = useState<'external' | 'gaia'>('gaia');

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
                onFound?.(found);
                setNeedsKey(found.status === 'needs_key');
                if (found.status === 'too_old') onNothingToOffer?.();
            } else if (missed.length === 0) {
                onNothingToOffer?.();
            }
            // Unanswered or failed: the user decides, so GAIA's own isn't installed beside theirs.
        } catch (err) {
            if (live.current) setCheckError(err instanceof Error ? err.message : String(err));
        } finally {
            if (live.current) setChecking(false);
        }
    }, [onNothingToOffer, onFound]);

    useEffect(() => {
        live.current = true;
        void detect();
        return () => { live.current = false; };
    }, [detect]);

    const choose = useCallback(async (mode: 'external' | 'gaia') => {
        if (!offer) return;
        setBusy(mode);
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
            setBusy(null);
        }
    }, [offer, key, onChosen]);

    if (checkError || unchecked.length > 0) {
        return (
            <>
                <p className="setup-hint">
                    {checkError ? t('setup.offer.checkFailed') : t('setup.offer.noAnswer', { url: unchecked.join(', ') })}
                </p>
                {checkError && (
                    <details className="dl-details">
                        <summary>{t('setup.progress.details')}</summary>
                        <pre dir="ltr">{checkError}</pre>
                    </details>
                )}
                <SetupFooter>
                    <button type="button" className="btn-secondary" disabled={checking} onClick={() => void detect()}>
                        {checking && <Loader2 size={13} className="spin" />} {t('setup.offer.checkAgain')}
                    </button>
                    {/* Only before GAIA's own server starts: after, it is already running. */}
                    {onNothingToOffer && canInstallOwn && (
                        <button type="button" className="btn-primary" disabled={checking} onClick={onNothingToOffer}>
                            {t('setup.offer.useOwn')}
                        </button>
                    )}
                </SetupFooter>
            </>
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
        // Nothing for the user to do: setup carries on with GAIA's own, and theirs is left alone.
        return (
            <p className="setup-hint" role="status">
                {t('setup.offer.tooOld')}{' '}
                <bdi dir="ltr" className="setup-choice-note">Lemonade {found.version ?? ''} · {found.url}</bdi>
            </p>
        );
    }
    const options: Array<{ mode: 'gaia' | 'external'; title: MessageKey; note: MessageKey }> = [
        { mode: 'gaia', title: 'setup.offer.useOwn', note: 'setup.offer.ownNote' },
        { mode: 'external', title: 'setup.offer.useMine', note: 'setup.offer.mineNote' },
    ];
    return (
        <form id="setup-lemonade-form" className="setup-lemonade" aria-label={t('setup.offer.label')} onSubmit={(e) => { e.preventDefault(); void choose(pick); }}>
            <fieldset className="setup-choices" disabled={!!busy}>
                <legend className="setup-choice-title">{t('setup.offer.title')}</legend>
                {options.map((o) => (
                    <label key={o.mode} className={`setup-choice${pick === o.mode ? ' is-active' : ''}`}>
                        <input type="radio" name="setup-server" value={o.mode} checked={pick === o.mode} onChange={() => { setPick(o.mode); setError(null); }} />
                        <span>
                            <span className="setup-choice-title">
                                {t(o.title)}
                                {o.mode === 'gaia' && <span className="setup-choice-tag">{t('setup.offer.recommended')}</span>}
                            </span>
                            <span className="setup-choice-note">{t(o.note)}</span>
                        </span>
                    </label>
                ))}
            </fieldset>
            <button type="button" className="setup-log-toggle setup-offer-more" onClick={() => setMore((m) => !m)} aria-expanded={more} aria-controls="setup-offer-detail">
                <ChevronRight size={13} className={more ? 'is-open' : ''} aria-hidden="true" /> {t('setup.offer.whatGaiaDoes')}
            </button>
            {more && (
                <div id="setup-offer-detail">
                    <p className="setup-choice-note"><bdi dir="ltr">Lemonade{found.version ? ` ${found.version}` : ''} · {found.url}</bdi></p>
                    <p className="setup-choice-note">{t('setup.offer.whatGaiaDoesText')}</p>
                </div>
            )}
            {pick === 'external' && needsKey && (
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
            {/* A rejected key names the setting it came from; say what to do instead. */}
            {error && (needsKey && pick === 'external'
                ? <p className="setup-error" role="alert">{t('setup.offer.keyRejected')}</p>
                : <SetupAlert text={error} />)}
            <SetupFooter>
                <button type="submit" form="setup-lemonade-form" className="btn-primary" disabled={!!busy || (pick === 'external' && needsKey && !key.trim())}>
                    {busy && <Loader2 size={13} className="spin" />} {t('setup.continue')}
                </button>
            </SetupFooter>
        </form>
    );
}

/** The user's Lemonade isn't answering: start it, or set up GAIA's own instead. */
function ServerProblem({ problem, onRetry, onUseGaia }: { problem: SetupProblem; onRetry: () => void; onUseGaia: () => void }) {
    const t = useT();
    // A too-old server is upgraded outside GAIA, so switching to GAIA's own is the way on.
    const switchFirst = problem.kind === 'server_too_old' && problem.use_gaia;
    return (
        <>
            {/* How to start it can be a command, so it waits behind Details. */}
            {problem.start && (
                <details className="dl-details">
                    <summary>{t('setup.server.howToStart')}</summary>
                    <pre dir="ltr">{problem.start}</pre>
                </details>
            )}
            <SetupFooter>
                <button type="button" className={switchFirst ? 'btn-secondary' : 'btn-primary'} onClick={onRetry}>{t('setup.tryAgain')}</button>
                {problem.use_gaia && (
                    <button type="button" className={switchFirst ? 'btn-primary' : 'btn-secondary'} onClick={onUseGaia}>{t('setup.server.useGaia')}</button>
                )}
            </SetupFooter>
            {problem.use_gaia && (
                <p className="setup-hint">{t('setup.server.useGaiaNote')}</p>
            )}
            {problem.use_gaia && problem.env_url && (
                <p className="setup-hint">{t('setup.server.envIgnoredNote', { url: problem.env_url })}</p>
            )}
        </>
    );
}

/** A failure only the backend's words name: a plain sentence, those words behind Details. */
function SetupAlert({ text }: { text: string }) {
    const t = useT();
    return (
        <div className="setup-error" role="alert">
            <p>{t('setup.failedLead')}</p>
            <details className="dl-details">
                <summary>{t('setup.progress.details')}</summary>
                <pre dir="ltr">{text}</pre>
            </details>
        </div>
    );
}

/** A stopped run: the words for a problem GAIA understands, the raw output behind Details. */
function RunError({ run }: { run: SetupRunStatus }) {
    const t = useT();
    if (run.problem) {
        return (
            <div className="setup-error" role="alert">
                <p>{t(run.problem.kind === 'server_too_old' ? 'setup.server.tooOld' : 'setup.server.unreachable')}</p>
                <details className="dl-details">
                    <summary>{t('setup.progress.details')}</summary>
                    <pre dir="ltr">{run.problem.message}</pre>
                </details>
            </div>
        );
    }
    return (
        <div className="setup-error" role="alert">
            {/* GAIA's own model server is downloaded first, so a failure there is usually the internet. */}
            <p>{t(run.components?.server === 'failed' ? 'setup.server.ownFailed' : 'setup.stopped')}</p>
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
    /** Opened from Settings (run setup again, or add local models): offers a way back, and ends with the installed model in use. */
    onClose?: () => void;
}

/** Where the user is. Download and Test are one run; the run's rows say which. */
type Phase = 'check' | 'plan' | 'gpu' | 'models' | 'key' | 'download';
const SCREEN_ORDER: Stage[] = ['check', 'plan', 'key', 'models', 'gpu', 'download', 'test', 'ready'];
type Stage = Phase | 'test' | 'ready';

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
    // GPU memory isn't a step: it opens from Check this PC or Customize, and goes back there.
    const [gpuFrom, setGpuFrom] = useState<'check' | 'models'>('models');
    const [footerSlot, setFooterSlot] = useState<HTMLElement | null>(null);
    /** A step opened from the rail or Back/Next while setup is further on; null shows where setup is. */
    const [viewing, setViewing] = useState<Stage | null>(null);
    /** Steps already shown, so Next and the rail can return to them. */
    const [visited, setVisited] = useState<Set<Stage>>(() => new Set<Stage>(['check']));
    // Where setup really is, read by stepTo before this render computes it.
    const liveRef = useRef<Stage>('check');
    const [hardware, setHardware] = useState<PreflightReport | null>(null);
    const [hardwareError, setHardwareError] = useState<string | null>(null);
    const [run, setRun] = useState<SetupRunStatus | null>(null);
    const [lostContact, setLostContact] = useState<string | null>(null);
    const [startError, setStartError] = useState<string | null>(null);
    const [key, setKey] = useState('');
    const [keyState, setKeyState] = useState<{ busy: boolean; error: string | null; done: boolean; waiting?: string | null }>({ busy: false, error: null, done: false });
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
    /** The cloud model the user picked from the price list; null keeps the provider's top pick. */
    const [cloudPick, setCloudPick] = useState<string | null>(null);
    /** The price list's top row, shown as chosen until another is picked; null without a priced list. */
    const [cloudDefault, setCloudDefault] = useState<string | null>(null);
    const [showKey, setShowKey] = useState(false);
    const [firstTasks, setFirstTasks] = useState<StartCard[]>([]);
    // Optional models added from the Download step, fetched and tested after the run.
    const [queued, setQueued] = useState<string[]>([]);
    /** Extras added during the run that never started, e.g. after a low-disk No. */
    const [notFetched, setNotFetched] = useState<string[]>([]);
    // GAIA uses a Lemonade the user runs, so the Connect step changes nothing there.
    const [usersServer, setUsersServer] = useState(false);
    /** GAIA restarted during a download; the plan offers to pick it up. */
    const [interrupted, setInterrupted] = useState(false);
    // What the last run was started with, so Resume and Retry repeat it.
    const lastRun = useRef<{ gaiaLemonade: boolean; options: api.SetupRunOptions }>({ gaiaLemonade: false, options: {} });
    const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const serverAsked = useRef(false);
    const extending = useRef(false);
    const [statusRead, setStatusRead] = useState(false);
    const [lemonadeSettled, setLemonadeSettled] = useState(false);
    const lemonadeNothing = useCallback(() => setLemonadeSettled(true), []);
    /** The user's own Lemonade the checks found, while they choose; then what they chose. */
    const [lemonadeFound, setLemonadeFound] = useState<LemonadeFound | null>(null);
    const [lemonadeChoice, setLemonadeChoice] = useState<'external' | 'gaia' | null>(null);
    // A connect waiting on an OS prompt stops asking once setup is gone.
    const mounted = useRef(true);
    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; };
    }, []);
    const { network: gateway, providers, recheck: recheckGateway } = useGatewayNetwork();
    const providerFor = (id: ProviderId) => providers.find((p) => p.id === id);
    const chosenProvider = providerFor(choice);
    const envKey = chosenProvider?.key_source === 'environment' && chosenProvider.key_hint
        ? chosenProvider
        : undefined;
    const cloudProviderBlocked = (id: ProviderId) => !!providerFor(id)?.blocked;
    // The gateway is for AMD's network: hidden once it is known not to answer, unless already chosen.
    const gatewayShown = gateway?.reachable !== false || choice === 'amd';
    // Only a key GAIA or the server already holds can be left blank.
    const savedKey = chosenProvider?.key_source === 'stored' || chosenProvider?.key_source === 'lemonade';
    // Local only is on: no cloud provider can be picked, so the cloud card says why instead.
    const cloudBlocked = cloudProviderBlocked('fireworks') && cloudProviderBlocked('amd');
    const unavailable = (id: ProviderId) => cloudProviderBlocked(id) || (id === 'amd' && gateway?.reachable === false);
    const unavailableNote = (id: ProviderId) => cloudProviderBlocked(id)
        ? t('settings.privacy.localOnlyBlocked')
        : t('setup.key.gatewayOff');

    const cloud = choice !== 'local';
    const serverRun = run?.server_only ? run : null;
    const fullRun = run && !run.server_only ? run : null;
    const running = run?.state === 'running' || run?.state === 'verifying';
    const installed = fullRun?.state === 'ready';
    const moving = downloadsActive(run);
    // A model downloading again shows on the Download step, not under old test results.
    const pulling = (fullRun?.models ?? []).some((m) => m.state === 'downloading' || m.state === 'queued');
    const testing = !pulling && ((fullRun?.tests?.length ?? 0) > 0 || fullRun?.state === 'verifying');

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

    // A boolean, not the callback: a parent's new arrow each render would rerun every read.
    const again = onClose != null;
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
                if (again && s.state !== 'running' && s.state !== 'verifying') return;
                setRun(s);
                if (!s.server_only) setPhase('download');
                // A cloud run with no saved choice can't say which provider; the
                // key step lets the user switch if this guess is wrong.
                if (s.skip_chat_model) setChoice(readChoice() === 'amd' ? 'amd' : 'fireworks');
            })
            .catch((err) => setStartError(errorText(err)))
            .finally(() => setStatusRead(true));
    }, [loadHardware, loadPlan, again]);

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
            // GAIA restarted and lost the run: back to the plan, which offers to resume.
            if (s.state === 'idle' && !s.models?.length) {
                setRun(null);
                setPhase('plan');
                setInterrupted(true);
                void loadPlan();
                return;
            }
            setRun(s);
        } catch (err) {
            setLostContact(errorText(err));
        }
    }, [loadPlan, t]);

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

    // A second click while the first start is on its way would start a second run.
    const starting = useRef(false);
    const start = useCallback(async (gaiaLemonade = false, options: api.SetupRunOptions = {}): Promise<boolean> => {
        if (starting.current) return false;
        starting.current = true;
        setStartError(null);
        setActionError(null);
        setResumeOffer(false);
        setInterrupted(false);
        try {
            saveChoice(choice);
            lastRun.current = { gaiaLemonade, options };
            const s = await api.startSetup(cloud, gaiaLemonade, options);
            setRun(s);
            setViewing(null);
            const extras = options.extraModels ?? [];
            if (extras.length) setNotFetched((n) => n.filter((id) => !extras.includes(id)));
            if (!options.serverOnly) setPhase('download');
            if (gaiaLemonade) setUsersServer(false);
            return true;
        } catch (err) {
            const detail = api.setupConfirmFrom(err);
            if (detail) {
                setConfirm({ kind: 'server', detail, then: 'run' });
                return false;
            }
            // Another window, or an earlier click, already started it: show that run.
            if (err instanceof api.ApiError && err.httpStatus === 409) {
                const live = await api.getSetupStatus().catch(() => null);
                if (live && (live.state === 'running' || live.state === 'verifying')) {
                    setRun(live);
                    setViewing(null);
                    if (!live.server_only) setPhase('download');
                    return true;
                }
            }
            setStartError(errorText(err));
            return false;
        } finally {
            starting.current = false;
        }
    }, [choice, cloud]);

    // The checks start the model server themselves: it downloads no model.
    useEffect(() => {
        if (phase !== 'check' || !statusRead || !lemonadeSettled || !plan || plan.lemonade.reachable || run || serverAsked.current) return;
        serverAsked.current = true;
        void start(false, { serverOnly: true });
    }, [phase, statusRead, lemonadeSettled, plan, run, start]);

    // The checks finished: they stop on what they found, for the user to check.
    const checksDone = !!(selection && plan?.catalog && plan.lemonade.reachable && (hardware || hardwareError));

    const retryRun = useCallback(() => {
        const { gaiaLemonade, options } = lastRun.current;
        void start(gaiaLemonade, options);
    }, [start]);

    /** Download with the selection: the plan's models, minus skips, plus ticked extras. */
    const download = useCallback((acknowledgeLowDisk: boolean, latest: SetupPlan | null = plan) => {
        setStartPlan(latest);
        const extras = latest && selection && !cloud ? extraModels(latest, selection) : [];
        return start(false, { acknowledgeLowDisk, keepTrying, extraModels: extras });
    }, [plan, selection, cloud, keepTrying, start]);

    const [ackDisk, setAckDisk] = useState(false);

    /** Save the choices (chat model, vision model, skips), then download. */
    const committing = useRef(false);
    const commit = useCallback(async (acknowledgeLowDisk: boolean, acknowledgeNoFit = noFitAcked) => {
        if (!plan || !selection || committing.current) return;
        setActionError(null);
        setAckDisk(acknowledgeLowDisk);
        const catalog = catalogOf(plan);
        const big = cloud ? [] : tooBig(catalog, selection).filter((e) => !e.downloaded || e.id === selection.defaultChat);
        // Nothing here can run it: no confirm can make it work, so it blocks the download.
        const cantRun = big.find(cantRunHere);
        if (cantRun) {
            const key = cantRun.id === selection.defaultChat ? 'setup.models.cantRun.chat' : 'setup.models.cantRun.other';
            setActionError(t(key, { name: cantRun.name, why: tooBigText(cantRun, t) }));
            return;
        }
        if (!cloud && !acknowledgeNoFit) {
            if (big.length > 0 && big.every((e) => e.need_gb != null)) {
                setConfirm({ kind: 'no_fit', models: big });
                return;
            }
        }
        setBusy(true);
        committing.current = true;
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
                // Whatever the choice, llama.cpp runs on the backend GAIA has tested.
                await applyTestedBackend();
                const vision = savedVision(catalog, selection);
                if (vision !== (latest.vision_model ?? null)) latest = await api.chooseSetupVisionModel(vision);
                for (const change of skipChanges(latest, selection)) {
                    latest = await api.skipSetupModel(change.id, change.skipped);
                }
            }
            setPlan(latest);
            await download(acknowledgeLowDisk, latest);
        } catch (err) {
            setActionError(errorText(err));
        } finally {
            committing.current = false;
            setBusy(false);
        }
    }, [plan, selection, cloud, noFitAcked, download, t]);

    const stepTo = useCallback((id: Stage) => {
        setActionError(null);
        if (phase === 'download') {
            setViewing(id === liveRef.current ? null : id);
            return;
        }
        setViewing(null);
        setPhase(id === 'test' || id === 'ready' ? 'download' : id);
    }, [phase]);

    // Back to the recommendation; a run already going keeps its choice.
    const backToPlan = useCallback(() => {
        if (viewing == null) setChoice('local');
        stepTo('plan');
    }, [stepTo, viewing]);

    const customize = useCallback(() => {
        if (running && viewing != null) return;
        setChoice('local');
        setCustomizing(true);
        stepTo('models');
    }, [stepTo, running, viewing]);

    // The plan screen's two ways to run GAIA; this PC is the one recommended.
    const [where, setWhere] = useState<'local' | 'cloud'>('local');

    const toCloud = useCallback((provider: 'fireworks' | 'amd') => {
        if (running && viewing != null) return;
        setCustomizing(false);
        setChoice(provider);
        stepTo('key');
    }, [stepTo, running, viewing]);
    const useCloud = useCallback(() => toCloud(readChoice() === 'amd' ? 'amd' : 'fireworks'), [toCloud]);

    // Chat already runs in the cloud: carry on there rather than recommend a local download.
    const inUseCloud = useModelStore((s) => (s.active?.remote && s.active.provider !== 'local' ? s.active.provider : null));
    const continueFromChecks = useCallback(() => {
        if (inUseCloud) toCloud(inUseCloud);
        else stepTo('plan');
    }, [inUseCloud, toCloud, stepTo]);

    const showGpu = !!gpu?.applies;
    const backOne = useCallback(() => {
        stepTo((viewing ?? phase) === 'gpu' ? gpuFrom : 'plan');
    }, [gpuFrom, stepTo, viewing, phase]);
    const openGpu = useCallback((from: 'check' | 'models') => {
        setGpuFrom(from);
        stepTo('gpu');
    }, [stepTo]);

    // Alt+Left and Alt+Right go back and on a step, like Back and Next.
    const navRef = useRef<{ prev: Stage | null; next: Stage | null }>({ prev: null, next: null });
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if (!e.altKey || confirm) return;
            const target = e.target instanceof Element ? e.target : null;
            if (target?.closest('input, textarea, select, [contenteditable="true"]')) return;
            const to = e.key === 'ArrowLeft' ? navRef.current.prev : e.key === 'ArrowRight' ? navRef.current.next : null;
            if (!to) return;
            e.preventDefault();
            stepTo(to);
        };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [stepTo, confirm]);

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
        if (running) return;
        setKeyState({ busy: true, error: null, done: false });
        try {
            await api.connectProviderWaitingForOs(
                choice,
                { api_key: (!usersServer && key) || undefined },
                (waiting) => setKeyState({ busy: true, error: null, done: false, waiting }),
                () => !mounted.current,
            );
            const { models } = await api.listProviderModels(choice);
            // Connect the row shown as chosen; only a provider with no price list (the gateway) takes its top pick.
            const wanted = cloudPick ?? cloudDefault;
            const pick = wanted
                ? models.find((m) => m.id === wanted)
                : models.find((m) => m.rank === 1) ?? models[0];
            if (!pick) throw new Error(wanted ? t('setup.key.modelNotOffered', { model: shortModelName(wanted) }) : t('setup.key.noModels'));
            await selectModel(pick.id);
            setCloudModel(pick.id);
            setKey('');
            setKeyState({ busy: false, error: null, done: true });
            void download(false);
        } catch (err) {
            setKeyState({ busy: false, error: errorText(err), done: false });
        }
    }, [choice, key, cloudPick, cloudDefault, selectModel, usersServer, download, t, running]);

    const switchCloud = useCallback((id: ProviderId) => {
        saveChoice(id);
        setChoice(id);
        setCloudPick(null);
        setCloudDefault(null);
        setKeyState({ busy: false, error: null, done: false });
    }, []);

    // Extras added during the run: fetched and tested the same way, once it ends.
    useEffect(() => {
        if (!installed || queued.length === 0 || extending.current) return;
        const extras = queued;
        extending.current = true;
        void start(false, { keepTrying, extraModels: extras }).then((started) => {
            extending.current = false;
            setQueued((q) => q.filter((id) => !extras.includes(id)));
            // Unstarted, the Ready screen says so rather than dropping them.
            if (!started) setNotFetched((n) => [...n, ...extras.filter((id) => !n.includes(id))]);
        });
    }, [installed, queued, keepTrying, start]);

    const canFinish = installed && queued.length === 0;
    const answering = cloud ? cloudModel : fullRun?.chat_model ?? (plan && !plan.decided_after_server ? plan.chat_model.id : null);

    const finish = useCallback(async (first?: StartCard) => {
        setActionError(null);
        try {
            // Chat opens on the model setup set up and tested, not an older pick; a cloud setup keeps its model.
            if (!cloud && answering) await selectModel(answering);
            await refreshModel();
        } catch (err) {
            setActionError(errorText(err));
            return;
        }
        onReady(first);
    }, [answering, cloud, onReady, refreshModel, selectModel]);

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
    // A step that went wrong opens "Having trouble?" by itself.
    const stepFailed = (run?.state === 'failed') || !!startError || !!planError || !!hardwareError || !!lostContact || !!actionError;
    // A first run has no earlier chat to pick up, so "Check in" waits for one.
    const readyTasks = again ? firstTasks : firstTasks.filter((c) => c.skill !== 'check-in');
    const downloadDone = fullRun && (fullRun.state === 'failed' || fullRun.state === 'cancelled')
        && (fullRun.models ?? []).length > 0
        && (fullRun.models ?? []).every((m) => m.state === 'ready' || m.state === 'skipped' || m.state === 'verifying' || m.state === 'unchecked')
        && !moving;
    // Its row in the download list says why and offers Retry, so nothing below repeats it.
    const componentFailed = !!fullRun && runFailed && !fullRun.problem
        && Object.values(fullRun.components ?? {}).includes('failed');

    const catalog = catalogOf(plan);
    const pending = selection ? pendingDownloads(catalog, selection) : [];
    const short = plan && selection ? shortfall(plan, selection) : null;
    const modelsOnPc = selection ? catalog.filter((e) => selection.ticked.has(e.id) && e.downloaded).length : 0;
    // Settings > Setup counts every downloaded model, so step 1 must not say none are here.
    const othersOnPc = catalog.some((e) => e.downloaded);
    const checkLede = !checksDone ? 'setup.check.lede'
        : modelsOnPc === 0 ? (othersOnPc ? 'setup.check.ledeOthersOnPc' : 'setup.check.ledeDone')
            : pending.length === 0 ? 'setup.check.ledeAllOnPc' : 'setup.check.ledeSomeOnPc';
    const chatEntry = catalog.find((e) => e.id === selection?.defaultChat);

    const blocked = !!hardware && !hardware.compatible && hardware.blockers.length > 0;

    const liveStage: Stage = canFinish ? 'ready' : phase === 'download' && testing ? 'test' : phase;
    liveRef.current = liveStage;
    const stage: Stage = viewing ?? liveStage;
    useEffect(() => {
        setVisited((v) => (v.has(stage) ? v : new Set(v).add(stage)));
    }, [stage]);
    // A new step: start at its top, with focus on its title, as a page change would.
    const firstStage = useRef(true);
    useEffect(() => {
        if (firstStage.current) {
            firstStage.current = false;
            return;
        }
        const scroller = document.querySelector('.setup-scroll');
        if (scroller) scroller.scrollTop = 0;
        document.getElementById('setup-title')?.focus({ preventScroll: true });
    }, [stage]);
    // One flat line of steps; Customize, the cloud key and GPU memory are screens of the step they open from.
    const trail: Array<{ id: Stage; label: string; key: MessageKey }> = [
        { id: 'check', label: t('setup.check.title'), key: 'setup.check.title' },
        { id: 'plan', label: t('setup.plan.step'), key: 'setup.plan.step' },
        { id: 'download', label: t('setup.download.step'), key: 'setup.download.step' },
        { id: 'test', label: t('setup.test.step'), key: 'setup.test.step' },
        { id: 'ready', label: t('setup.ready.step'), key: 'setup.ready.step' },
    ];
    const railOf = (s: Stage): Stage => (s === 'models' || s === 'key' ? 'plan' : s === 'gpu' ? (gpuFrom === 'check' ? 'check' : 'plan') : s);
    // Your setup reopens on the screen the user chose there.
    const setupScreen: Stage = cloud ? 'key' : customizing ? 'models' : 'plan';
    const at = trail.findIndex((s) => s.id === railOf(liveStage));
    // A step can be opened once setup has reached it.
    const runStep = (id: Stage) => id === 'download' || id === 'test' || id === 'ready';
    // What the checks did before any run printed a line, so the Checks log is never empty.
    const checkNotes: string[] = [];
    if (hardware) {
        const memory = [
            plan?.system?.physical_memory ? `${totalMemory(plan.system.physical_memory, null)} installed` : null,
            hardware.ram_gb != null ? `${hardware.ram_gb} GB the OS can use` : null,
        ].filter(Boolean).join(', ');
        const disk = hardware.disk_free_gb != null ? `${Math.round(hardware.disk_free_gb)} GB free on disk` : null;
        checkNotes.push(`This PC: ${[hardware.gpu_name, hardware.npu_detected ? 'NPU' : null].filter(Boolean).join(', ') || 'no GPU reported'}`
            + [memory, disk].filter(Boolean).map((part) => `; ${part}`).join(''));
    } else if (hardwareError) {
        checkNotes.push(`Couldn't read this PC: ${hardwareError}`);
    }
    if (lemonadeFound) {
        checkNotes.push(`Found your Lemonade${lemonadeFound.version ? ` ${lemonadeFound.version}` : ''} at ${lemonadeFound.url}`);
        checkNotes.push(lemonadeChoice === 'external' ? 'You chose your own Lemonade'
            : lemonadeChoice === 'gaia' ? "You chose GAIA's own Lemonade"
                : 'Waiting for you to choose which Lemonade GAIA uses');
    }
    // Until the user picks a server, GAIA has none of its own to report on.
    if (plan && !(lemonadeFound && !lemonadeChoice)) {
        checkNotes.push(plan.lemonade.reachable
            ? `Model server answering at ${plan.lemonade.url ?? 'its address'}`
            : 'No model server answering yet');
    } else if (planError) {
        checkNotes.push(`Couldn't read the model list: ${planError}`);
    }
    const reachable = (id: Stage, i: number) => i <= at || (visited.has(id) && !runStep(id));
    const railSteps: RailStep[] = trail.map((s, i) => ({
        id: s.id,
        label: s.label,
        status: i < at ? 'done'
            : i > at ? 'pending'
                : liveStage === 'ready' ? 'done'
                    : runFailed && (liveStage === 'download' || liveStage === 'test' || liveStage === 'check') ? 'failed'
                        : running && (liveStage === 'download' || liveStage === 'test') ? 'active' : 'current',
        shown: s.id === railOf(stage),
        onOpen: s.id !== railOf(stage) && reachable(s.id, i) ? () => stepTo(s.id === 'plan' ? setupScreen : s.id) : undefined,
    }));
    const shownAt = trail.findIndex((s) => s.id === railOf(stage));
    // Which way the screen moved, so the new one slides in from that side.
    const shownPos = stage === 'gpu' && gpuFrom === 'check' ? 0.5 : SCREEN_ORDER.indexOf(stage);
    const lastShownPos = useRef(shownPos);
    const stepDir = useRef<'forward' | 'back'>('forward');
    if (lastShownPos.current !== shownPos) {
        stepDir.current = shownPos > lastShownPos.current ? 'forward' : 'back';
        lastShownPos.current = shownPos;
    }
    const prevStep: Stage | null = stage === 'gpu' ? gpuFrom
        : stage === 'models' || stage === 'key' ? 'plan'
            : shownAt > 0 ? (trail[shownAt - 1].id === 'plan' ? setupScreen : trail[shownAt - 1].id) : null;
    const reachedNext = stage !== 'gpu' && shownAt >= 0 && shownAt < trail.length - 1 && reachable(trail[shownAt + 1].id, shownAt + 1) ? trail[shownAt + 1].id : null;
    // Where the screen's own Continue goes when it only moves on (never one that starts a download).
    const plainNext: Stage | null = viewing != null ? null
        : stage === 'check' && checksDone ? 'plan'
            : stage === 'gpu' ? gpuFrom : null;
    const nextStep = reachedNext ?? plainNext;
    navRef.current = { prev: prevStep, next: nextStep };
    const choiceStep = stage === 'gpu' || stage === 'models' || stage === 'key';
    // Opened from further on while a run is going: the choices there wait for it.
    const runGoing = viewing != null && running;

    const expectedTests: SetupTest['job'][] = ['server'];
    if (!cloud) expectedTests.push('chat');
    if (!cloud && selection && resolveVision(catalog, selection)) expectedTests.push('vision');
    if (selection && catalog.some((e) => isEmbedding(e) && selection.ticked.has(e.id))) expectedTests.push('embedding');
    for (const e of catalog) {
        if (selection && isOptional(e) && selection.ticked.has(e.id) && !e.downloaded) expectedTests.push(e.roles[0]);
    }
    expectedTests.push('agent');

    // The key worked: say so, and which model answers, before the download list.
    const keyAccepted = cloud && keyState.done && cloudModel ? (
        <p className="setup-ok setup-key-accepted" role="status">
            <Check size={14} aria-hidden="true" /> {t('setup.key.accepted', { model: shortModelName(cloudModel) })}
        </p>
    ) : null;

    const optional = selection && !cloud ? catalog.filter((e) => isOptional(e) && !selection.ticked.has(e.id) && !e.downloaded
        && (canCreateImages(chatEntry) || !e.roles.includes('image_creation'))) : [];
    const biggerOption = catalog.find((e) => e.fit_code === 'memory' && e.chat_choice && e.support === 'verified');

    return (
        <main className="setup" aria-labelledby="setup-title">
            <SetupRail steps={railSteps} />
            <SetupFooterSlot value={footerSlot}>
            <div className="setup-pane">
            {/* The whole pane scrolls, so the scrollbar sits at the window's edge. */}
            <div className="setup-scroll">
            <div className="setup-inner">
                {shownAt >= 0 && (
                    <nav className="setup-topnav" aria-label={t('setup.nav.label')}>
                        <button type="button" className="setup-arrow" onClick={() => prevStep && stepTo(prevStep)} disabled={!prevStep} aria-label={t('setup.nav.prev')}>
                            <ChevronLeft size={16} aria-hidden="true" />
                        </button>
                        <button type="button" className="setup-arrow" onClick={() => nextStep && stepTo(nextStep)} disabled={!nextStep} aria-label={t('setup.nav.next')}>
                            <ChevronRight size={16} aria-hidden="true" />
                        </button>
                        <span><bdi>{t('setup.nav.stepOf', { n: String(shownAt + 1), total: String(trail.length) })}</bdi></span>
                        {viewing != null && viewing !== liveStage && (
                            <button type="button" className="link-btn" onClick={() => setViewing(null)}>
                                {t.compose((say) => say('setup.nav.toLive', { step: say(trail.find((s) => s.id === liveStage)?.key ?? 'setup.download.step') }))}
                            </button>
                        )}
                        <LanguagePicker />
                    </nav>
                )}
                {resumeOffer && phase !== 'download' && (
                    <div className="setup-banner" role="status">
                        <Info size={14} aria-hidden="true" />
                        <span>{t('setup.resume.banner')}</span>
                    </div>
                )}
                {interrupted && phase !== 'download' && <p className="setup-hint" role="status">{t('setup.interrupted')}</p>}

                <div key={stage} className={`setup-stage is-${stepDir.current}`}>
                    {stage === 'check' && (
                        <>
                            <h1 id="setup-title" className="setup-title" tabIndex={-1}>{t(checksDone ? 'setup.check.headingDone' : 'setup.check.heading')}</h1>
                            <p className="setup-lede">{t(checkLede)}</p>
                            <SetupChecks hardware={hardware} hardwareError={hardwareError} plan={plan} serverRun={serverRun} awaitingChoice={!!lemonadeFound && !lemonadeSettled} />
                            {serverRun?.state === 'running' && (
                                <>
                                    <RunActivity run={serverRun} idle={t('setup.check.serverWorking')} bar={false} />
                                    <SetupFooter>
                                        <button type="button" className="btn-secondary" onClick={() => void cancel()}>{t('setup.progress.cancel')}</button>
                                    </SetupFooter>
                                </>
                            )}
                            {checksDone && plan && <DetectedSummary plan={plan} hardware={hardware} onChangeMemory={showGpu && viewing == null ? () => openGpu('check') : undefined} />}
                            {checksDone && viewing == null && !(lemonadeFound && !lemonadeSettled) && (
                                <SetupFooter>
                                    <button type="button" className="btn-primary" onClick={continueFromChecks}>{t('setup.continue')}</button>
                                </SetupFooter>
                            )}
                            {serverRun && runFailed && (
                                <>
                                    <RunError run={serverRun} />
                                    {serverRun.problem
                                        ? <ServerProblem problem={serverRun.problem} onRetry={() => void start(false, { serverOnly: true })} onUseGaia={() => void start(true, { serverOnly: true })} />
                                        : (
                                            <SetupFooter>
                                                <button type="button" className="btn-primary" onClick={() => void start(!!serverRun.gaia_lemonade, { serverOnly: true })}>{t('setup.tryAgain')}</button>
                                            </SetupFooter>
                                        )}
                                </>
                            )}
                            {planError && !plan && (
                                <>
                                    <SetupAlert text={planError} />
                                    <SetupFooter>
                                        <button type="button" className="btn-secondary" onClick={() => void start(false, { keepTrying })}>{t('setup.models.useRecommended')}</button>
                                        <button type="button" className="btn-primary" onClick={() => void loadPlan()}>{t('setup.tryAgain')}</button>
                                    </SetupFooter>
                                    <p className="setup-hint">{t('setup.models.useRecommendedNote')}</p>
                                </>
                            )}
                            {startError && <SetupAlert text={startError} />}
                            {/* The server start was refused before any run existed: nothing else offers a retry. */}
                            {startError && !serverRun && plan && !plan.lemonade.reachable && (
                                <SetupFooter>
                                    <button type="button" className="btn-primary" onClick={() => void start(false, { serverOnly: true })}>{t('setup.tryAgain')}</button>
                                </SetupFooter>
                            )}
                            {/* Before GAIA starts its own server, and after, so the user can still switch to theirs. */}
                            {statusRead && plan && ((!plan.lemonade.reachable && !serverRun) || (checksDone && viewing == null && plan.lemonade.mode !== 'external')) && (
                                <ExistingLemonadeOffer
                                    onNothingToOffer={lemonadeNothing}
                                    canInstallOwn={!plan.lemonade.reachable && !serverRun}
                                    onFound={setLemonadeFound}
                                    onChosen={(mode) => { setUsersServer(mode === 'external'); setLemonadeChoice(mode); setLemonadeSettled(true); loadHardware(); void loadPlan(); }}
                                />
                            )}
                        </>
                    )}

                    {stage === 'plan' && plan && selection && (
                        <>
                            <h1 id="setup-title" className="setup-title" tabIndex={-1}>{t('setup.plan.title')}</h1>
                            <p className="setup-lede">{t('setup.plan.lede')}</p>
                            <div className="setup-where">
                                <section className={`setup-hero setup-where-card${where === 'local' ? ' is-active' : ''}`} aria-label={t('setup.plan.label')} onClick={() => !runGoing && setWhere('local')}>
                                    <p className="setup-eyebrow">{selection.chatChosen ? t('setup.plan.yours') : t('setup.plan.recommended')}</p>
                                    <p className="setup-hero-title">
                                        <input type="radio" name="setup-where" checked={where === 'local'} onChange={() => setWhere('local')} disabled={runGoing} aria-labelledby="setup-where-local" />
                                        <bdi id="setup-where-local">{t('setup.plan.run', { name: chatEntry?.name ?? '' })}</bdi>
                                    </p>
                                    <dl className="setup-facts">
                                        <div><dt>{t('setup.summary.download')}</dt><dd><bdi dir="ltr">{formatSize(totals(plan, selection).downloadGb ?? 0)}</bdi></dd></div>
                                        <div><dt>{t('setup.plan.runsOn')}</dt><dd><bdi>{chatEntry && isNpu(chatEntry) ? t('setup.checks.npu') : hardware?.gpu_name ?? t('setup.plan.cpu')}</bdi></dd></div>
                                        <div><dt>{t('setup.plan.diskAfter')}</dt><dd><bdi dir="ltr">{totals(plan, selection).afterGb != null ? formatSize(Math.max(0, totals(plan, selection).afterGb as number)) : '—'}</bdi></dd></div>
                                    </dl>
                                    <ul className="setup-reasons">
                                        {!selection.chatChosen && <li>{t('setup.plan.why.evaluated')}</li>}
                                        {chatEntry?.need_gb != null && chatEntry.capacity_gb != null && chatEntry.fits && (
                                            <li>{t('setup.plan.why.fits', { need: formatFitGb(chatEntry.need_gb), have: formatFitGb(chatEntry.capacity_gb) })}</li>
                                        )}
                                        <li>{t('setup.plan.why.private')}</li>
                                        {chatEntry && isNpu(chatEntry) && <li>{t('setup.plan.why.noImagesNpu')}</li>}
                                        {biggerOption && biggerOption.id !== chatEntry?.id && biggerOption.need_gb != null && biggerOption.capacity_gb != null && (
                                            <li>{t('setup.plan.why.bigger', { name: biggerOption.name, need: formatFitGb(biggerOption.need_gb), have: formatFitGb(biggerOption.capacity_gb) })}</li>
                                        )}
                                    </ul>
                                    {pending.length > 0 && <p className="setup-hint">{t('setup.plan.cancelAnytime')}</p>}
                                </section>
                                <section className={`setup-hero setup-where-card${where === 'cloud' ? ' is-active' : ''}${cloudBlocked ? ' is-disabled' : ''}`} aria-label={t('setup.plan.cloudLabel')} onClick={() => !runGoing && !cloudBlocked && setWhere('cloud')}>
                                    <p className="setup-hero-title">
                                        <input type="radio" name="setup-where" checked={where === 'cloud'} onChange={() => setWhere('cloud')} disabled={runGoing || cloudBlocked} aria-labelledby="setup-where-cloud" />
                                        <span id="setup-where-cloud">{t('setup.plan.cloud')}</span>
                                    </p>
                                    {cloudBlocked
                                        ? <p className="setup-hint">{t('settings.privacy.localOnlyBlocked')}</p>
                                        : (
                                            <ul className="setup-reasons">
                                                <li>{t('setup.plan.cloudWhy.stillLocal')}</li>
                                                <li>{t('setup.plan.cloudWhy.key')}</li>
                                                <li>{t('setup.plan.cloudWhy.sent')}</li>
                                            </ul>
                                        )}
                                </section>
                            </div>
                            <SetupFooter>
                                {/* Both pick local models, which a cloud setup doesn't download. */}
                                {where !== 'cloud' && <button type="button" className="btn-secondary" onClick={customize} disabled={busy || runGoing}>{t('setup.plan.customize')}</button>}
                                {where === 'cloud' ? (
                                    <button type="button" className="btn-primary" onClick={useCloud} disabled={runGoing}>{t('setup.continue')}</button>
                                ) : (
                                    <button type="button" className="btn-primary" onClick={() => (short ? setConfirm({ kind: 'low_disk' }) : void commit(false))} disabled={busy || blocked || runGoing}>
                                        {busy && <Loader2 size={13} className="spin" aria-hidden="true" />} {pending.length ? t('setup.plan.start') : t('setup.plan.startNoDownload')}
                                    </button>
                                )}
                            </SetupFooter>
                            {onClose && (
                                <div className="setup-row">
                                    <button type="button" className="link-btn" onClick={onClose}>{t('setup.backToGaia')}</button>
                                </div>
                            )}
                            {blocked && (
                                <div className="setup-error" role="alert">
                                    <p>{t('setup.blocked')}</p>
                                    <details className="dl-details">
                                        <summary>{t('setup.progress.details')}</summary>
                                        <pre dir="ltr">{hardware?.blockers.join('\n')}</pre>
                                    </details>
                                </div>
                            )}
                            {short && <p className="setup-error" role="alert">{t('setup.summary.short', { need: formatSize(short.needGb), free: formatSize(short.freeGb), drive: t('setup.summary.theDrive'), freeUp: formatSize(short.freeUpGb) })}</p>}
                            {(actionError ?? startError) && <SetupAlert text={(actionError ?? startError)!} />}
                            {where !== 'cloud' && (
                                <details className="setup-disclosure">
                                    <summary>{t('setup.plan.whatDownloads')}</summary>
                                    <DownloadContents plan={plan} models={pending} />
                                </details>
                            )}
                        </>
                    )}

                    {stage === 'gpu' && gpu && (
                        <>
                            <h1 id="setup-title" className="setup-title" tabIndex={-1}>{t('setup.gpu.title')}</h1>
                            <p className="setup-lede">{t('setup.gpu.lede')}</p>
                            <GpuMemoryPanel
                                status={gpu}
                                onStatus={setGpu}
                                mode="setup"
                                headingId="setup-title"
                                names={names}
                                cloud={choice !== 'local'}
                                onBack={backOne}
                                onSkip={() => stepTo(gpuFrom)}
                                onContinue={() => stepTo(gpuFrom)}
                            />
                        </>
                    )}

                    {stage === 'models' && (
                        <>
                            <h1 id="setup-title" className="setup-title" tabIndex={-1}>{t('setup.models.title')}</h1>
                            {plan && selection && <BackendChoice plan={plan} selection={selection} onSelection={setSelection} disabled={busy || runGoing} onRecheck={() => void loadPlan()} />}
                            {showGpu && gpu?.total_gb != null && gpu.gpu_gb != null && (
                                <p className="setup-hint setup-gpu-option">
                                    {t('setup.models.gpu', { gpu: formatSize(gpu.gpu_gb), total: formatSize(gpu.total_gb) })}{' '}
                                    <button type="button" className="link-btn" onClick={() => openGpu('models')} disabled={busy || runGoing}>{t('setup.found.changeMemory')}</button>
                                </p>
                            )}
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
                                busy={busy || runGoing}
                                error={actionError ?? startError}
                            />
                        </>
                    )}

                    {stage === 'key' && (
                        <form id="setup-key-form" onSubmit={(e) => { e.preventDefault(); void connect(); }} className="setup-form">
                            <h1 id="setup-title" className="setup-title" tabIndex={-1}>{t('setup.key.title')}</h1>
                            <RadioGroup className="setup-choices" aria-label={t('setup.key.provider')}>
                                {CHOICES.filter((c) => c.id !== 'local' && (c.id !== 'amd' || gatewayShown)).map((c) => (
                                    <button
                                        key={c.id}
                                        type="button"
                                        role="radio"
                                        aria-checked={choice === c.id}
                                        className={`setup-choice${choice === c.id ? ' is-active' : ''}${unavailable(c.id) ? ' is-disabled' : ''}`}
                                        onClick={() => switchCloud(c.id)}
                                        disabled={keyState.busy || unavailable(c.id)}
                                        title={cloudProviderBlocked(c.id) ? t('settings.privacy.localOnlyBlocked') : undefined}
                                    >
                                        <Cloud size={16} aria-hidden="true" />
                                        <span>
                                            <span className="setup-choice-title"><bdi>{choiceTitle(c, t)}</bdi></span>
                                            <span className="setup-choice-note">{unavailable(c.id) ? unavailableNote(c.id) : t(c.note)}</span>
                                        </span>
                                    </button>
                                ))}
                            </RadioGroup>
                            {gateway?.reachable === false && gatewayShown && (
                                <button type="button" className="link-btn setup-start" onClick={recheckGateway}>{t('setup.key.checkAgain')}</button>
                            )}

                            {cloudProviderBlocked(choice) && (
                                <p className="setup-a11y-alert" role="alert">{t('settings.privacy.localOnlyBlocked')}</p>
                            )}
                            {choice === 'fireworks' && !usersServer && !cloudProviderBlocked(choice) && (
                                <section className="setup-card">
                                    <h3 className="mc-job-title">{t('setup.key.getOne')}</h3>
                                    <ol className="setup-key-steps">
                                        <li><a href={FIREWORKS_KEYS_URL} target="_blank" rel="noreferrer">{t('setup.key.step1')}</a></li>
                                        <li>{t('setup.key.step2')}</li>
                                        <li>{t('setup.key.step3')}</li>
                                    </ol>
                                </section>
                            )}
                            {choice === 'amd' && !usersServer && !cloudProviderBlocked(choice) && (
                                <section className="setup-card">
                                    <p className="setup-hint"><a href={GATEWAY_GUIDE_URL} target="_blank" rel="noreferrer">{t('setup.key.gatewayHelp')}</a></p>
                                </section>
                            )}
                            {!cloudProviderBlocked(choice) && (
                                <CloudPrices provider={choice} limit={3} picked={cloudPick} onPick={setCloudPick} onDefault={setCloudDefault} pickDisabled={keyState.busy || runGoing} />
                            )}
                            {!usersServer && (
                                <>
                                    <label className="setup-key-label" htmlFor="setup-key-input">
                                        {t('setup.key.labelFor', { provider: choiceTitle(CHOICES.find((c) => c.id === choice) ?? CHOICES[1], t) })}
                                    </label>
                                    <div className="setup-row setup-key-row">
                                        <input
                                            id="setup-key-input"
                                            className="settings-input"
                                            type={showKey ? 'text' : 'password'}
                                            autoComplete="off"
                                            spellCheck={false}
                                            value={key}
                                            onChange={(e) => setKey(e.target.value)}
                                            placeholder={savedKey ? t('setup.key.placeholder') : t('setup.key.placeholderNew')}
                                        />
                                        <button type="button" className="btn-secondary setup-key-show" aria-label={t(showKey ? 'setup.key.hide' : 'setup.key.show')} title={t(showKey ? 'setup.key.hide' : 'setup.key.show')} onClick={() => setShowKey((s) => !s)}>
                                            {showKey ? <EyeOff size={14} aria-hidden="true" /> : <Eye size={14} aria-hidden="true" />}
                                        </button>
                                    </div>
                                </>
                            )}
                            <p className="setup-hint">{usersServer ? t('setup.key.usersServer') : t('setup.key.where')}</p>
                            {envKey && !usersServer && !key && (
                                <p className="setup-hint" role="status">{t('setup.key.foundEnv', { name: envKey.name, hint: envKey.key_hint ?? '' })}</p>
                            )}
                            {keyState.busy && !keyState.waiting && <p className="setup-hint" role="status">{t('setup.key.testing')}</p>}
                            {keyState.busy && keyState.waiting && <p className="setup-hint" role="status">{keyState.waiting}</p>}
                            {keyState.error && <p className="setup-error" role="alert">{keyState.error}</p>}
                            <SetupFooter>
                                <button type="button" className="btn-secondary" onClick={backToPlan} disabled={keyState.busy}>{t('setup.back')}</button>
                                <button type="submit" form="setup-key-form" className="btn-primary" disabled={keyState.busy || unavailable(choice) || runGoing}>
                                    {keyState.busy && <Loader2 size={13} className="spin" />} {t('setup.key.connect')}
                                </button>
                            </SetupFooter>
                        </form>
                    )}

                    {/* Started, before the run's first report: the step is already here, not a blank page. */}
                    {stage === 'download' && !fullRun && (
                        <>
                            <h1 id="setup-title" className="setup-title" tabIndex={-1}>{t('setup.download.title')}</h1>
                            <p className="setup-lede">{t('setup.download.lede')}</p>
                            {keyAccepted}
                            <p className="setup-hint" role="status"><Loader2 size={13} className="spin" aria-hidden="true" /> {t('setup.download.starting')}</p>
                        </>
                    )}

                    {stage === 'download' && fullRun && (
                        <>
                            <h1 id="setup-title" className="setup-title" tabIndex={-1}>{t('setup.download.title')}</h1>
                            <p className="setup-lede">{t('setup.download.lede')}</p>
                            {keyAccepted}
                            {plan && <section className="setup-card" aria-label={t('setup.contents.title')}><DownloadContents plan={plan} run={fullRun} onRetry={retryRun} retryDisabled={busy} /></section>}
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
                                <>
                                    <RunActivity run={fullRun} plan={plan} idle={t('setup.download.working')} />
                                    <SetupFooter>
                                        <button type="button" className="btn-secondary" onClick={() => void cancel()} disabled={busy}>{t('setup.progress.cancel')}</button>
                                    </SetupFooter>
                                </>
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
                                <>
                                    <p className="setup-error" role="alert">{t('setup.lostContact', { error: lostContact })}</p>
                                    <SetupFooter>
                                        <button type="button" className="btn-primary" onClick={() => void poll()}>{t('setup.reconnect')}</button>
                                    </SetupFooter>
                                </>
                            )}
                            {runFailed && downloadDone && !componentFailed && fullRun.state === 'failed' && !fullRun.model_failed && (
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
                            {runFailed && downloadDone && !componentFailed && (
                                <>
                                    <p className="setup-hint">{t('setup.download.finishNote')}</p>
                                    <SetupFooter>
                                        <button type="button" className="btn-primary" onClick={retryRun} disabled={busy}>{t('setup.finish')}</button>
                                    </SetupFooter>
                                </>
                            )}
                            {runFailed && !downloadDone && !componentFailed && !(fullRun.models ?? []).some((m) => m.state === 'failed' || m.state === 'cancelled') && (
                                <>
                                    <RunError run={fullRun} />
                                    {fullRun.problem
                                        ? <ServerProblem problem={fullRun.problem} onRetry={retryRun} onUseGaia={() => void start(true, lastRun.current.options)} />
                                        : (
                                            <SetupFooter>
                                                <button type="button" className="btn-primary" onClick={() => void start(!!fullRun.gaia_lemonade, lastRun.current.options)}>{t('setup.tryAgain')}</button>
                                            </SetupFooter>
                                        )}
                                </>
                            )}
                            {(actionError || startError) && <SetupAlert text={(actionError ?? startError)!} />}
                        </>
                    )}

                    {stage === 'test' && fullRun && (
                        <>
                            <h1 id="setup-title" className="setup-title" tabIndex={-1}>{t('setup.test.title')}</h1>
                            <p className="setup-lede">{t('setup.test.lede')}</p>
                            <TestStep
                                run={fullRun}
                                expected={expectedTests}
                                names={names}
                                catalog={catalog}
                                onRetry={retryRun}
                                onChooseAnother={cloud ? undefined : customize}
                                onCancel={() => void cancel()}
                                // GAIA never deletes a model on the user's own server.
                                onDownloadAgain={plan?.lemonade.mode === 'external' ? undefined : (id) => void retryModel(id, 'verify')}
                                busy={busy}
                            />
                            {/* A failed test already shows this under its own row. */}
                            {runFailed && fullRun.error && !(fullRun.tests ?? []).some((r) => r.error === fullRun.error) && (
                                <details className="dl-details">
                                    <summary>{t('setup.progress.details')}</summary>
                                    <pre dir="ltr">{fullRun.error}</pre>
                                </details>
                            )}
                            {(actionError || startError) && <SetupAlert text={(actionError ?? startError)!} />}
                        </>
                    )}

                    {stage === 'ready' && (
                        <>
                            <h1 id="setup-title" className="setup-title" tabIndex={-1}>{t('setup.ready.heading')}</h1>
                            <div className="setup-done" role="status">
                                <div className="setup-done-head">
                                    {/* The heading already says GAIA is ready; this says what answers. */}
                                    {answering && (
                                        <p>
                                            {t(cloud ? 'setup.ready.answersCloud' : 'setup.ready.answersLocal', { model: cloud ? displayModelName(answering) : (names[answering] ?? finalPlan?.catalog?.find((e) => e.id === answering)?.name ?? displayModelName(answering)) })}
                                        </p>
                                    )}
                                </div>
                                <SetupFooter>
                                    <button type="button" className="btn-primary" onClick={() => void finish()} autoFocus>
                                        {t('setup.ready.start')}
                                    </button>
                                </SetupFooter>
                                {!cloud && finalPlan && <ReadySummary plan={finalPlan} chatModel={answering} gpu={gpu} />}
                                {startError && <SetupAlert text={startError} />}
                                {(fullRun?.tests ?? []).filter((r) => r.state === 'failed').map((r) => (
                                    <p key={`${r.job}:${r.subject ?? ''}`} className="setup-failed-extra" role="alert">
                                        {t('setup.ready.extraFailed', { job: t(`setup.test.job.${r.job}`) })}
                                    </p>
                                ))}
                                {notFetched.length > 0 && !confirm && (
                                    <p className="setup-failed-extra" role="alert">
                                        {t('setup.ready.extraNotFetched', { names: notFetched.map((id) => names[id] ?? id).join(', ') })}
                                    </p>
                                )}
                                {readyTasks.length > 0 && (
                                    <>
                                        <p className="setup-hint">{t('setup.ready.tasks')}</p>
                                        <StartCards cards={readyTasks} onPick={(card) => void finish(card)} />
                                    </>
                                )}
                            </div>
                        </>
                    )}

                </div>
                {onClose && !running && stage !== 'plan' && stage !== 'ready' && (
                    <div className="setup-row">
                        <button type="button" className="link-btn setup-close" onClick={onClose}>{t('setup.backToGaia')}</button>
                    </div>
                )}
                {runGoing && (choiceStep || stage === 'plan') && (
                    <p className="setup-hint" role="status">{t('setup.nav.runGoing')}</p>
                )}
                <StageLogs run={run} before={{ check: checkNotes }} failed={stepFailed} />
            </div>
            </div>
            <div className="setup-footer" ref={setFooterSlot} />
            </div>
            </SetupFooterSlot>

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
