// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { formatSize, ltrIn, totalMemory } from '@webui/components/setup/format';
import type { MessageKey } from '@webui/i18n/registry';

import type { PreflightReport, SetupPlan, SetupRunStatus } from '@/api/client.types';
import type { Translate } from '@/i18n/useT';

/** `question`: waiting on the person, not on work. */
export type CheckRowState = 'done' | 'active' | 'waiting' | 'failed' | 'question';

export interface CheckRow {
    id: 'device' | 'memory' | 'disk' | 'server' | 'models';
    label: MessageKey;
    value: string | null;
    state: CheckRowState;
}

export interface CheckInputs {
    hardware: PreflightReport | null;
    /** Why the hardware couldn't be read, already in words. */
    hardwareError: string | null;
    plan: SetupPlan | null;
    serverRun: SetupRunStatus | null;
    /** The PC's own model server was found and the PC has yet to choose. */
    awaitingChoice: boolean;
}

/** The five checks of "Check this PC", as webui's `SetupChecks` computes them. */
export function checkRows({ hardware, hardwareError, plan, serverRun, awaitingChoice }: CheckInputs, t: Translate): CheckRow[] {
    const serverUp = !!plan?.lemonade.reachable;
    const serverFailed = serverRun?.state === 'failed' || serverRun?.state === 'cancelled';
    const whose = t(plan?.lemonade.mode === 'external' ? 'setup.found.serverYours' : 'setup.found.serverOwn');
    const serverFound = plan?.system?.lemonade_version
        ? t('setup.checks.serverFound', { version: plan.system.lemonade_version, whose })
        : whose;
    const hwState: CheckRowState = hardware ? 'done' : hardwareError ? 'failed' : 'active';
    const device = [hardware?.gpu_name, hardware?.npu_detected ? t('setup.checks.npu') : null].filter(Boolean).join(' · ');
    // The next screen decides from this value, so it is the plan's, not the OS's smaller figure.
    const modelMemory = hardwareError
        ? null
        : plan?.capacity_gb != null
          ? formatSize(plan.capacity_gb)
          : serverUp
            ? totalMemory(plan?.system?.physical_memory, hardware?.ram_gb)
            : null;
    const memoryState: CheckRowState = hardwareError
        ? 'failed'
        : modelMemory
          ? 'done'
          : serverFailed
            ? 'failed'
            : awaitingChoice
              ? 'waiting'
              : 'active';
    const catalogRead = serverUp && !!plan?.catalog;
    return [
        {
            id: 'device',
            label: 'setup.checks.device',
            value: hardwareError ? `${t('setup.system.unread')} ${hardwareError}` : device || null,
            state: hwState,
        },
        { id: 'memory', label: 'setup.found.memory', value: modelMemory, state: memoryState },
        {
            id: 'disk',
            label: 'setup.checks.disk',
            value:
                hardware?.disk_free_gb != null
                    ? t('setup.checks.diskValue', { size: ltrIn(t.locale, `${Math.round(hardware.disk_free_gb)} GB`) })
                    : null,
            state: hwState,
        },
        {
            id: 'server',
            label: 'setup.checks.server',
            value: serverUp ? serverFound : awaitingChoice ? t('setup.checks.serverChoose') : null,
            state: serverUp ? 'done' : serverFailed ? 'failed' : awaitingChoice ? 'question' : hardware ? 'active' : 'waiting',
        },
        {
            id: 'models',
            label: 'setup.checks.backends',
            value: catalogRead
                ? t('setup.checks.backendsValue', {
                      models: String(plan!.catalog!.length),
                      downloaded: String(plan!.catalog!.filter((e) => e.downloaded).length),
                  })
                : null,
            state: catalogRead ? 'done' : serverUp ? 'active' : 'waiting',
        },
    ];
}

/** Which lede the heading carries, from what setup found on disk. */
export function checkLede(done: boolean, plan: SetupPlan | null, ticked: ReadonlySet<string>): MessageKey {
    if (!done) return 'setup.check.lede';
    const catalog = plan?.catalog ?? [];
    const onPc = catalog.filter((e) => ticked.has(e.id) && e.downloaded).length;
    if (onPc === 0) return catalog.some((e) => e.downloaded) ? 'setup.check.ledeOthersOnPc' : 'setup.check.ledeDone';
    return catalog.some((e) => ticked.has(e.id) && !e.downloaded) ? 'setup.check.ledeSomeOnPc' : 'setup.check.ledeAllOnPc';
}

/** What the PC read raised for the person to see: blockers, and warnings (the NPU one waits for a running server). */
export function findingWarnings(hardware: PreflightReport | null): string[] {
    return [
        ...(hardware?.blockers ?? []),
        ...(hardware?.warnings ?? []).filter((w) => hardware?.lemonade_running || !/\bNPU\b/.test(w)),
    ];
}
