// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { MessageKey } from '@webui/i18n/registry';

import type { SetupPlan, SetupRunStatus } from '@/api/client.types';

/** The five steps of the rail, in order. Download and Test are one run; the run's rows say which. */
export const SETUP_STAGES = ['check', 'plan', 'download', 'test', 'ready'] as const;
export type SetupStage = (typeof SETUP_STAGES)[number];

export const STAGE_LABELS: Record<SetupStage, MessageKey> = {
    check: 'mobileApp.setup.check.title',
    plan: 'setup.plan.step',
    download: 'setup.download.step',
    test: 'setup.test.step',
    ready: 'setup.ready.step',
};

export type RailStatus = 'done' | 'current' | 'active' | 'failed' | 'pending';

export function isRunning(run: SetupRunStatus | null | undefined): boolean {
    return run?.state === 'running' || run?.state === 'verifying';
}

/** A run that started the model server only is not the download. */
export function fullRun(run: SetupRunStatus | null | undefined): SetupRunStatus | null {
    return run && !run.server_only ? run : null;
}

/** Where setup is, as the web UI's `liveStage`: the run decides once the download has started. */
export function liveStageOf(phase: 'check' | 'plan' | 'download', run: SetupRunStatus | null | undefined): SetupStage {
    const full = fullRun(run);
    if (full?.state === 'ready') return 'ready';
    if (phase !== 'download') return phase;
    const pulling = (full?.models ?? []).some((m) => m.state === 'downloading' || m.state === 'queued');
    const testing = !pulling && ((full?.tests?.length ?? 0) > 0 || full?.state === 'verifying');
    return testing ? 'test' : 'download';
}

export interface RailStep {
    id: SetupStage;
    status: RailStatus;
    /** The step on screen, which may be behind where setup is. */
    shown: boolean;
    /** Only steps setup has reached can be opened (a run's steps only once it is there). */
    reachable: boolean;
}

export function railSteps(args: {
    live: SetupStage;
    shown: SetupStage;
    running: boolean;
    failed: boolean;
    visited: ReadonlySet<SetupStage>;
}): RailStep[] {
    const at = SETUP_STAGES.indexOf(args.live);
    return SETUP_STAGES.map((id, i) => {
        const runStep = id === 'download' || id === 'test' || id === 'ready';
        const status: RailStatus =
            i < at ? 'done'
            : i > at ? 'pending'
            : args.live === 'ready' ? 'done'
            : args.failed && args.live !== 'plan' ? 'failed'
            : args.running && (args.live === 'download' || args.live === 'test') ? 'active'
            : 'current';
        return {
            id,
            status,
            shown: id === args.shown,
            reachable: i <= at || (args.visited.has(id) && !runStep),
        };
    });
}

/** Previous and next on the top navigation: any step already reached, and none past where setup is. */
export function navTargets(steps: RailStep[]): { prev: SetupStage | null; next: SetupStage | null } {
    const at = steps.findIndex((s) => s.shown);
    if (at < 0) return { prev: null, next: null };
    return {
        prev: at > 0 ? steps[at - 1]!.id : null,
        next: at < steps.length - 1 && steps[at + 1]!.reachable ? steps[at + 1]!.id : null,
    };
}

/** A download that didn't finish last time: offer to resume, never resume on its own. */
export function offersResume(plan: SetupPlan | undefined, run: SetupRunStatus | null | undefined): boolean {
    if (run && !(run.state === 'idle' && !run.models?.length)) return false;
    return !!plan?.catalog?.some((e) => e.partial && e.selected);
}

/** GAIA restarted and lost the run: it had models and is now idle with none. */
export function runWasLost(before: SetupRunStatus | null | undefined, now: SetupRunStatus | undefined): boolean {
    return !!before && !!before.models?.length && now?.state === 'idle' && !now.models?.length;
}
