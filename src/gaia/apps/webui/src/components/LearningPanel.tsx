// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Nightly learning: what the agent learned, the adapters it trained, and when it trains. */

import { useCallback, useEffect, useState } from 'react';
import { X, Play, Square, RotateCcw, Trash2, ChevronDown, ChevronRight, CircleCheck, CircleX } from 'lucide-react';
import { useChatStore } from '../stores/chatStore';
import { useEscapeToClose } from '../hooks/useEscapeToClose';
import { Switch } from './session-panel/Switch';
import * as learn from '../services/learningApi';
import type { Adapter, LearnedItem, LearningKind, LearningStatus, Run, Score } from '../services/learningApi';
import './LearningPanel.css';

const KIND_LABEL: Record<LearningKind, string> = {
    preference: 'Preference',
    concept: 'Concept',
    workflow: 'Faster workflow',
    correction: 'Correction',
};

const RUN_LABEL: Record<Run['status'], string> = {
    running: 'Running',
    promoted: 'New adapter active',
    rejected: 'Kept current model',
    nothing_to_learn: 'Nothing new',
    stopped: 'Stopped',
    failed: 'Failed',
    skipped: 'Skipped',
};

const ADAPTER_LABEL: Record<Adapter['status'], string> = {
    active: 'Active',
    archived: 'Archived',
    rejected: 'Rejected',
    candidate: 'Not judged',
};

const PHASE_LABEL: Record<string, string> = {
    starting: 'Starting',
    reflecting: 'Reading history',
    baseline: 'Measuring the current model',
    training: 'Training',
    converting: 'Converting',
    evaluating: 'Testing the new adapter',
    done: 'Done',
};

/** Adapter and run ids are local-time stamps: 20261023-021939. */
export function stampToDate(id: string): string {
    const m = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/.exec(id);
    if (!m) return id;
    const d = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

function fraction(passed?: number, total?: number): string {
    return total ? `${passed}/${total}` : '—';
}

function ScoreLine({ adapter }: { adapter: Adapter }) {
    const cand = adapter.scores?.candidate;
    const base = adapter.scores?.base;
    if (!cand || !base) return null;
    return (
        <span className="lp-score">
            Learned {fraction(cand.learned_passed, cand.learned_total)}
            <span className="lp-muted"> (base {base.learned_passed})</span>
            {' · '}General {fraction(cand.regression_passed, cand.regression_total)}
        </span>
    );
}

function ProbeTable({ base, cand }: { base: Score; cand: Score }) {
    const baseBy = new Map(base.results.map((r) => [`${r.kind}|${r.question}`, r]));
    const rows = cand.results.filter((r) => r.kind === 'learned');
    if (rows.length === 0) return null;
    return (
        <table className="lp-probes">
            <thead>
                <tr><th>Question</th><th>Base</th><th>Adapter</th></tr>
            </thead>
            <tbody>
                {rows.map((r) => {
                    const b = baseBy.get(`${r.kind}|${r.question}`);
                    return (
                        <tr key={r.question}>
                            <td>
                                <div>{r.question}</div>
                                <div className="lp-reply">{r.reply}</div>
                            </td>
                            <td>{b && (b.passed ? <CircleCheck size={14} className="lp-ok" aria-label="passed" /> : <CircleX size={14} className="lp-bad" aria-label="failed" />)}</td>
                            <td>{r.passed ? <CircleCheck size={14} className="lp-ok" aria-label="passed" /> : <CircleX size={14} className="lp-bad" aria-label="failed" />}</td>
                        </tr>
                    );
                })}
            </tbody>
        </table>
    );
}

function AdapterRow({ adapter, onChanged, onError }: { adapter: Adapter; onChanged: () => void; onError: (m: string) => void }) {
    const [open, setOpen] = useState(false);
    const [busy, setBusy] = useState(false);
    const [confirmDelete, setConfirmDelete] = useState(false);
    const act = async (fn: () => Promise<unknown>) => {
        setBusy(true);
        try { await fn(); onChanged(); } catch (e) { onError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
    };
    const usable = adapter.status === 'archived' || adapter.status === 'candidate';
    return (
        <li className={`lp-adapter is-${adapter.status}`}>
            <div className="lp-adapter-head">
                <button className="lp-expand" onClick={() => setOpen(!open)} aria-expanded={open} aria-label={`Details for ${adapter.id}`}>
                    {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                </button>
                <span className="lp-adapter-date">{stampToDate(adapter.id)}</span>
                <span className={`lp-pill lp-pill-${adapter.status}`}>{ADAPTER_LABEL[adapter.status]}</span>
                <ScoreLine adapter={adapter} />
                <span className="lp-actions">
                    {usable && (
                        <button className="btn-secondary lp-btn" disabled={busy} onClick={() => act(() => learn.activateAdapter(adapter.id))}>
                            <RotateCcw size={12} /> {adapter.status === 'archived' ? 'Roll back to this' : 'Use this'}
                        </button>
                    )}
                    {adapter.status !== 'active' && (confirmDelete ? (
                        <button className="btn-danger lp-btn" disabled={busy} onClick={() => act(() => learn.deleteAdapter(adapter.id))}>Confirm delete</button>
                    ) : (
                        <button className="btn-icon" aria-label={`Delete adapter ${adapter.id}`} onClick={() => setConfirmDelete(true)}><Trash2 size={14} /></button>
                    ))}
                </span>
            </div>
            {open && (
                <div className="lp-adapter-body">
                    {adapter.decision && <p className="lp-decision">{adapter.decision}.</p>}
                    {adapter.metrics && (
                        <p className="lp-muted">
                            {adapter.metrics.steps} steps in {Math.round(adapter.metrics.seconds ?? 0)}s
                            {adapter.metrics.peak_gpu_gb ? `, ${adapter.metrics.peak_gpu_gb} GB GPU` : ''}
                            {adapter.metrics.eval_loss != null && adapter.metrics.base_eval_loss != null
                                ? ` · held-out loss ${adapter.metrics.base_eval_loss.toFixed(2)} → ${adapter.metrics.eval_loss.toFixed(2)}` : ''}
                            {adapter.size_bytes ? ` · ${(adapter.size_bytes / 1e6).toFixed(0)} MB` : ''}
                        </p>
                    )}
                    {adapter.items && adapter.items.length > 0 && (
                        <ul className="lp-tags">
                            {adapter.items.map((i) => <li key={i.id} className={`lp-kind lp-kind-${i.kind}`} title={i.title}>{KIND_LABEL[i.kind]}: {i.title}</li>)}
                        </ul>
                    )}
                    {adapter.scores?.base && adapter.scores?.candidate && (
                        <ProbeTable base={adapter.scores.base} cand={adapter.scores.candidate} />
                    )}
                </div>
            )}
        </li>
    );
}

function RunCard({ run, onStop }: { run: Run; onStop: () => void }) {
    const p = run.progress;
    const pct = p?.total ? Math.round(((p.step ?? 0) / p.total) * 100) : null;
    return (
        <div className={`lp-run is-${run.status}`}>
            <div className="lp-run-head">
                <span className={`lp-pill lp-pill-run-${run.status}`}>{RUN_LABEL[run.status]}</span>
                <span className="lp-muted">{stampToDate(run.id)} · {run.trigger === 'scheduled' ? 'nightly' : 'started by you'}</span>
                {run.status === 'running' && (
                    <button className="btn-secondary lp-btn" onClick={onStop}><Square size={12} /> Stop</button>
                )}
            </div>
            {run.status === 'running' && (
                <div className="lp-progress" role="status">
                    <span>{PHASE_LABEL[run.phase] ?? run.phase}</span>
                    {run.phase === 'training' && pct !== null && (
                        <>
                            <div className="lp-bar" aria-hidden="true"><div style={{ width: `${pct}%` }} /></div>
                            <span className="lp-muted">step {p?.step}/{p?.total}{p?.loss != null ? `, loss ${p.loss.toFixed(3)}` : ''}</span>
                        </>
                    )}
                </div>
            )}
            {run.summary && <p className="lp-summary">{run.summary}</p>}
            {run.error && <p className="lp-error">{run.error}</p>}
            {run.opportunities && run.opportunities.length > 0 && (
                <ul className="lp-tags">
                    {run.opportunities.map((o) => (
                        <li key={o.id} className={`lp-kind lp-kind-${o.kind}${o.error ? ' has-error' : ''}`} title={o.error ?? o.title}>
                            {KIND_LABEL[o.kind]}: {o.title}
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}

function ItemRow({ item, onForget }: { item: LearnedItem; onForget: (id: string) => void }) {
    const [open, setOpen] = useState(false);
    return (
        <li className="lp-item">
            <button className="lp-expand" onClick={() => setOpen(!open)} aria-expanded={open} aria-label={`Evidence for ${item.title}`}>
                {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            </button>
            <span className={`lp-kind lp-kind-${item.kind}`}>{KIND_LABEL[item.kind]}</span>
            <span className="lp-item-title">{item.title}</span>
            <span className="lp-muted">{item.examples} {item.examples === 1 ? 'example' : 'examples'}</span>
            <button className="btn-secondary lp-btn" onClick={() => onForget(item.id)}>Forget</button>
            {open && (
                <ul className="lp-evidence">
                    {item.evidence.map((e, n) => <li key={n}>{e}</li>)}
                </ul>
            )}
        </li>
    );
}

export function LearningPanel() {
    const setShowLearning = useChatStore((s) => s.setShowLearning);
    const close = useCallback(() => setShowLearning(false), [setShowLearning]);
    useEscapeToClose(true, close);

    const [status, setStatus] = useState<LearningStatus | null>(null);
    const [adapters, setAdapters] = useState<Adapter[]>([]);
    const [items, setItems] = useState<LearnedItem[]>([]);
    const [error, setError] = useState<string | null>(null);

    const refresh = useCallback(async () => {
        try {
            const [s, a, i] = await Promise.all([learn.getStatus(), learn.listAdapters(), learn.listItems()]);
            setStatus(s);
            setAdapters(a);
            setItems(i);
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        }
    }, []);

    const running = status?.latest_run?.status === 'running';
    useEffect(() => {
        void refresh();
        const id = setInterval(() => void refresh(), running ? 3000 : 30000);
        return () => clearInterval(id);
    }, [refresh, running]);

    const run = async (fn: () => Promise<unknown>) => {
        setError(null);
        try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
        await refresh();
    };

    const cfg = status?.config;
    const toolchainReady = status?.toolchain?.ready ?? false;

    return (
        <div className="modal-overlay" onClick={close} role="dialog" aria-modal="true" aria-label="Nightly learning">
            <div className="modal-panel lp-modal" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <h3>Nightly learning</h3>
                    <button className="btn-icon" onClick={close} aria-label="Close nightly learning"><X size={18} /></button>
                </div>
                <div className="modal-body">
                    <p className="lp-intro">
                        While you're away, GAIA reads what it learned from your chats, trains a small adapter on this PC and
                        keeps it only if it answers your questions better without getting worse at anything else. Nothing leaves this machine.
                    </p>
                    {error && <p className="lp-error" role="alert">{error}</p>}
                    {!status ? (
                        <p className="lp-muted">Loading…</p>
                    ) : (
                        <>
                            <section className="lp-section lp-settings">
                                <div className="lp-row">
                                    <Switch
                                        checked={!!cfg?.enabled}
                                        onChange={(on) => void run(() => learn.updateConfig({ enabled: on }))}
                                        label="Nightly learning"
                                        text={cfg?.enabled ? 'On' : 'Off'}
                                    />
                                    <label className="lp-window">
                                        Between
                                        <input type="time" value={cfg?.window_start} aria-label="Window start"
                                            onChange={(e) => void run(() => learn.updateConfig({ window_start: e.target.value }))} />
                                        and
                                        <input type="time" value={cfg?.window_end} aria-label="Window end"
                                            onChange={(e) => void run(() => learn.updateConfig({ window_end: e.target.value }))} />
                                    </label>
                                    <label className="lp-check">
                                        <input type="checkbox" checked={!!cfg?.require_ac_power}
                                            onChange={(e) => void run(() => learn.updateConfig({ require_ac_power: e.target.checked }))} />
                                        Only on mains power
                                    </label>
                                </div>
                                <div className="lp-row lp-muted">
                                    <span>Model: {status.base_model}</span>
                                    <span>Adapter: {status.active_adapter ? stampToDate(status.active_adapter.id) : 'none'}</span>
                                    <span>Toolchain: {status.toolchain ? (toolchainReady ? `ready (${status.toolchain.gpu})` : 'needs setup') : 'not checked'}</span>
                                </div>
                                {!status.base_model_trainable && (
                                    <p className="lp-error">
                                        {status.base_model} can't be personalised yet. Supported: {status.trainable_models.join(', ')}.
                                    </p>
                                )}
                                {status.toolchain && !toolchainReady && status.toolchain.problems.map((p) => <p key={p} className="lp-error">{p}</p>)}
                                {!status.toolchain && <p className="lp-muted">Run <code>gaia learn setup</code> once to install the training toolchain.</p>}
                                <div className="lp-row">
                                    <button className="btn-primary lp-btn" disabled={running || !toolchainReady || !status.base_model_trainable}
                                        onClick={() => void run(learn.startRun)}>
                                        <Play size={12} /> Learn now
                                    </button>
                                    {status.active_adapter && (
                                        <button className="btn-secondary lp-btn" onClick={() => void run(learn.deactivateAdapter)}>
                                            Use the base model
                                        </button>
                                    )}
                                </div>
                            </section>

                            {status.latest_run && (
                                <section className="lp-section">
                                    <h4>Latest run</h4>
                                    <RunCard run={status.latest_run} onStop={() => void run(learn.stopRun)} />
                                </section>
                            )}

                            <section className="lp-section">
                                <h4>Adapters</h4>
                                {adapters.length === 0 ? (
                                    <p className="lp-muted">No adapters yet. The first one is trained after your first night with learning on.</p>
                                ) : (
                                    <ul className="lp-adapters">
                                        {adapters.map((a) => <AdapterRow key={a.id} adapter={a} onChanged={() => void refresh()} onError={setError} />)}
                                    </ul>
                                )}
                            </section>

                            <section className="lp-section">
                                <h4>What it has learned</h4>
                                {items.length === 0 ? (
                                    <p className="lp-muted">Nothing yet.</p>
                                ) : (
                                    <>
                                        <ul className="lp-items">
                                            {items.map((i) => <ItemRow key={i.id} item={i} onForget={(id) => void run(() => learn.forgetItem(id))} />)}
                                        </ul>
                                        <p className="lp-muted">Forgetting an item switches off an adapter that learned it; the next one is trained without it.</p>
                                    </>
                                )}
                            </section>
                        </>
                    )}
                </div>
            </div>
        </div>
    );
}
