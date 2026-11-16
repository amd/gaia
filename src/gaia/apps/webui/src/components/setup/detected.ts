// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** What setup found about this PC, as plain data: the web summary and the phone's read it the same way. */

import type { MessageKey } from '../../i18n/registry';
import type { PreflightReport, SetupDevice, SetupGaiaBuild, SetupPlan, SetupRole } from '../../types';
import { formatSize, totalMemory } from './format';

export function deviceText(d: SetupDevice): string {
    return d.family ? `${d.name} (${d.family})` : d.name;
}

/** The runtimes GAIA uses, by the names people know them by. The first two run chat. */
export const RUNTIMES: Array<[recipe: string, name: string]> = [
    ['llamacpp', 'llama.cpp'],
    ['flm', 'FastFlowLM'],
    ['whispercpp', 'whisper.cpp'],
    ['kokoro', 'Kokoro'],
    ['sd-cpp', 'stable-diffusion.cpp'],
];
export const CHAT_RUNTIMES = new Set(['llamacpp', 'flm']);

export type Backend = NonNullable<SetupPlan['backends']>[number];

export function runtimesOf(plan: SetupPlan): Array<{ name: string; b: Backend }> {
    const out: Array<{ name: string; b: Backend }> = [];
    for (const [recipe, name] of RUNTIMES) {
        const b = (plan.backends ?? []).find((x) => x.recipe === recipe && x.default);
        if (b && b.state !== 'unsupported') out.push({ name, b });
    }
    return out;
}

/** GAIA's version as people say it: "0.25.5 RC 2", or the commit of a source build. */
export function gaiaVersion(build: SetupGaiaBuild | undefined, plain: string | undefined): string | null {
    if (!build) return plain ?? null;
    if (build.kind === 'release_candidate' && build.rc) return `${build.version.replace(/rc\d+$/, '')} RC ${build.rc}`;
    if (build.kind === 'source' && build.commit) return `${build.version} (${build.commit})`;
    return build.version;
}

export const BUILD_WORDS: Record<SetupGaiaBuild['kind'], string> = {
    release: 'release',
    release_candidate: 'release candidate',
    local: 'local build',
    source: 'from source',
};

/** The facts the summary is built from, read once for the screen and the copy. */
export function facts(plan: SetupPlan, hardware: PreflightReport | null) {
    const sys = plan.system;
    const devices = (pick: (k: string) => boolean) => (sys?.devices ?? []).filter((d) => d.available && pick(d.kind));
    const gpus = devices((k) => k.endsWith('gpu'));
    const npus = devices((k) => k.endsWith('npu'));
    const runtimes = runtimesOf(plan);
    return {
        sys,
        cpu: devices((k) => k === 'cpu')[0],
        gpus,
        // Apple Silicon reports no separate GPU device; the chip is the GPU.
        graphics: gpus.length ? gpus.map(deviceText).join('; ') : hardware?.gpu_name ?? null,
        npus,
        runsOn: gpus[0]?.name ?? hardware?.gpu_name ?? null,
        npu: npus[0] ?? null,
        runtimes,
        // The versions that decide whether chat works: GAIA, its model server, and what runs chat on it.
        gaia: gaiaVersion(sys?.gaia_build, sys?.gaia_version),
        build: sys?.gaia_build?.kind ?? null,
        software: [
            gaiaVersion(sys?.gaia_build, sys?.gaia_version) ? `GAIA ${gaiaVersion(sys?.gaia_build, sys?.gaia_version)}` : null,
            sys?.lemonade_version ? `Lemonade ${sys.lemonade_version}` : null,
            ...runtimes.filter(({ b }) => CHAT_RUNTIMES.has(b.recipe) && installed(b))
                .map(({ name, b }) => `${name} ${b.version ?? b.backend}`),
        ].filter((x): x is string => !!x),
        totalMemory: totalMemory(sys?.physical_memory, hardware?.ram_gb),
        freeGb: plan.disk?.free_gb ?? hardware?.disk_free_gb ?? null,
        modelsPath: plan.disk?.path ?? null,
        gaiaHome: sys?.gaia_home ?? null,
    };
}

// Lemonade updates an outdated backend itself on first use, and keeps reporting it as outdated after.
export const STATE_WORDS: Record<string, string> = { installed: 'installed', update_required: 'installed, updates on first use' };
export const installed = (b: Backend) => b.state === 'installed' || b.state === 'update_required';

export const JOB_KEYS: Record<SetupRole, MessageKey> = {
    chat: 'setup.job.chat',
    vision: 'setup.job.vision',
    embedding: 'setup.job.documents',
    speech: 'setup.job.speech',
    voice: 'setup.job.voice',
    image_creation: 'setup.job.images',
};

/** The summary as plain text, in English, for pasting into a bug report or a chat. */
export function specText(plan: SetupPlan, hardware: PreflightReport | null): string {
    const f = facts(plan, hardware);
    const server = plan.lemonade.mode === 'external' ? "the user's own" : "GAIA's own";
    return [
        `GAIA: ${f.gaia ?? 'unknown'}${f.build ? `, ${BUILD_WORDS[f.build]}` : ''} (agent ${f.sys?.agent_version ?? 'not installed yet'})`,
        `Model server: ${server} Lemonade ${f.sys?.lemonade_version ?? '(version not reported)'}${plan.lemonade.url ? ` at ${plan.lemonade.url}` : ''}`,
        ...f.runtimes.map(({ name, b }) => `${name}: ${b.backend}${b.version ? ` ${b.version}` : ''} (${STATE_WORDS[b.state ?? ''] ?? 'not installed'})`),
        `OS: ${f.sys?.os ?? hardware?.os ?? 'unknown'}`,
        `Processor: ${f.cpu?.name ?? f.sys?.processor ?? 'unknown'}${f.cpu?.cores ? ` (${f.cpu.cores} cores, ${f.cpu.threads ?? f.cpu.cores} threads)` : ''}`,
        `Graphics: ${f.graphics ?? 'none found'}`,
        `NPU: ${f.npus.map(deviceText).join('; ') || 'none found'}`,
        `Memory: ${f.totalMemory ?? 'unknown'}${plan.capacity_gb != null ? `, ${formatSize(plan.capacity_gb)} for models${plan.memory_source ? ` (${plan.memory_source})` : ''}` : ''}`,
        `Models folder: ${f.modelsPath ?? 'unknown'}${f.freeGb != null ? ` (${formatSize(f.freeGb)} free)` : ''}`,
        `GAIA data and logs: ${f.gaiaHome ?? 'unknown'}`,
    ].join('\n');
}
