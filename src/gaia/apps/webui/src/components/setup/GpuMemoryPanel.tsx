// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import { AlertTriangle, Check, Copy, Info, Loader2, RotateCcw } from 'lucide-react';
import * as api from '../../services/api';
import { useT } from '../../i18n/useT';
import type { Say } from '../../i18n/registry';
import type { GpuMemoryPreset, GpuMemoryStatus } from '../../types';
import { ConfirmSheet } from './ConfirmSheet';
import { SetupFooter } from './SetupFooter';
import { formatSize } from './format';

type Outcome = { kind: 'applied' } | { kind: 'denied' } | { kind: 'error'; message: string } | null;

/** "Windows" / "Linux", for "GAIA keeps at least 16 GB for Windows and your apps." */
function osName(os: string): string {
    return os === 'windows' ? 'Windows' : os === 'linux' ? 'Linux' : os;
}

/** The literal a guided step asks the user to run, for its Copy button. */
export function copyableCommand(step: string): string | null {
    const m = /^Run:\s*(.+)$/.exec(step) ?? /^(sudo .+)$/.exec(step);
    return m ? m[1].trim() : null;
}

/**
 * The GPU's share once the knob is at `settingGb`, from the backend's presets,
 * so a message says the same number the split bar shows. Null when no preset
 * names it: the knob's own value is not what the GPU gets, so it is never shown.
 */
export function gpuGbAtSetting(status: GpuMemoryStatus, settingGb: number): number | null {
    if (status.setting && Math.abs(status.setting.current_gb - settingGb) < 0.05 && status.gpu_gb != null) return status.gpu_gb;
    return status.presets?.find((p) => Math.abs(p.setting_gb - settingGb) < 0.05)?.gpu_gb ?? null;
}

/** "Restart to use N GB", or without the size when the backend didn't name it. */
export function restartLine(say: Say, gpuGb: number | null, applied = false): string {
    if (gpuGb == null) return say(applied ? 'gpu.appliedAny' : 'gpu.pending.restartAny');
    return say(applied ? 'gpu.applied' : 'gpu.pending.restart', { gb: formatSize(gpuGb) });
}

/**
 * Focus *el* and scroll only its own scroller to show it: a plain focus() also
 * scrolls overflow-hidden ancestors, which shifts the Settings dialog's body.
 */
function focusInView(el: HTMLElement) {
    el.focus({ preventScroll: true });
    for (let p = el.parentElement; p; p = p.parentElement) {
        const overflow = getComputedStyle(p).overflowY;
        if (overflow !== 'auto' && overflow !== 'scroll') continue;
        const top = el.getBoundingClientRect().top - p.getBoundingClientRect().top;
        if (top < 0 || top > p.clientHeight - 40) p.scrollTop += top - 16;
        return;
    }
}

function errorMessage(err: unknown): string {
    return err instanceof Error ? err.message : String(err);
}

/** The memory split: CPU, the GPU's share now, and the share the user is picking. */
function SplitBar({ status, targetGpuGb }: { status: GpuMemoryStatus; targetGpuGb: number | null }) {
    const t = useT();
    // Tenths, rounded once: the legend's figures then add up to the total shown.
    const tenths = (gb: number) => Math.round(gb * 10);
    const total = tenths(status.total_gb ?? 0);
    const gpu = tenths(status.gpu_gb ?? 0);
    const target = targetGpuGb != null && tenths(targetGpuGb) > gpu ? tenths(targetGpuGb) : null;
    const extra = target != null ? target - gpu : 0;
    const cpu = Math.max(0, total - gpu - extra);
    const gb = (n: number) => formatSize(n / 10);
    const share = (n: number) => (total > 0 ? Math.min(100, (n / total) * 100) : 0);
    const highlight = status.pending?.state === 'took_effect';
    return (
        <div className="gpu-split">
            <div
                className={`gpu-bar${highlight ? ' is-new' : ''}`}
                role="img"
                aria-label={target != null
                    ? t('gpu.split.ariaTarget', { cpu: gb(cpu), gpu: gb(target), now: gb(gpu) })
                    : t('gpu.split.aria', { cpu: gb(cpu), gpu: gb(gpu) })}
            >
                <span className="gpu-seg is-gpu" style={{ inlineSize: `${share(gpu)}%` }} />
                <span className="gpu-seg is-target" style={{ inlineSize: `${share(extra)}%` }} />
                <span className="gpu-seg is-cpu" />
                {target != null && (
                    <span className="gpu-marker" style={{ insetInlineStart: `${share(target)}%` }} />
                )}
            </div>
            <div className="gpu-legend" aria-hidden="true">
                <span className="gpu-key is-gpu"><bdi>{t('gpu.split.gpu', { gb: gb(gpu) })}</bdi></span>
                {target != null && <span className="gpu-key is-target"><bdi>{t('gpu.split.added', { gb: gb(extra) })}</bdi></span>}
                <span className="gpu-key is-cpu"><bdi>{t('gpu.split.cpu', { gb: gb(cpu) })}</bdi></span>
            </div>
        </div>
    );
}

interface PresetPickerProps {
    presets: GpuMemoryPreset[];
    /** null is keeping the GPU's share as it is. */
    value: number | null;
    onChange: (settingGb: number | null) => void;
    recommended: number | null;
    max: number | null;
    /** The GPU's share now, offered first as the choice to change nothing; null hides it. */
    keepGb: number | null;
    disabled?: boolean;
}

/** A radiogroup of safe sizes, after keeping the current one; arrow keys move between them. */
function PresetPicker({ presets, value, onChange, recommended, max, keepGb, disabled }: PresetPickerProps) {
    const t = useT();
    const refs = useRef<Array<HTMLButtonElement | null>>([]);
    const choices: Array<number | null> = [...(keepGb != null ? [null] : []), ...presets.map((p) => p.setting_gb)];
    const at = Math.max(0, choices.indexOf(value));
    const onKey = (e: KeyboardEvent) => {
        const delta = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 0;
        if (!delta) return;
        e.preventDefault();
        const rtl = getComputedStyle(e.currentTarget).direction === 'rtl';
        const horizontal = e.key === 'ArrowRight' || e.key === 'ArrowLeft';
        const step = horizontal && rtl ? -delta : delta;
        const next = (at + step + choices.length) % choices.length;
        onChange(choices[next]);
        refs.current[next]?.focus();
    };
    const offset = keepGb != null ? 1 : 0;
    return (
        <div className="gpu-presets" role="radiogroup" aria-label={t('gpu.presets.label')} onKeyDown={onKey}>
            {keepGb != null && (
                <button
                    ref={(el) => { refs.current[0] = el; }}
                    type="button"
                    role="radio"
                    aria-checked={value === null}
                    tabIndex={at === 0 ? 0 : -1}
                    className={`gpu-preset${value === null ? ' is-active' : ''}`}
                    disabled={disabled}
                    onClick={() => onChange(null)}
                >
                    <bdi>{t('gpu.presets.keep', { gb: formatSize(keepGb) })}</bdi>
                </button>
            )}
            {presets.map((p, i) => (
                <button
                    key={p.setting_gb}
                    ref={(el) => { refs.current[i + offset] = el; }}
                    type="button"
                    role="radio"
                    aria-checked={p.setting_gb === value}
                    tabIndex={i + offset === at ? 0 : -1}
                    className={`gpu-preset${p.setting_gb === value ? ' is-active' : ''}`}
                    disabled={disabled}
                    onClick={() => onChange(p.setting_gb)}
                >
                    <bdi>{t('gpu.presets.gpu', { gb: formatSize(p.gpu_gb) })}</bdi>
                    {p.setting_gb === recommended && <>{' '}<span className="gpu-tag is-recommended">{t('gpu.presets.recommended')}</span></>}
                    {p.setting_gb === max && <>{' '}<span className="gpu-tag is-max">{t('gpu.presets.max')}</span></>}
                </button>
            ))}
        </div>
    );
}

export interface GpuMemoryPanelProps {
    status: GpuMemoryStatus;
    /** The heading to move focus to: on entry in setup, and after a refusal. */
    headingId?: string;
    onStatus: (status: GpuMemoryStatus) => void;
    /** Setup offers Skip and Continue; Settings offers Undo. */
    mode: 'setup' | 'settings';
    names: Record<string, string>;
    onSkip?: () => void;
    onContinue?: () => void;
    /** Setup's Back. */
    onBack?: () => void;
}

/**
 * How this PC's memory is split between the CPU and an integrated GPU, what a
 * bigger GPU share would let run, and the way to change it: GAIA applies it
 * where it can (after consent and an admin prompt), and walks the user through
 * AMD Software or the BIOS where it can't. Every reading comes from the backend
 * re-reading the machine, never from what was asked for.
 */
export function GpuMemoryPanel({ status, onStatus, mode, names, onSkip, onContinue, onBack, headingId }: GpuMemoryPanelProps) {
    const t = useT();
    const focusHeading = useCallback(() => {
        const el = headingId ? document.getElementById(headingId) : null;
        if (el) focusInView(el);
    }, [headingId]);
    useEffect(() => {
        if (mode === 'setup') focusHeading();
    }, [mode, focusHeading]);
    const stepsId = useId();
    const presets = status.presets ?? [];
    // Nothing to gain from a change: keeping the current share is what's picked.
    const [target, setTarget] = useState<number | null>(status.recommended_setting_gb ?? (status.gpu_gb != null ? null : presets[0]?.setting_gb ?? null));
    const [guided, setGuided] = useState(false);
    const [guidedSteps, setGuidedSteps] = useState<string[] | null>(null);
    const [guidedValue, setGuidedValue] = useState<string | null | undefined>(undefined);
    const [sheet, setSheet] = useState<'apply' | 'revert' | null>(null);
    const [busy, setBusy] = useState<'apply' | 'revert' | 'steps' | 'done' | 'keep' | null>(null);
    const [outcome, setOutcome] = useState<Outcome>(null);
    const [copied, setCopied] = useState<string | null>(null);

    const preset = presets.find((p) => p.setting_gb === target) ?? null;
    const recommended = status.recommended_setting_gb ?? null;
    // The top preset is the most GAIA allows: the CPU keeps only its floor there.
    const max = presets.length > 0 && presets[presets.length - 1].setting_gb === status.setting?.max_gb ? presets[presets.length - 1].setting_gb : null;
    const cpuLeft = preset && status.total_gb != null ? Math.max(0, status.total_gb - preset.gpu_gb) : null;
    // No raise unlocks a default model: the split already runs the best one GAIA picks.
    const fitsNow = recommended == null ? (status.models ?? []).find((m) => m.default && m.fits_now) : undefined;
    const unlocks = (preset?.unlocks ?? []).slice(0, 2).map((id) => names[id] ?? id);
    const pending = status.pending ?? null;
    const canApply = status.method === 'apply';
    // guided_value null: nothing this PC can give, so no steps to show.
    const canGuide = status.guided_value !== null;

    const showSteps = useCallback(async () => {
        setGuided(true);
        setGuidedSteps(null);
        setBusy('steps');
        setOutcome(null);
        try {
            const read = await api.getGpuMemory(target ?? undefined);
            setGuidedValue(read.guided_value);
            setGuidedSteps(read.guided_value !== null ? read.guided_steps ?? [] : []);
        } catch (err) {
            setGuidedValue(undefined);
            setGuidedSteps([]);
            setOutcome({ kind: 'error', message: errorMessage(err) });
        } finally {
            setBusy(null);
        }
    }, [target]);

    const apply = async () => {
        if (target == null) return;
        setSheet(null);
        setBusy('apply');
        setOutcome(null);
        try {
            onStatus(await api.applyGpuMemory(target));
            setOutcome({ kind: 'applied' });
        } catch (err) {
            const denied = err instanceof api.ApiError && err.httpStatus === 403;
            setOutcome(denied ? { kind: 'denied' } : { kind: 'error', message: errorMessage(err) });
            if (denied) focusHeading();
        } finally {
            setBusy(null);
        }
    };

    const revert = async () => {
        setSheet(null);
        setBusy('revert');
        setOutcome(null);
        try {
            onStatus(await api.revertGpuMemory());
            setOutcome({ kind: 'applied' });
        } catch (err) {
            setOutcome(err instanceof api.ApiError && err.httpStatus === 403 ? { kind: 'denied' } : { kind: 'error', message: errorMessage(err) });
        } finally {
            setBusy(null);
        }
    };

    /** "I've done this": record what the user set, then read the split again. */
    const doneByHand = async () => {
        if (target == null) return;
        setBusy('done');
        setOutcome(null);
        try {
            onStatus(await api.recordGpuMemoryPending(target));
            setGuided(false);
        } catch (err) {
            setOutcome({ kind: 'error', message: errorMessage(err) });
        } finally {
            setBusy(null);
        }
    };

    const keepCurrent = async () => {
        setBusy('keep');
        setOutcome(null);
        try {
            onStatus(await api.dismissGpuMemoryPending());
        } catch (err) {
            setOutcome({ kind: 'error', message: errorMessage(err) });
        } finally {
            setBusy(null);
        }
    };

    const copy = async (text: string) => {
        try {
            await navigator.clipboard.writeText(text);
            setCopied(text);
        } catch (err) {
            setOutcome({ kind: 'error', message: t('gpu.copyFailed', { reason: errorMessage(err) }) });
        }
    };

    const restartPending = pending?.state === 'restart_required';

    return (
        <div className="gpu">
            <p className="setup-hint">
                {t('gpu.body', { total: formatSize(status.total_gb ?? 0), gpu: formatSize(status.gpu_gb ?? 0) })}
            </p>
            <SplitBar status={status} targetGpuGb={!restartPending && !guided ? preset?.gpu_gb ?? null : null} />

            {pending?.state === 'took_effect' && (
                <div className="gpu-notice is-ok" role="status">
                    <Check size={14} aria-hidden="true" />
                    <span>{t('gpu.pending.tookEffect', { gb: formatSize(status.gpu_gb ?? 0) })}</span>
                    <button type="button" className="btn-secondary" onClick={() => void keepCurrent()} disabled={busy !== null}>{t('gpu.ok')}</button>
                </div>
            )}
            {restartPending && (
                <div className="gpu-notice" role="status">
                    <Info size={14} aria-hidden="true" />
                    <span>
                        {t.compose((say) => [
                            restartLine(say, gpuGbAtSetting(status, pending.setting_gb), outcome?.kind === 'applied'),
                            say('gpu.pending.restartHow'),
                            mode === 'setup' ? say('gpu.pending.downloadsFirst') : null,
                        ].filter(Boolean).join(' '))}
                    </span>
                    {pending.source === 'guided' && (
                        <button type="button" className="btn-secondary" onClick={() => void keepCurrent()} disabled={busy !== null}>
                            {t('gpu.keep', { gb: formatSize(status.gpu_gb ?? 0) })}
                        </button>
                    )}
                </div>
            )}
            {pending?.state === 'not_applied' && (
                <div className="gpu-notice is-warn" role="status">
                    <AlertTriangle size={14} aria-hidden="true" />
                    <span>{t('gpu.pending.notApplied', { gb: formatSize(status.gpu_gb ?? 0) })}</span>
                    <button type="button" className="btn-secondary" onClick={() => void keepCurrent()} disabled={busy !== null}>{t('gpu.keep', { gb: formatSize(status.gpu_gb ?? 0) })}</button>
                </div>
            )}

            {!restartPending && !guided && presets.length > 0 && (
                <>
                    {fitsNow && (
                        <p className="gpu-unlock is-keep">
                            <Check size={13} aria-hidden="true" /> {t('gpu.keepRecommended', { model: names[fitsNow.id] ?? fitsNow.id })}
                        </p>
                    )}
                    <PresetPicker presets={presets} value={target} onChange={setTarget} recommended={recommended} max={max} keepGb={status.gpu_gb ?? null} disabled={busy !== null} />
                    {preset && (
                        <p className="gpu-unlock">
                            {unlocks.length > 0
                                ? t(unlocks.length > 1 ? 'gpu.unlock.two' : 'gpu.unlock.one', { gb: formatSize(preset.gpu_gb), a: unlocks[0], b: unlocks[1] ?? '' })
                                : t('gpu.unlock.none', { gb: formatSize(preset.gpu_gb) })}
                        </p>
                    )}
                    {preset && preset.setting_gb === max && cpuLeft != null && (
                        <p className="gpu-unlock is-warn" role="note">
                            <AlertTriangle size={13} aria-hidden="true" /> {t('gpu.maxWarn', { cpu: formatSize(cpuLeft), os: osName(status.os) })}
                        </p>
                    )}
                    <p className="setup-hint">
                        {t.compose((say) => `${say('gpu.reserve', { gb: formatSize(status.reserve_gb ?? 16), os: osName(status.os) })} ${say('gpu.needs')}`)}
                    </p>
                    {!canApply && status.method_reason && <p className="setup-hint"><bdi dir="ltr">{status.method_reason}</bdi></p>}
                </>
            )}
            {!restartPending && !guided && presets.length === 0 && (
                <p className="setup-hint">{t('gpu.noPresets')}</p>
            )}

            {guided && (
                <div className="gpu-guided">
                    {preset && <p className="setup-hint" id={stepsId}>{t('gpu.guided.lede', { gb: formatSize(preset.gpu_gb) })}</p>}
                    {guidedSteps === null ? (
                        <p className="setup-hint" role="status"><Loader2 size={13} className="spin" aria-hidden="true" /> {t('gpu.guided.loading')}</p>
                    ) : guidedValue === null ? (
                        <p className="setup-hint">{t('gpu.noPresets')}</p>
                    ) : (
                        <ol className="gpu-steps" aria-labelledby={stepsId}>
                            {guidedSteps.map((step) => {
                                const cmd = copyableCommand(step);
                                return (
                                    <li key={step}>
                                        <span>{step}</span>
                                        {cmd && (
                                            <button type="button" className="btn-secondary gpu-copy" onClick={() => void copy(cmd)} aria-label={t('gpu.copyAria', { what: cmd })}>
                                                {copied === cmd ? <Check size={12} aria-hidden="true" /> : <Copy size={12} aria-hidden="true" />}
                                                {copied === cmd ? t('gpu.copied') : t('gpu.copy')}
                                            </button>
                                        )}
                                    </li>
                                );
                            })}
                        </ol>
                    )}
                    <SetupFooter>
                        <button type="button" className="btn-secondary" onClick={() => setGuided(false)} disabled={busy !== null}>{t('gpu.guided.back')}</button>
                        {!canApply && guidedValue !== null && (
                            <button type="button" className="btn-primary" onClick={() => void doneByHand()} disabled={busy !== null || target == null}>
                                {busy === 'done' && <Loader2 size={13} className="spin" aria-hidden="true" />} {t('gpu.guided.done')}
                            </button>
                        )}
                    </SetupFooter>
                </div>
            )}

            {busy === 'apply' || busy === 'revert' ? (
                <p className="setup-hint" role="status"><Loader2 size={13} className="spin" aria-hidden="true" /> {t('gpu.waiting')}</p>
            ) : null}
            {outcome?.kind === 'denied' && (
                <div className="gpu-notice is-warn" role="alert">
                    <AlertTriangle size={14} aria-hidden="true" />
                    <span>{t('gpu.denied')}</span>
                </div>
            )}
            {outcome?.kind === 'error' && <p className="setup-error" role="alert">{outcome.message}</p>}

            {!guided && (() => {
                const apply = !restartPending && canApply && presets.length > 0 && (
                    <button type="button" className="btn-primary" onClick={() => setSheet('apply')} disabled={busy !== null || target == null}>
                        {outcome?.kind === 'denied' || pending?.state === 'not_applied' ? t('gpu.tryAgain') : t('gpu.apply')}
                    </button>
                );
                const showMe = !restartPending && presets.length > 0 && canGuide && (
                    <button type="button" className={canApply ? 'btn-secondary' : 'btn-primary'} onClick={() => void showSteps()} disabled={busy !== null || target == null}>
                        {t('gpu.showMe')}
                    </button>
                );
                if (mode === 'setup') {
                    // Back first, then the ways on, the main one last.
                    return (
                        <SetupFooter>
                            {onBack && <button type="button" className="btn-secondary" onClick={onBack}>{t('setup.back')}</button>}
                            {restartPending
                                ? onContinue && <button type="button" className="btn-primary" onClick={onContinue} autoFocus>{t('setup.continue')}</button>
                                : target == null && presets.length > 0
                                    ? (onSkip ?? onContinue) && <button type="button" className="btn-primary" onClick={onSkip ?? onContinue} disabled={busy !== null}>{t('gpu.keepAsIs')}</button>
                                    : <>
                                    {onSkip && <button type="button" className="btn-secondary" onClick={onSkip} disabled={busy !== null}>{t('gpu.skip')}</button>}
                                    {canApply ? <>{showMe}{apply}</> : showMe}
                                </>}
                        </SetupFooter>
                    );
                }
                return (
                    <div className="setup-row gpu-actions">
                        {apply}
                        {showMe}
                        {status.revert?.available && (
                            <button type="button" className="btn-secondary" onClick={() => setSheet('revert')} disabled={busy !== null}>
                                <RotateCcw size={13} aria-hidden="true" /> {t('gpu.undo')}
                            </button>
                        )}
                    </div>
                );
            })()}

            {sheet === 'apply' && target != null && (
                <ConfirmSheet
                    title={preset ? t('gpu.consent.title', { gb: formatSize(preset.gpu_gb) }) : t('gpu.consent.titleAny')}
                    confirmLabel={t('setup.continue')}
                    cancelLabel={t('setup.cancel')}
                    onConfirm={() => void apply()}
                    onCancel={() => setSheet(null)}
                >
                    <p>{preset ? t('gpu.consent.body', { name: status.setting?.name ?? '', gb: formatSize(preset.gpu_gb) }) : t('gpu.consent.bodyAny', { name: status.setting?.name ?? '' })}</p>
                    <p className="confirm-note">{t('gpu.consent.saved', { gb: formatSize(status.gpu_gb ?? 0) })}</p>
                </ConfirmSheet>
            )}
            {sheet === 'revert' && (
                <ConfirmSheet
                    title={t('gpu.revert.title')}
                    confirmLabel={t('setup.continue')}
                    cancelLabel={t('setup.cancel')}
                    onConfirm={() => void revert()}
                    onCancel={() => setSheet(null)}
                >
                    <p>{t('gpu.revert.body')}</p>
                </ConfirmSheet>
            )}
        </div>
    );
}
