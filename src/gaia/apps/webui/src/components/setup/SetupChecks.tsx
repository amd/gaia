// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { Check, ChevronRight, Circle, HelpCircle, Loader2, X } from 'lucide-react';
import { useT } from '../../i18n/useT';
import type { PreflightReport, SetupPlan, SetupRunStatus } from '../../types';
import { formatSize, ltrIn, totalMemory } from './format';

/** `question`: waiting on the user, not on work. */
type RowState = 'done' | 'active' | 'waiting' | 'failed' | 'question';

interface Row {
    id: string;
    label: string;
    value: string | null;
    state: RowState;
}

function RowIcon({ state }: { state: RowState }) {
    if (state === 'done') return <Check size={13} aria-hidden="true" />;
    if (state === 'active') return <Loader2 size={13} className="spin" aria-hidden="true" />;
    if (state === 'failed') return <X size={13} aria-hidden="true" />;
    if (state === 'question') return <HelpCircle size={13} aria-hidden="true" />;
    return <Circle size={13} aria-hidden="true" />;
}

interface Props {
    hardware: PreflightReport | null;
    hardwareError: string | null;
    plan: SetupPlan | null;
    serverRun: SetupRunStatus | null;
    /** The user's own Lemonade was found and setup waits for them to choose. */
    awaitingChoice?: boolean;
}

/** "Check this PC": five real checks, with a bar that counts the finished ones. */
export function SetupChecks({ hardware, hardwareError, plan, serverRun, awaitingChoice = false }: Props) {
    const t = useT();
    const [open, setOpen] = useState(false);
    const serverUp = !!plan?.lemonade.reachable;
    const serverFailed = serverRun?.state === 'failed' || serverRun?.state === 'cancelled';
    const whose = t(plan?.lemonade.mode === 'external' ? 'setup.found.serverYours' : 'setup.found.serverOwn');
    const serverFound = plan?.system?.lemonade_version
        ? t('setup.checks.serverFound', { version: plan.system.lemonade_version, whose })
        : whose;
    const hwState: RowState = hardware ? 'done' : hardwareError ? 'failed' : 'active';
    const device = [hardware?.gpu_name, hardware?.npu_detected ? t('setup.checks.npu') : null].filter(Boolean).join(' · ');
    // The setup choice is made from this value. Do not show the OS-visible RAM
    // here: an iGPU carve-out can make it look lower than the next screen.
    const modelMemory = hardwareError ? null
        : plan?.capacity_gb != null ? formatSize(plan.capacity_gb)
            // Like the next screen: the OS figure when the server couldn't size memory.
            : serverUp ? totalMemory(plan?.system?.physical_memory, hardware?.ram_gb)
                : null;
    const memoryState: RowState = hardwareError
        ? 'failed'
        : modelMemory
            ? 'done'
            : serverFailed
                ? 'failed'
                : awaitingChoice
                    ? 'waiting'
                    : 'active';
    const rows: Row[] = [
        // A failed read says why on its first row, so the failure isn't a bare red mark.
        { id: 'device', label: t('setup.checks.device'), value: hardwareError ? `${t('setup.system.unread')} ${hardwareError}` : device || null, state: hwState },
        {
            id: 'memory',
            label: t('setup.found.memory'),
            value: modelMemory,
            state: memoryState,
        },
        {
            id: 'disk',
            label: t('setup.checks.disk'),
            value: hardware?.disk_free_gb != null ? t('setup.checks.diskValue', { size: ltrIn(t.locale, `${Math.round(hardware.disk_free_gb)} GB`) }) : null,
            state: hwState,
        },
        {
            id: 'server',
            label: t('setup.checks.server'),
            // What answered, not that something is running: which Lemonade, and whose.
            value: serverUp ? serverFound : awaitingChoice ? t('setup.checks.serverChoose') : null,
            // Waiting on the user is not work in progress: no spinner.
            state: serverUp ? 'done' : serverFailed ? 'failed' : awaitingChoice ? 'question' : hardware ? 'active' : 'waiting',
        },
        {
            id: 'models',
            label: t('setup.checks.backends'),
            value: serverUp && plan?.catalog
                ? t('setup.checks.backendsValue', { models: String(plan.catalog.length), downloaded: String(plan.catalog.filter((e) => e.downloaded).length) })
                : null,
            state: serverUp && plan?.catalog ? 'done' : serverUp ? 'active' : 'waiting',
        },
    ];
    const finished = rows.filter((r) => r.state === 'done').length;
    const percent = Math.round((finished / rows.length) * 100);
    const list = (
        <ul className="setup-rows" id="setup-check-rows">
            {rows.map((r) => (
                <li key={r.id} className={`setup-result is-${r.state}`}>
                    <RowIcon state={r.state} />
                    <span className="setup-result-label">{r.label}</span>
                    {r.value && <bdi className="setup-result-value">{r.value}</bdi>}
                </li>
            ))}
        </ul>
    );
    // Every check passed: one line, opened to see each check and what it found.
    if (finished === rows.length) {
        return (
            <section className="setup-card setup-checks-done" aria-label={t('setup.check.title')}>
                <p className="setup-result is-done" role="status">
                    <button type="button" className="setup-checks-toggle" aria-expanded={open} aria-controls="setup-check-rows" onClick={() => setOpen((o) => !o)}>
                        <Check size={13} aria-hidden="true" />
                        <span className="setup-result-label">{t('setup.checks.allPassed', { n: String(rows.length) })}</span>
                        <ChevronRight size={13} className={open ? 'is-open' : ''} aria-hidden="true" />
                    </button>
                </p>
                {open && list}
            </section>
        );
    }
    return (
        <section className="setup-card" aria-label={t('setup.check.title')}>
            <div className="setup-bar-head">
                {/* A count that stops moving reads like a hang; say who it waits on. */}
                {awaitingChoice
                    ? <span>{t('setup.checks.waitingChoice')}</span>
                    : <>
                        <span>{t('setup.checks.count', { done: String(finished), total: String(rows.length) })}</span>
                        <bdi dir="ltr">{percent}%</bdi>
                    </>}
            </div>
            <progress className="setup-progress" max={100} value={percent} aria-label={t('setup.check.title')} />
            {list}
        </section>
    );
}
