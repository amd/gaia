// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertCircle, Check, ChevronRight, Cloud, Cpu, Loader2 } from 'lucide-react';
import * as api from '../services/api';
import { useModelStore } from '../stores/modelStore';
import { lemonadeKeyRejected } from '../utils/lemonade';
import type { LemonadeFound, LemonadeTarget, PreflightReport, ProviderId, SetupCheck, SetupRunStatus, SetupStep } from '../types';
import './SetupScreen.css';

const CHOICES: Array<{ id: ProviderId; title: string; note: string }> = [
    { id: 'local', title: 'On this PC', note: 'Private. Models run on your AMD hardware; nothing leaves this machine.' },
    { id: 'fireworks', title: 'Fireworks AI', note: 'Faster, larger cloud models. Needs an API key; chat history is sent to Fireworks AI.' },
    { id: 'amd', title: 'AMD LLM Gateway', note: 'For AMD employees. Needs a gateway key; chat history is sent to the gateway.' },
];

const POLL_MS = 1000;
const CHOICE_KEY = 'gaia-setup-choice';
// The check ran for a local install, so a missing chat model is not a cloud user's problem.
const CHAT_MODEL_MISSING = /^Model '(?![^']*embed)[^']*' is not downloaded$/i;

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

function StepIcon({ status, n }: { status?: SetupStep['status']; n: number }) {
    if (status === 'done') return <Check size={14} aria-hidden="true" />;
    if (status === 'active') return <Loader2 size={14} className="spin" aria-hidden="true" />;
    if (status === 'failed' || status === 'cancelled') return <AlertCircle size={14} aria-hidden="true" />;
    return <>{n}</>;
}

const OFFERED: ReadonlyArray<LemonadeFound['status']> = ['ok', 'needs_key', 'too_old'];

/**
 * First run on a PC that already runs Lemonade: offer it, so GAIA does not
 * install a second server and a second copy of every model.
 */
function ExistingLemonadeOffer({ onChosen }: { onChosen: (mode: 'external' | 'gaia') => void }) {
    const [offer, setOffer] = useState<{ target: LemonadeTarget; found: LemonadeFound } | null>(null);
    const [checkError, setCheckError] = useState<string | null>(null);
    const [key, setKey] = useState('');
    const [needsKey, setNeedsKey] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [chosen, setChosen] = useState<'external' | 'gaia' | null>(null);

    useEffect(() => {
        let live = true;
        (async () => {
            const target = await api.getLemonadeTarget();
            if (!target.offer) return;
            const { found } = await api.detectLemonade();
            if (live && found && OFFERED.includes(found.status)) {
                setOffer({ target, found });
                setNeedsKey(found.status === 'needs_key');
            }
        })().catch((err) => {
            if (live) setCheckError(err instanceof Error ? err.message : String(err));
        });
        return () => { live = false; };
    }, []);

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

    if (checkError) {
        return <p className="setup-hint">Could not check for a Lemonade Server you already run: {checkError}</p>;
    }
    if (!offer) return null;
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

interface SetupScreenProps {
    /** What `gaia init --check` said; the screen is shown only when it is not ready. */
    initial: SetupCheck;
    onReady: () => void;
}

/**
 * First run, the same steps as the TUI: install the local model server, then
 * download the models, then check they load. A cloud choice skips the local
 * chat model (the embedding model is still needed for documents and memory).
 */
export function SetupScreen({ initial, onReady }: SetupScreenProps) {
    const refreshModel = useModelStore((s) => s.refresh);
    const selectModel = useModelStore((s) => s.select);
    const [choice, setChoice] = useState<ProviderId>(() => readChoice() ?? 'local');
    const [hardware, setHardware] = useState<PreflightReport | null>(null);
    const [hardwareError, setHardwareError] = useState<string | null>(null);
    const [run, setRun] = useState<SetupRunStatus | null>(null);
    const [startError, setStartError] = useState<string | null>(null);
    const [key, setKey] = useState('');
    const [keyState, setKeyState] = useState<{ busy: boolean; error: string | null; done: boolean }>({ busy: false, error: null, done: false });
    const [showLog, setShowLog] = useState(false);
    // GAIA uses a Lemonade the user runs, so the Connect step changes nothing there.
    const [usersServer, setUsersServer] = useState(false);
    const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

    const cloud = choice !== 'local';
    const steps: SetupStep[] = run?.steps ?? (cloud ? initial.cloud_steps ?? initial.steps : initial.steps);
    // The check ran before setup; once a run starts its steps say what is left.
    const reason = run ? undefined : initial.reasons?.find((r) => !(cloud && CHAT_MODEL_MISSING.test(r)));
    const running = run?.state === 'running' || run?.state === 'verifying';
    const installed = run?.state === 'ready';

    useEffect(() => {
        api.getLemonadeTarget()
            .then((t) => setUsersServer(t.source === 'config'))
            .catch((err) => console.warn('[SetupScreen] could not read which Lemonade GAIA uses', err));
        api.getOnboardingPreflight()
            .then(setHardware)
            .catch((err) => setHardwareError(err instanceof Error ? err.message : String(err)));
        // Pick up a setup another tab (or a reload) already started.
        api.getSetupStatus()
            .then((s) => {
                if (s.state === 'idle') return;
                setRun(s);
                // A cloud run with no saved choice can't say which provider; the
                // Connect step lets the user switch if this guess is wrong.
                if (s.skip_chat_model && !readChoice()) setChoice('fireworks');
            })
            .catch((err) => setStartError(`Could not read setup progress: ${err instanceof Error ? err.message : err}`));
    }, []);

    const stopPolling = useCallback(() => {
        if (pollRef.current) clearInterval(pollRef.current);
        pollRef.current = null;
    }, []);

    useEffect(() => {
        if (!running) return stopPolling;
        pollRef.current = setInterval(() => {
            api.getSetupStatus().then(setRun).catch((err) => {
                setRun((r) => r && { ...r, state: 'failed', error: `Lost contact with GAIA: ${err instanceof Error ? err.message : err}` });
            });
        }, POLL_MS);
        return stopPolling;
    }, [running, stopPolling]);

    const start = useCallback(async () => {
        setStartError(null);
        try {
            saveChoice(choice);
            setRun(await api.startSetup(cloud));
        } catch (err) {
            setStartError(err instanceof Error ? err.message : String(err));
        }
    }, [cloud, choice]);

    const connect = useCallback(async () => {
        setKeyState({ busy: true, error: null, done: false });
        try {
            await api.connectProvider(choice, { api_key: (!usersServer && key) || undefined });
            const { models } = await api.listProviderModels(choice);
            const pick = models.find((m) => m.rank === 1) ?? models[0];
            if (!pick) throw new Error('Connected, but the provider listed no chat models.');
            await selectModel(pick.id);
            setKey('');
            setKeyState({ busy: false, error: null, done: true });
        } catch (err) {
            setKeyState({ busy: false, error: err instanceof Error ? err.message : String(err), done: false });
        }
    }, [choice, key, selectModel, usersServer]);

    const switchCloud = useCallback((id: ProviderId) => {
        saveChoice(id);
        setChoice(id);
        setKeyState({ busy: false, error: null, done: false });
    }, []);

    const finish = useCallback(async () => {
        await refreshModel();
        onReady();
    }, [onReady, refreshModel]);

    const canFinish = installed && (!cloud || keyState.done);
    const activeStep = steps.find((s) => s.status === 'active' || s.status === 'failed');

    return (
        <main className="setup" aria-labelledby="setup-title">
            <div className="setup-inner">
                <h1 id="setup-title" className="setup-title">Set up GAIA</h1>
                <p className="setup-lede">
                    {reason ? `${reason}.` : 'A few things are needed before the first chat.'}
                </p>

                <ol className="setup-steps">
                    <li className={`setup-step${run ? ' is-done' : ' is-current'}`}>
                        <div className="setup-step-head">
                            <span className="setup-step-num">{run ? <Check size={14} aria-hidden="true" /> : 1}</span>
                            <span className="setup-step-label">Choose where GAIA runs</span>
                        </div>
                        {!run && (
                            <div className="setup-step-body">
                                <ExistingLemonadeOffer onChosen={(mode) => setUsersServer(mode === 'external')} />
                                <div className="setup-choices" role="radiogroup" aria-label="Where GAIA runs">
                                    {CHOICES.map((c) => (
                                        <button
                                            key={c.id}
                                            type="button"
                                            role="radio"
                                            aria-checked={choice === c.id}
                                            className={`setup-choice${choice === c.id ? ' is-active' : ''}`}
                                            onClick={() => setChoice(c.id)}
                                        >
                                            {c.id === 'local' ? <Cpu size={16} aria-hidden="true" /> : <Cloud size={16} aria-hidden="true" />}
                                            <span>
                                                <span className="setup-choice-title">{c.title}</span>
                                                <span className="setup-choice-note">{c.note}</span>
                                            </span>
                                        </button>
                                    ))}
                                </div>
                                {hardware && (
                                    <p className="setup-hint">
                                        This PC: {hardware.ram_gb != null ? `${hardware.ram_gb} GB memory` : 'memory unknown'}
                                        {hardware.gpu_name ? ` · ${hardware.gpu_name}` : ''}
                                        {hardware.npu_detected ? ' · Ryzen AI NPU' : ''}
                                        {hardware.disk_free_gb != null ? ` · ${hardware.disk_free_gb} GB free` : ''}
                                    </p>
                                )}
                                {hardwareError && (
                                    <p className="setup-hint">Could not read this PC&rsquo;s hardware: {hardwareError}</p>
                                )}
                                {hardware && !hardware.compatible && hardware.blockers.length > 0 && (
                                    <p className="setup-error" role="alert">{hardware.blockers.join(' ')}</p>
                                )}
                                {hardware && hardware.warnings.length > 0 && (
                                    <p className="setup-hint">{hardware.warnings.join(' ')}</p>
                                )}
                                <button type="button" className="btn-primary setup-start" onClick={() => void start()}>
                                    Start setup
                                </button>
                                {startError && <p className="setup-error" role="alert">{startError}</p>}
                            </div>
                        )}
                    </li>

                    {steps.map((step, i) => {
                        const status = run ? step.status : 'pending';
                        const isActive = status === 'active';
                        const failed = status === 'failed' || status === 'cancelled';
                        return (
                            <li key={step.key} className={`setup-step is-${status ?? 'pending'}`} aria-current={isActive ? 'step' : undefined}>
                                <div className="setup-step-head">
                                    <span className="setup-step-num"><StepIcon status={status} n={i + 2} /></span>
                                    <span className="setup-step-label">
                                        {step.label}
                                        {step.detail && <span className="setup-step-detail"> — {step.detail}</span>}
                                    </span>
                                </div>
                                {isActive && run && (
                                    <div className="setup-step-body">
                                        <p className="setup-hint" role="status">{run.text}</p>
                                        {run.percent != null && (
                                            <progress className="setup-progress" max={100} value={run.percent} aria-label={run.text} />
                                        )}
                                    </div>
                                )}
                                {failed && run && step === activeStep && (
                                    <div className="setup-step-body">
                                        <p className="setup-error" role="alert">{run.error ?? 'Setup stopped.'}</p>
                                        <div className="setup-row">
                                            <button type="button" className="btn-primary" onClick={() => void start()}>Try again</button>
                                            <span className="setup-hint">or run <code>{run.command}</code> in a terminal.</span>
                                        </div>
                                    </div>
                                )}
                            </li>
                        );
                    })}

                    {cloud && (
                        <li className={`setup-step${keyState.done ? ' is-done' : installed ? ' is-current' : ' is-pending'}`}>
                            <div className="setup-step-head">
                                <span className="setup-step-num">{keyState.done ? <Check size={14} aria-hidden="true" /> : steps.length + 2}</span>
                                <span className="setup-step-label">Connect {CHOICES.find((c) => c.id === choice)?.title}</span>
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
                                                disabled={keyState.busy}
                                            >
                                                {c.title}
                                            </button>
                                        ))}
                                    </div>
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
                        <p>GAIA is ready.</p>
                        <button type="button" className="btn-primary" onClick={() => void finish()} autoFocus>
                            Start chatting
                        </button>
                    </div>
                )}
            </div>
        </main>
    );
}
