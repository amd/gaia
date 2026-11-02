// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Check, Circle, Loader2, X } from 'lucide-react';
import { useT } from '../../i18n/useT';
import type { PreflightReport, SetupPlan, SetupRunStatus } from '../../types';

type RowState = 'done' | 'active' | 'waiting' | 'failed';

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
    return <Circle size={13} aria-hidden="true" />;
}

interface Props {
    hardware: PreflightReport | null;
    hardwareError: string | null;
    plan: SetupPlan | null;
    serverRun: SetupRunStatus | null;
}

/** "Check this PC": five real checks, with a bar that counts the finished ones. */
export function SetupChecks({ hardware, hardwareError, plan, serverRun }: Props) {
    const t = useT();
    const serverUp = !!plan?.lemonade.reachable;
    const serverFailed = serverRun?.state === 'failed' || serverRun?.state === 'cancelled';
    const hwState: RowState = hardware ? 'done' : hardwareError ? 'failed' : 'active';
    const device = [hardware?.gpu_name, hardware?.npu_detected ? t('setup.checks.npu') : null].filter(Boolean).join(' · ');
    const rows: Row[] = [
        { id: 'device', label: t('setup.checks.device'), value: device || null, state: hwState },
        {
            id: 'memory',
            label: t('setup.checks.memory'),
            value: hardware?.ram_gb != null ? t('setup.checks.memoryValue', { gb: String(hardware.ram_gb) }) : null,
            state: hwState,
        },
        {
            id: 'disk',
            label: t('setup.checks.disk'),
            value: hardware?.disk_free_gb != null ? t('setup.checks.diskValue', { gb: String(Math.round(hardware.disk_free_gb)) }) : null,
            state: hwState,
        },
        {
            id: 'server',
            label: t('setup.checks.server'),
            value: serverUp ? t('setup.checks.serverUp') : null,
            state: serverUp ? 'done' : serverFailed ? 'failed' : hardware ? 'active' : 'waiting',
        },
        {
            id: 'models',
            label: t('setup.checks.backends'),
            value: null,
            state: serverUp && plan?.catalog ? 'done' : serverUp ? 'active' : 'waiting',
        },
    ];
    const finished = rows.filter((r) => r.state === 'done').length;
    const percent = Math.round((finished / rows.length) * 100);
    return (
        <section className="setup-card" aria-label={t('setup.check.title')}>
            <div className="setup-bar-head">
                <span>{t('setup.checks.count', { done: String(finished), total: String(rows.length) })}</span>
                <bdi dir="ltr">{percent}%</bdi>
            </div>
            <progress className="setup-progress" max={100} value={percent} aria-label={t('setup.check.title')} />
            <ul className="setup-rows">
                {rows.map((r) => (
                    <li key={r.id} className={`setup-result is-${r.state}`}>
                        <RowIcon state={r.state} />
                        <span className="setup-result-label">{r.label}</span>
                        {r.value && <bdi className="setup-result-value">{r.value}</bdi>}
                    </li>
                ))}
            </ul>
        </section>
    );
}
