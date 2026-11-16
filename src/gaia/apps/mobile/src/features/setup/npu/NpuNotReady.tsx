// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { ltrIn } from '@webui/components/setup/format';
import type { SetupNpuProblem, SetupPlan } from '@webui/types';

import { GaiaApiError } from '@/api/errors';
import { useRecheckNpu } from '@/data';
import { useLocaleCode, useT, type Translate } from '@/i18n/useT';
import { Button, Notice, Stack, Text } from '@/ui';

type Key = Parameters<Translate>[0];

const NPU_PROBLEM_KEYS: Record<SetupNpuProblem['code'], readonly [Key, Key]> = {
    driver: ['setup.npu.problem.driver', 'setup.npu.fix.driver'],
    device: ['setup.npu.problem.device', 'setup.npu.fix.device'],
    firmware: ['setup.npu.problem.firmware', 'setup.npu.fix.firmware'],
    access: ['setup.npu.problem.access', 'setup.npu.fix.access'],
    memlock: ['setup.npu.problem.memlock', 'setup.npu.fix.memlock'],
    runtime: ['setup.npu.problem.runtime', 'setup.npu.fix.runtime'],
};

function Problem({ problem }: { problem: SetupNpuProblem }) {
    const t = useT();
    const locale = useLocaleCode();
    const keys = NPU_PROBLEM_KEYS[problem.code];
    // A code this build doesn't know still says what the server found, in English.
    if (!keys) return <Text role="label">{`${problem.title} ${problem.fix}`}</Text>;
    const p = problem.params;
    let [title, fix] = keys;
    if (problem.code === 'access' && p.group) fix = 'setup.npu.fix.accessGroup';
    if (problem.code === 'memlock' && p.fix === 'restart') [title, fix] = ['setup.npu.problem.memlockStill', 'setup.npu.fix.memlockRestart'];
    if (problem.code === 'memlock' && p.fix === 'stuck') [title, fix] = ['setup.npu.problem.memlockStuck', 'setup.npu.fix.memlockStuck'];
    const ltr = (v: string | undefined) => ltrIn(locale, v ?? '');
    return (
        <Stack gap="xs">
            <Text role="label" weight="semibold">
                {t(title, { device: ltr(p.device), limit: ltr(p.limit), version: ltr(p.version), detail: ltr(p.detail) })}
            </Text>
            <Text role="label" tone="secondary">{t(fix, { group: ltr(p.group) })}</Text>
        </Stack>
    );
}

/**
 * The NPU is there but can't run a model yet: what is missing, and a way to check again.
 * The locked-memory fix starts an administrator prompt on the PC, so here it is only shown.
 * Check again refreshes the cached plan, so a parent reading it updates; `onReady` is also told the fresh plan.
 */
export function NpuNotReady({ problems, disabled = false, onReady }: {
    problems: SetupNpuProblem[];
    disabled?: boolean;
    onReady?: (plan: SetupPlan) => void;
}) {
    const t = useT();
    const recheck = useRecheckNpu();
    const canFix = problems.some((p) => p.code === 'memlock' && p.params.fix === 'apply');
    const failure = recheck.error;
    const refused = failure instanceof GaiaApiError && failure.status === 403;

    return (
        <Stack gap="md" testID="npu-not-ready">
            <Text>{t('setup.npu.notReady')}</Text>
            {problems.map((p) => <Problem key={p.code} problem={p} />)}
            <Text role="caption" tone="secondary">{t('setup.npu.meanwhile')}</Text>
            {failure ? (
                <Notice tone={refused ? 'info' : 'danger'} message={refused ? t('setup.plan.pcOnly') : t('setup.npu.recheckFailed')} />
            ) : null}
            {canFix ? (
                <Stack gap="xs">
                    <Button variant="secondary" label={t('setup.npu.fixMemlock')} disabled onPress={() => undefined} />
                    <Text role="caption" tone="muted">{t('setup.plan.pcOnly')}</Text>
                </Stack>
            ) : null}
            <Button
                variant="secondary"
                label={t('setup.npu.recheck')}
                busy={recheck.isPending}
                disabled={disabled || recheck.isPending}
                onPress={() => recheck.mutate(undefined, { onSuccess: (plan) => onReady?.(plan) })}
            />
        </Stack>
    );
}
