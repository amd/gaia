// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { useT } from '../../i18n/useT';
import * as api from '../../services/api';
import type { SetupNpuProblem } from '../../types';

const NPU_PROBLEM_KEYS = {
    driver: ['setup.npu.problem.driver', 'setup.npu.fix.driver'],
    device: ['setup.npu.problem.device', 'setup.npu.fix.device'],
    firmware: ['setup.npu.problem.firmware', 'setup.npu.fix.firmware'],
    access: ['setup.npu.problem.access', 'setup.npu.fix.access'],
    memlock: ['setup.npu.problem.memlock', 'setup.npu.fix.memlock'],
    runtime: ['setup.npu.problem.runtime', 'setup.npu.fix.runtime'],
} as const;

/** Why the administrator step didn't happen, by the server's `code`. */
const MEMLOCK_FIX_ERRORS = {
    cancelled: 'setup.npu.fixError.cancelled',
    timeout: 'setup.npu.fixError.timeout',
    not_authorized: 'setup.npu.fixError.notAuthorized',
    no_pkexec: 'setup.npu.fixError.noPkexec',
} as const;

function NpuProblem({ problem }: { problem: SetupNpuProblem }) {
    const t = useT();
    const keys = NPU_PROBLEM_KEYS[problem.code];
    // A code this build doesn't know still says what the server found, in English.
    if (!keys) return <li><strong>{problem.title}</strong> {problem.fix}</li>;
    const p = problem.params;
    let fix: Parameters<typeof t>[0] = keys[1];
    if (problem.code === 'access' && p.group) fix = 'setup.npu.fix.accessGroup';
    let title: Parameters<typeof t>[0] = keys[0];
    if (problem.code === 'memlock' && p.fix === 'restart') {
        title = 'setup.npu.problem.memlockStill';
        fix = 'setup.npu.fix.memlockRestart';
    }
    if (problem.code === 'memlock' && p.fix === 'stuck') {
        title = 'setup.npu.problem.memlockStuck';
        fix = 'setup.npu.fix.memlockStuck';
    }
    return (
        <li>
            <strong>{t(title, { device: p.device ?? '', limit: p.limit ?? '', version: p.version ?? '', detail: p.detail ?? '' })}</strong>{' '}
            {t(fix, { group: p.group ?? '' })}
        </li>
    );
}

/**
 * The NPU is there but can't run a model yet: what is missing, the one-time
 * locked-memory fix when that is it, and a way to check again.
 */
export function NpuNotReady({ problems, disabled, onRecheck, id = 'setup-npu-problems' }: {
    problems: SetupNpuProblem[];
    disabled: boolean;
    /** Read this PC again: the plan or model list, after the server forgot its reading. */
    onRecheck?: () => void;
    id?: string;
}) {
    const t = useT();
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const canFix = problems.some((p) => p.code === 'memlock' && p.params.fix === 'apply');

    const act = async (call: () => Promise<unknown>, failed: 'setup.npu.fixError.failed' | 'setup.npu.recheckFailed') => {
        setBusy(true);
        setError(null);
        try {
            await call();
            onRecheck?.();
        } catch (err) {
            const code = err instanceof api.ApiError ? (err.body?.code as string | undefined) : undefined;
            const key = code && code in MEMLOCK_FIX_ERRORS ? MEMLOCK_FIX_ERRORS[code as keyof typeof MEMLOCK_FIX_ERRORS] : failed;
            setError(t(key));
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="setup-npu-problems" id={id} role="note">
            <p>{t('setup.npu.notReady')}</p>
            <ul>{problems.map((p) => <NpuProblem key={p.code} problem={p} />)}</ul>
            <p className="setup-hint">{t('setup.npu.meanwhile')}</p>
            {error && <p className="setup-npu-error" role="alert">{error}</p>}
            <div className="setup-npu-actions">
                {canFix && (
                    <button type="button" className="btn-primary" disabled={disabled || busy} onClick={() => void act(api.fixSetupNpuMemlock, 'setup.npu.fixError.failed')}>
                        {t('setup.npu.fixMemlock')}
                    </button>
                )}
                {onRecheck && (
                    <button type="button" className="btn-secondary" disabled={disabled || busy} onClick={() => void act(api.recheckSetupNpu, 'setup.npu.recheckFailed')}>
                        {t('setup.npu.recheck')}
                    </button>
                )}
            </div>
        </div>
    );
}
