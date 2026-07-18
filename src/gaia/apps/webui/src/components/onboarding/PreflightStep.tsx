// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import { CheckCircle2, AlertTriangle, XCircle, Loader2, RefreshCw } from 'lucide-react';
import * as api from '../../services/api';
import { log } from '../../utils/logger';
import type { PreflightReport } from '../../types';

interface PreflightStepProps {
    /**
     * Called whenever the scan resolves so the wizard can guard the Next
     * button: a hard blocker (``compatible === false``) blocks advancing;
     * anything else clears the guard. While the scan is in flight the step is
     * guarded too — we never let the user past an unknown hardware state.
     */
    onGuardChange: (blocked: boolean) => void;
    onReport?: (report: PreflightReport) => void;
}

type ScanState =
    | { kind: 'loading' }
    | { kind: 'error'; message: string }
    | { kind: 'ready'; report: PreflightReport };

function fmtGb(v: number | null): string {
    return v === null ? '—' : `${v} GB`;
}

/** Step 2 — hardware pre-flight scan with gating on hard blockers (#1727). */
export function PreflightStep({ onGuardChange, onReport }: PreflightStepProps) {
    const [state, setState] = useState<ScanState>({ kind: 'loading' });

    const scan = useCallback(async () => {
        setState({ kind: 'loading' });
        onGuardChange(true); // block advancing until we know the result
        try {
            const report = await api.getOnboardingPreflight();
            setState({ kind: 'ready', report });
            onReport?.(report);
            // Only a hard blocker keeps the guard on. Warnings are advisory.
            onGuardChange(!report.compatible);
        } catch (err) {
            const message = err instanceof Error ? err.message : 'Hardware scan failed.';
            log.system.error('Preflight scan failed', err);
            setState({ kind: 'error', message });
            onGuardChange(true); // can't verify — don't let the user past silently
        }
    }, [onGuardChange, onReport]);

    useEffect(() => {
        scan();
    }, [scan]);

    if (state.kind === 'loading') {
        return (
            <div className="onboarding-body" data-testid="onboarding-preflight-loading">
                <h2>Checking your hardware…</h2>
                <p className="lede" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Loader2 size={16} className="onboarding-spin" /> Scanning RAM, disk, and accelerators.
                </p>
            </div>
        );
    }

    if (state.kind === 'error') {
        return (
            <div className="onboarding-body" data-testid="onboarding-preflight-error">
                <h2>Couldn't scan your hardware</h2>
                <div className="onboarding-banner error" role="alert">
                    <XCircle size={18} />
                    <div>
                        {state.message}
                        <div style={{ marginTop: 6 }}>
                            Make sure the GAIA backend is running, then retry.
                        </div>
                    </div>
                </div>
                <button className="onboarding-btn secondary" onClick={scan}>
                    <RefreshCw size={15} /> Retry scan
                </button>
            </div>
        );
    }

    const { report } = state;
    const ramOk = report.ram_gb === null || report.ram_gb >= report.required_memory_gb;
    const diskOk = report.disk_free_gb === null || report.disk_free_gb >= report.required_disk_gb;

    return (
        <div className="onboarding-body" data-testid="onboarding-preflight">
            <h2>Hardware check</h2>
            <span className="onboarding-tier">Tier: {report.tier}</span>

            <div className="preflight-rows">
                <PfRow
                    ok={ramOk}
                    label="Memory (RAM)"
                    value={fmtGb(report.ram_gb)}
                />
                <PfRow
                    ok={diskOk}
                    hardFail={!diskOk}
                    label="Free disk space"
                    value={fmtGb(report.disk_free_gb)}
                />
                <PfRow
                    ok={report.npu_detected === true}
                    unknown={report.npu_detected === null}
                    label="Ryzen AI NPU"
                    value={
                        report.npu_detected === true
                            ? 'Detected'
                            : report.npu_detected === false
                                ? 'Not found'
                                : 'Unknown'
                    }
                />
                {report.gpu_name && (
                    <PfRow ok label="GPU" value={report.gpu_name} />
                )}
            </div>

            {!report.compatible && report.blockers.length > 0 && (
                <div className="onboarding-banner error" role="alert" data-testid="preflight-blockers">
                    <XCircle size={18} />
                    <div>
                        <strong>This machine can't run the setup yet:</strong>
                        <ul>
                            {report.blockers.map((b, i) => <li key={i}>{b}</li>)}
                        </ul>
                    </div>
                </div>
            )}

            {report.warnings.length > 0 && (
                <div className="onboarding-banner warn" data-testid="preflight-warnings">
                    <AlertTriangle size={18} />
                    <div>
                        <ul>
                            {report.warnings.map((w, i) => <li key={i}>{w}</li>)}
                        </ul>
                    </div>
                </div>
            )}

            <button className="onboarding-btn secondary" onClick={scan}>
                <RefreshCw size={15} /> Re-scan
            </button>
        </div>
    );
}

function PfRow({ ok, hardFail, unknown, label, value }: {
    ok: boolean;
    hardFail?: boolean;
    unknown?: boolean;
    label: string;
    value: string;
}) {
    const icon = unknown ? (
        <AlertTriangle size={17} />
    ) : ok ? (
        <CheckCircle2 size={17} />
    ) : (
        hardFail ? <XCircle size={17} /> : <AlertTriangle size={17} />
    );
    const cls = unknown ? 'warn' : ok ? 'ok' : hardFail ? 'bad' : 'warn';
    return (
        <div className="preflight-row">
            <span className={`pf-icon ${cls}`}>{icon}</span>
            <span className="pf-label">{label}</span>
            <span className="pf-value">{value}</span>
        </div>
    );
}
