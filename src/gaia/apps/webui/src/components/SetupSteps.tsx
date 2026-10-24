// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { AlertCircle, Check, Info, Loader2 } from 'lucide-react';
import * as api from '../services/api';
import { shortModelName } from '../stores/modelStore';
import { formatGb } from '../utils/modelLabels';
import type { PreflightReport, SetupPassedOver, SetupPlan } from '../types';

/** One line of the system check: what was found, or that it is checked later. */
function CheckRow({ label, value, ok }: { label: string; value: string; ok: boolean }) {
    return (
        <li className={`setup-check-row${ok ? ' is-ok' : ''}`}>
            {ok ? <Check size={13} aria-hidden="true" /> : <Info size={13} aria-hidden="true" />}
            <span className="setup-check-label">{label}</span>
            <span className="setup-check-value">{value}</span>
        </li>
    );
}

function deviceText(hw: PreflightReport): { value: string; ok: boolean } {
    const parts = [hw.gpu_name, hw.npu_detected ? 'Ryzen AI NPU' : null].filter(Boolean);
    if (parts.length) return { value: parts.join(' · '), ok: true };
    // Lemonade reports the devices, so before it runs they are simply not known yet.
    if (!hw.lemonade_running) return { value: 'Checked once the model server is running', ok: false };
    return { value: 'No GPU or NPU reported; models run on the CPU', ok: false };
}

/**
 * Device, memory and disk, read the moment setup opens. Once the plan knows
 * the real download, the disk line follows it instead of the first estimate.
 */
export function SystemCheck({ hardware, error, plan = null, cloud = false }: {
    hardware: PreflightReport | null; error: string | null; plan?: SetupPlan | null;
    /** A cloud choice downloads no chat model, so the estimate's disk need doesn't apply. */
    cloud?: boolean;
}) {
    if (error) return <p className="setup-hint">This PC&rsquo;s hardware couldn&rsquo;t be read ({error}). Setup still checks it as it goes.</p>;
    if (!hardware) {
        return (
            <p className="setup-hint" role="status">
                <Loader2 size={13} className="spin" aria-hidden="true" /> Checking this PC…
            </p>
        );
    }
    const device = deviceText(hardware);
    // Before a model server runs, the NPU can't be asked about yet; the Device row says so.
    const warnings = hardware.lemonade_running
        ? hardware.warnings
        : hardware.warnings.filter((w) => !/\bNPU\b/.test(w));
    // The plan's own disk check (shown by the download button) replaces the estimate.
    const planned = plan?.download_gb != null && plan.disk_free_gb != null;
    const estimateDropped = planned || cloud;
    const blockers = estimateDropped ? hardware.blockers.filter((b) => b !== hardware.disk_blocker) : hardware.blockers;
    const compatible = estimateDropped ? blockers.length === 0 : hardware.compatible;
    const disk = planned
        ? { value: `${Math.round(plan.disk_free_gb as number)} GB free · ${formatGb(plan.download_gb as number)} to download`, ok: !plan.disk_problem }
        : {
            value: hardware.disk_free_gb != null ? `${Math.round(hardware.disk_free_gb)} GB free` : 'Not reported',
            ok: hardware.disk_free_gb != null && compatible,
        };
    return (
        <>
            <ul className="setup-check" aria-label="This PC">
                <CheckRow label="Device" value={device.value} ok={device.ok} />
                <CheckRow
                    label="Memory"
                    value={hardware.ram_gb != null ? `${hardware.ram_gb} GB` : 'Not reported'}
                    ok={hardware.ram_gb != null}
                />
                <CheckRow label="Disk" value={disk.value} ok={disk.ok} />
            </ul>
            {!compatible && blockers.length > 0 && (
                <p className="setup-error" role="alert">{blockers.join(' ')}</p>
            )}
            {warnings.length > 0 && <p className="setup-hint">{warnings.join(' ')}</p>}
        </>
    );
}

/** Why setup didn't pick each larger model: the user is told, never left to guess. */
function PassedOver({ items }: { items?: SetupPassedOver[] | null }) {
    if (!items || items.length === 0) return null;
    return (
        <>
            {items.map((p) => (
                <p key={p.id} className="setup-hint">Not {shortModelName(p.id)}: {p.reason}.</p>
            ))}
        </>
    );
}

interface ChatModelProps {
    plan: SetupPlan | null;
    planError: string | null;
    /** The model a running (or finished) setup reported choosing. */
    chosen: string | null;
    /** What that setup said about the larger models it passed over. */
    passedOver?: SetupPassedOver[] | null;
    /** Setup has started, so the choice is fixed. */
    locked: boolean;
    onPlan: (plan: SetupPlan) => void;
}

/** Which chat model setup downloads, named before it starts, with a way to pick another. */
export function ChatModelChoice({ plan, planError, chosen, passedOver, locked, onPlan }: ChatModelProps) {
    const [busy, setBusy] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    // A model the user picked here, or set beforehand, is theirs, not setup's.
    const [picked, setPicked] = useState(false);

    if (chosen) {
        const theirs = picked || (plan?.chat_model.user_set && plan.chat_model.id === chosen);
        return (
            <>
                <p className="setup-hint">
                    {theirs ? 'You chose ' : 'Setup chose '}
                    <strong>{shortModelName(chosen)}</strong>{theirs ? '.' : ' for this PC.'}
                </p>
                {!theirs && <PassedOver items={passedOver} />}
            </>
        );
    }
    if (planError) {
        return <p className="setup-hint">Setup names the chat model once it has checked this PC ({planError}).</p>;
    }
    if (!plan) {
        return (
            <p className="setup-hint" role="status">
                <Loader2 size={13} className="spin" aria-hidden="true" /> Working out which model fits this PC…
            </p>
        );
    }
    const chat = plan.chat_model;
    const name = shortModelName(chat.id);
    if (plan.decided_after_server) {
        const ladder = plan.options.map((o) => shortModelName(o.id));
        return (
            <p className="setup-hint">
                Picked once the model server is running: the largest model your GPU holds
                {ladder.length > 1 ? ` (${ladder.slice(0, -1).join(', ')}, or else ${ladder[ladder.length - 1]})` : ''}
                {' '}that also fits the memory free right now. Setup starts the server first and names the
                model and its download size here before anything large downloads.
            </p>
        );
    }

    const pick = async (id: string) => {
        setBusy(id);
        setError(null);
        try {
            onPlan(await api.chooseSetupChatModel(id));
            setPicked(true);
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setBusy(null);
        }
    };
    const fit = plan.memory_gb != null ? ` This PC has ${plan.memory_gb} GB for models (${plan.memory_source}).` : '';
    const size = chat.downloaded ? 'already downloaded' : chat.size_gb != null ? formatGb(chat.size_gb) : null;
    // A model the user set that isn't one of setup's stays theirs; Settings changes it.
    const own = chat.user_set && !plan.options.some((o) => o.id === chat.id);
    const choosable = !locked && !own && plan.lemonade.reachable
        && plan.options.filter((o) => o.fits !== false).length > 1;

    return (
        <>
            <p className="setup-hint">
                <strong>{name}</strong> will answer your chats{size ? ` (${size})` : ''}.{fit}
                {own && ' It’s your default_model; change it in Settings → Model after setup.'}
            </p>
            {!chat.user_set && <PassedOver items={plan.passed_over} />}
            {chat.memory_warning && (
                <p className="setup-hint">
                    <AlertCircle size={13} aria-hidden="true" /> {chat.memory_warning}
                </p>
            )}
            {choosable && (
                <div className="setup-choices" role="radiogroup" aria-label="Chat model">
                    {plan.options.map((o) => (
                        <button
                            key={o.id}
                            type="button"
                            role="radio"
                            aria-checked={o.id === chat.id}
                            className={`setup-choice${o.id === chat.id ? ' is-active' : ''}`}
                            disabled={o.fits === false || busy !== null}
                            onClick={() => { if (o.id !== chat.id) void pick(o.id); }}
                        >
                            {busy === o.id && <Loader2 size={14} className="spin" aria-hidden="true" />}
                            <span>
                                <span className="setup-choice-title">
                                    {shortModelName(o.id)}
                                    {o.id === plan.recommended_model ? ' (recommended)' : ''}
                                </span>
                                <span className="setup-choice-note">
                                    {o.fits === false ? o.reason : o.size_gb != null ? formatGb(o.size_gb) : 'Size unknown'}
                                </span>
                            </span>
                        </button>
                    ))}
                </div>
            )}
            {error && <p className="setup-error" role="alert">{error}</p>}
        </>
    );
}
