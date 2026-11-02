// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { AlertCircle, Check, ChevronRight, Copy } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { useT } from '../../i18n/useT';
import type { PreflightReport, SetupDevice, SetupPlan } from '../../types';
import { formatSize } from './format';

/** A technical value: names, versions and paths read the same in every language. */
const Tech = ({ children }: { children: ReactNode }) => <bdi dir="ltr">{children}</bdi>;

function Row({ label, children }: { label: string; children: ReactNode }) {
    return (
        <div className="found-row">
            <dt>{label}</dt>
            <dd>{children}</dd>
        </div>
    );
}

function deviceText(d: SetupDevice): string {
    return d.family ? `${d.name} (${d.family})` : d.name;
}

/** The runtimes GAIA uses, by the names people know them by. The first two run chat. */
const RUNTIMES: Array<[recipe: string, name: string]> = [
    ['llamacpp', 'llama.cpp'],
    ['flm', 'FastFlowLM'],
    ['whispercpp', 'whisper.cpp'],
    ['kokoro', 'Kokoro'],
    ['sd-cpp', 'stable-diffusion.cpp'],
];
const CHAT_RUNTIMES = new Set(['llamacpp', 'flm']);

type Backend = NonNullable<SetupPlan['backends']>[number];

function runtimesOf(plan: SetupPlan): Array<{ name: string; b: Backend }> {
    const out: Array<{ name: string; b: Backend }> = [];
    for (const [recipe, name] of RUNTIMES) {
        const b = (plan.backends ?? []).find((x) => x.recipe === recipe && x.default);
        if (b && b.state !== 'unsupported') out.push({ name, b });
    }
    return out;
}

/** This PC's memory in the same form as every other size here; Lemonade writes "128.00 GB". */
function totalMemory(reported: string | null | undefined, ramGb: number | null | undefined): string | null {
    const gb = reported ? Number.parseFloat(reported) : Number.NaN;
    if (Number.isFinite(gb) && reported?.trim().toUpperCase().endsWith('GB')) return formatSize(gb);
    if (reported) return reported;
    return ramGb != null ? formatSize(ramGb) : null;
}

/** The facts the summary is built from, read once for the screen and the copy. */
function facts(plan: SetupPlan, hardware: PreflightReport | null) {
    const sys = plan.system;
    const devices = (pick: (k: string) => boolean) => (sys?.devices ?? []).filter((d) => d.available && pick(d.kind));
    const gpus = devices((k) => k.endsWith('gpu'));
    const npus = devices((k) => k.endsWith('npu'));
    const runtimes = runtimesOf(plan);
    return {
        sys,
        cpu: devices((k) => k === 'cpu')[0],
        gpus,
        npus,
        runsOn: gpus[0]?.name ?? hardware?.gpu_name ?? null,
        npu: npus[0] ?? null,
        runtimes,
        // The versions that decide whether chat works: GAIA, its model server, and what runs chat on it.
        software: [
            sys?.gaia_version ? `GAIA ${sys.gaia_version}` : null,
            sys?.lemonade_version ? `Lemonade ${sys.lemonade_version}` : null,
            ...runtimes.filter(({ b }) => CHAT_RUNTIMES.has(b.recipe) && b.state === 'installed')
                .map(({ name, b }) => `${name} ${b.version ?? b.backend}`),
        ].filter((x): x is string => !!x),
        totalMemory: totalMemory(sys?.physical_memory, hardware?.ram_gb),
        freeGb: plan.disk?.free_gb ?? hardware?.disk_free_gb ?? null,
        modelsPath: plan.disk?.path ?? null,
        gaiaHome: sys?.gaia_home ?? null,
    };
}

const STATE_WORDS: Record<string, string> = { installed: 'installed', update_required: 'update needed' };

/** The summary as plain text, in English, for pasting into a bug report or a chat. */
export function specText(plan: SetupPlan, hardware: PreflightReport | null): string {
    const f = facts(plan, hardware);
    const server = plan.lemonade.mode === 'external' ? "the user's own" : "GAIA's own";
    return [
        `GAIA: ${f.sys?.gaia_version ?? 'unknown'} (agent ${f.sys?.agent_version ?? 'not installed yet'})`,
        `Model server: ${server} Lemonade ${f.sys?.lemonade_version ?? '(version not reported)'}${plan.lemonade.url ? ` at ${plan.lemonade.url}` : ''}`,
        ...f.runtimes.map(({ name, b }) => `${name}: ${b.backend}${b.version ? ` ${b.version}` : ''} (${STATE_WORDS[b.state ?? ''] ?? 'not installed'})`),
        `OS: ${f.sys?.os ?? hardware?.os ?? 'unknown'}`,
        `Processor: ${f.cpu?.name ?? f.sys?.processor ?? 'unknown'}${f.cpu?.cores ? ` (${f.cpu.cores} cores, ${f.cpu.threads ?? f.cpu.cores} threads)` : ''}`,
        `Graphics: ${f.gpus.map(deviceText).join('; ') || hardware?.gpu_name || 'none found'}`,
        `NPU: ${f.npus.map(deviceText).join('; ') || 'none found'}`,
        `Memory: ${f.totalMemory ?? 'unknown'}${plan.capacity_gb != null ? `, ${formatSize(plan.capacity_gb)} for models${plan.memory_source ? ` (${plan.memory_source})` : ''}` : ''}`,
        `Models folder: ${f.modelsPath ?? 'unknown'}${f.freeGb != null ? ` (${formatSize(f.freeGb)} free)` : ''}`,
        `GAIA data and logs: ${f.gaiaHome ?? 'unknown'}`,
    ].join('\n');
}

/**
 * What setup found, so the user can check it read this PC right before anything
 * downloads: the few facts that decide the setup up front, where things are
 * kept, and the rest one click away.
 */
export function DetectedSummary({ plan, hardware }: { plan: SetupPlan; hardware: PreflightReport | null }) {
    const t = useT();
    const f = facts(plan, hardware);
    const [open, setOpen] = useState(false);
    const [copied, setCopied] = useState<'done' | 'failed' | null>(null);
    const unknown = <span className="found-muted">{t('setup.found.unknown')}</span>;
    const warnings = [...(hardware?.blockers ?? []), ...(hardware?.warnings ?? [])];

    const copy = () => {
        navigator.clipboard.writeText(specText(plan, hardware)).then(
            () => setCopied('done'),
            (err) => {
                console.warn('[DetectedSummary] could not copy the details', err);
                setCopied('failed');
            },
        );
        setTimeout(() => setCopied(null), 2000);
    };

    return (
        <section className="setup-card found" aria-label={t('setup.found.title')}>
            <div className="found-head">
                <h3 className="mc-job-title">{t('setup.found.title')}</h3>
                <button type="button" className="found-copy" onClick={copy} aria-label={t('setup.found.copy')} title={t('setup.found.copy')}>
                    {copied === 'done' ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
                </button>
                {copied && <span className="found-copied" role="status">{t(copied === 'done' ? 'setup.found.copied' : 'setup.found.copyFailed')}</span>}
            </div>

            <dl className="found-glance">
                <Row label={t('setup.found.runsOn')}>
                    {f.runsOn ? <Tech>{f.runsOn}</Tech> : unknown}
                    {f.npu && <span className="found-chip"><Tech>NPU</Tech></span>}
                </Row>
                <Row label={t('setup.found.memory')}>
                    {plan.capacity_gb != null && f.totalMemory
                        ? t('setup.found.memoryOf', { size: formatSize(plan.capacity_gb), total: f.totalMemory })
                        : plan.capacity_gb != null ? <Tech>{formatSize(plan.capacity_gb)}</Tech>
                            : f.totalMemory ? <Tech>{f.totalMemory}</Tech> : unknown}
                </Row>
                <Row label={t('setup.found.space')}>
                    {f.freeGb != null ? <Tech>{formatSize(f.freeGb)}</Tech> : unknown}
                </Row>
                <Row label={t('setup.found.software')}>
                    {f.software.length ? <Tech>{f.software.join(' · ')}</Tech> : unknown}
                </Row>
            </dl>

            <dl className="found-paths">
                <Row label={t('setup.found.modelsFolder')}>{f.modelsPath ? <Tech>{f.modelsPath}</Tech> : unknown}</Row>
                <Row label={t('setup.found.gaiaFolder')}>{f.gaiaHome ? <Tech>{f.gaiaHome}</Tech> : unknown}</Row>
            </dl>

            {warnings.length > 0 && (
                <ul className="found-warnings">
                    {warnings.map((w) => <li key={w}><AlertCircle size={13} aria-hidden="true" /> <Tech>{w}</Tech></li>)}
                </ul>
            )}

            <button type="button" className="setup-log-toggle found-more" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
                <ChevronRight size={13} className={open ? 'is-open' : ''} aria-hidden="true" /> {t('setup.found.details')}
            </button>
            {open && (
                <dl className="found-details">
                    <Row label={t('setup.found.server')}>
                        <span className="found-line">{plan.lemonade.mode === 'external' ? t('setup.found.serverYours') : t('setup.found.serverOwn')}</span>
                        {plan.lemonade.url && <span className="found-line"><Tech>{plan.lemonade.url}</Tech></span>}
                    </Row>
                    <Row label={t('setup.found.runtime')}>
                        {f.runtimes.length
                            ? f.runtimes.map(({ name, b }) => (
                                <span key={b.recipe} className="found-line">
                                    <Tech>{name} {b.backend}{b.version ? ` ${b.version}` : ''}</Tech>
                                    {' · '}
                                    {t(b.state === 'installed' ? 'setup.pc.backend.installed' : b.state === 'update_required' ? 'setup.found.update' : 'setup.pc.backend.available')}
                                </span>
                            ))
                            : unknown}
                    </Row>
                    <Row label={t('setup.found.agent')}>
                        {f.sys?.agent_version ? <Tech>{f.sys.agent_version}</Tech> : <span className="found-muted">{t('setup.found.agentLater')}</span>}
                    </Row>
                    <Row label={t('setup.found.os')}>{f.sys?.os ?? hardware?.os ? <Tech>{f.sys?.os ?? hardware?.os}</Tech> : unknown}</Row>
                    <Row label={t('setup.found.cpu')}>
                        {f.cpu || f.sys?.processor ? (
                            <>
                                <span className="found-line"><Tech>{f.cpu?.name ?? f.sys?.processor}</Tech></span>
                                {f.cpu?.cores ? <span className="found-line found-muted">{t('setup.found.cores', { cores: String(f.cpu.cores), threads: String(f.cpu.threads ?? f.cpu.cores) })}</span> : null}
                            </>
                        ) : unknown}
                    </Row>
                    <Row label={t('setup.found.gpu')}>
                        {f.gpus.length ? f.gpus.map((d) => <span key={d.name} className="found-line"><Tech>{deviceText(d)}</Tech></span>)
                            : <span className="found-muted">{t('setup.found.none')}</span>}
                    </Row>
                    <Row label={t('setup.found.npu')}>
                        {f.npus.length ? f.npus.map((d) => <span key={d.name} className="found-line"><Tech>{deviceText(d)}</Tech></span>)
                            : <span className="found-muted">{t('setup.found.none')}</span>}
                    </Row>
                </dl>
            )}
        </section>
    );
}
