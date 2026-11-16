// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { MessageKey } from '@webui/i18n/registry';
import type { SetupCatalogEntry, SetupRunStatus, SetupTest } from '@webui/types';

type Job = SetupTest['job'];

const JOB_ORDER: Job[] = ['server', 'chat', 'vision', 'embedding', 'speech', 'voice', 'image_creation', 'agent'];
const OPTIONAL_ROLES = ['speech', 'voice', 'image_creation'];

const WHY: Record<string, MessageKey> = {
    memory: 'setup.test.why.memory',
    broken: 'setup.test.why.broken',
    stuck: 'setup.test.why.stuck',
    missing: 'setup.test.why.missing',
    agent: 'setup.test.why.agent',
    answer: 'setup.test.why.answer',
    window: 'setup.test.why.window',
};

/** The jobs this run should end up testing, so the bar has a true total: the server, each model's role, the assistant. */
export function expectedTests(run: SetupRunStatus): Job[] {
    const roles = new Set<string>((run.models ?? []).flatMap((m) => m.roles));
    return JOB_ORDER.filter(
        (job) => job === 'server' || job === 'agent' || (job === 'chat' ? !run.skip_chat_model : roles.has(job)),
    );
}

/** A failed test's plain reason, by what went wrong; anything else gets the general one. */
export function whyKey(row: SetupTest, canRedownload: boolean): MessageKey {
    if (row.kind === 'broken' && !canRedownload) return 'setup.test.why.brokenYours';
    return WHY[row.kind ?? ''] ?? 'setup.test.why.other';
}

export interface TestView {
    rows: SetupTest[];
    waiting: Job[];
    finished: number;
    total: number;
    percent: number;
    stopped: boolean;
    /** The assistant never installed (no download server, or no internet): its test can't run. */
    agentNotInstalled: boolean;
    /** The waiting job the run is starting right now. */
    startingJob: Job | undefined;
    /** The sentence under a stopped run. */
    lead: MessageKey | null;
    /** Only a model's own failure is helped by choosing another model; a missing assistant isn't one. */
    modelAtFault: boolean;
    /** Models whose file didn't load, when this PC's server is GAIA's to delete from. */
    broken: string[];
}

export function testView(run: SetupRunStatus, catalog: SetupCatalogEntry[], canRedownload: boolean): TestView {
    const rows = run.tests ?? [];
    const seen = new Set(rows.map((r) => r.job));
    const waiting = expectedTests(run).filter((job) => !seen.has(job));
    const finished = rows.filter((r) => r.state !== 'running').length;
    const total = Math.max(rows.length + waiting.length, 1);
    // Speech, voice and image creation are extras: a failure there doesn't stop GAIA being ready.
    const required = rows.filter(
        (r) => r.state === 'failed' && !catalog.find((e) => e.id === r.subject)?.roles.some((role) => OPTIONAL_ROLES.includes(role)),
    );
    const stopped = run.state === 'failed' || run.state === 'cancelled';
    const lead: MessageKey | null = !stopped
        ? null
        : run.state === 'cancelled'
          ? 'setup.test.cancelled'
          : required.some((r) => r.kind === 'memory')
            ? 'setup.test.failedMemory'
            : 'setup.test.failedHelp';
    return {
        rows,
        waiting,
        finished,
        total,
        percent: Math.round((finished / total) * 100),
        stopped,
        agentNotInstalled: stopped && run.components?.agent === 'failed',
        startingJob: run.state === 'verifying' && !rows.some((r) => r.state === 'running') ? waiting[0] : undefined,
        lead,
        modelAtFault: required.some((r) => r.subject && r.kind !== 'missing'),
        broken: canRedownload
            ? [...new Set(required.filter((r) => r.kind === 'broken' && r.subject).map((r) => r.subject as string))]
            : [],
    };
}

/** What a repeat of the run fetches besides the models it needs anyway: the optional extras it had. */
export function rerunOptions(run: SetupRunStatus, catalog: SetupCatalogEntry[]): { keepTrying: false; extraModels: string[] } {
    const optional = new Set(catalog.filter((e) => e.support === null).map((e) => e.id));
    return { keepTrying: false, extraModels: (run.models ?? []).map((m) => m.id).filter((id) => optional.has(id)) };
}
