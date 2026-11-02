// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { AlertCircle, Check, Copy } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { useT } from '../../i18n/useT';
import type { PreflightReport, SetupDevice, SetupPlan } from '../../types';
import { formatSize } from './format';

function Row({ label, children }: { label: string; children: ReactNode }) {
    return (
        <div className="found-row">
            <dt>{label}</dt>
            <dd>{children}</dd>
        </div>
    );
}

/** A technical value: names, versions and paths read the same in every language. */
const Tech = ({ children }: { children: ReactNode }) => <bdi dir="ltr">{children}</bdi>;

function deviceText(d: SetupDevice): string {
    return d.family ? `${d.name} (${d.family})` : d.name;
}

/** The runtimes GAIA uses, by the names people know them by, in the order shown. */
const RUNTIMES: Array<[recipe: string, name: string]> = [
    ['llamacpp', 'llama.cpp'],
    ['flm', 'FastFlowLM'],
    ['whispercpp', 'whisper.cpp'],
    ['kokoro', 'Kokoro'],
    ['sd-cpp', 'stable-diffusion.cpp'],
];

type Backend = NonNullable<SetupPlan['backends']>[number];

function runtimesOf(plan: SetupPlan): Array<{ name: string; b: Backend }> {
    const out: Array<{ name: string; b: Backend }> = [];
    for (const [recipe, name] of RUNTIMES) {
        const b = (plan.backends ?? []).find((x) => x.recipe === recipe && x.default);
        if (b && b.state !== 'unsupported') out.push({ name, b });
    }
    return out;
}

const STATE_WORDS: Record<string, string> = { installed: 'installed', update_required: 'update needed' };

/** The summary as plain text, in English, for pasting into a bug report or a chat. */
export function specText(plan: SetupPlan, hardware: PreflightReport | null): string {
    const sys = plan.system;
    const devices = (pick: (k: string) => boolean) => (sys?.devices ?? []).filter((d) => d.available && pick(d.kind));
    const cpu = devices((k) => k === 'cpu')[0];
    const server = plan.lemonade.mode === 'external' ? "the user's own" : "GAIA's own";
    const lines = [
        `GAIA: ${sys?.gaia_version ?? 'unknown'}`,
        `GAIA agent: ${sys?.agent_version ?? 'not installed yet'}`,
        `Model server: ${server} Lemonade ${sys?.lemonade_version ?? '(version not reported)'}${plan.lemonade.url ? ` at ${plan.lemonade.url}` : ''}`,
        ...runtimesOf(plan).map(({ name, b }) => `${name}: ${b.backend}${b.version ? ` ${b.version}` : ''} (${STATE_WORDS[b.state ?? ''] ?? 'not installed'})`),
        `OS: ${sys?.os ?? hardware?.os ?? 'unknown'}`,
        `Processor: ${cpu?.name ?? sys?.processor ?? 'unknown'}${cpu?.cores ? ` (${cpu.cores} cores, ${cpu.threads ?? cpu.cores} threads)` : ''}`,
        `Graphics: ${devices((k) => k.endsWith('gpu')).map(deviceText).join('; ') || hardware?.gpu_name || 'none found'}`,
        `NPU: ${devices((k) => k.endsWith('npu')).map(deviceText).join('; ') || 'none found'}`,
        `Memory: ${sys?.physical_memory ?? (hardware?.ram_gb != null ? `${hardware.ram_gb} GB` : 'unknown')}${plan.capacity_gb != null ? `, ${formatSize(plan.capacity_gb)} for models${plan.memory_source ? ` (${plan.memory_source})` : ''}` : ''}`,
        `Disk for models: ${plan.disk?.free_gb != null ? `${formatSize(plan.disk.free_gb)} free` : 'unknown'}${plan.disk?.path ? ` at ${plan.disk.path}` : ''}`,
    ];
    return lines.join('\n');
}

/**
 * Everything setup found, across the stack, so the user can check it got this
 * PC right before anything downloads.
 */
export function DetectedSummary({ plan, hardware }: { plan: SetupPlan; hardware: PreflightReport | null }) {
    const t = useT();
    const sys = plan.system;
    const unknown = <span className="found-muted">{t('setup.found.unknown')}</span>;
    const devices = (kind: (k: string) => boolean) => (sys?.devices ?? []).filter((d) => d.available && kind(d.kind));
    const cpu = devices((k) => k === 'cpu')[0];
    const gpus = devices((k) => k.endsWith('gpu'));
    const npus = devices((k) => k.endsWith('npu'));
    const runtimes = runtimesOf(plan);
    const [copied, setCopied] = useState<'done' | 'failed' | null>(null);
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
    const freeGb = plan.disk?.free_gb ?? hardware?.disk_free_gb ?? null;
    const warnings = [...(hardware?.blockers ?? []), ...(hardware?.warnings ?? [])];

    return (
        <section className="setup-card found" aria-label={t('setup.found.title')}>
            <div className="found-head">
                <h3 className="mc-job-title">{t('setup.found.title')}</h3>
                <button type="button" className="found-copy" onClick={copy} aria-label={t('setup.found.copy')} title={t('setup.found.copy')}>
                    {copied === 'done' ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
                </button>
                {copied && <span className="found-copied" role="status">{t(copied === 'done' ? 'setup.found.copied' : 'setup.found.copyFailed')}</span>}
            </div>
            <div className="found-groups">
                <div className="found-group">
                    <p className="found-heading"><bdi>{t('setup.found.gaia')}</bdi></p>
                    <dl className="found-list">
                    <Row label={t('setup.found.gaiaVersion')}><Tech>GAIA {sys?.gaia_version ?? '—'}</Tech></Row>
                    <Row label={t('setup.found.agent')}>
                        {sys?.agent_version ? <Tech>{sys.agent_version}</Tech> : <span className="found-muted">{t('setup.found.agentLater')}</span>}
                    </Row>
                </dl>
                </div>
                <div className="found-group">
                    <p className="found-heading">{t('setup.found.server')}</p>
                    <dl className="found-list">
                    <Row label={t('setup.found.serverWhich')}>
                        {plan.lemonade.mode === 'external' ? t('setup.found.serverYours') : t('setup.found.serverOwn')}
                    </Row>
                    <Row label={t('setup.found.version')}>{sys?.lemonade_version ? <Tech>Lemonade {sys.lemonade_version}</Tech> : unknown}</Row>
                    <Row label={t('setup.found.address')}>{plan.lemonade.url ? <Tech>{plan.lemonade.url}</Tech> : unknown}</Row>
                    <Row label={t('setup.found.runtime')}>
                        {runtimes.length
                            ? runtimes.map(({ name, b }) => (
                                <span key={b.recipe} className="found-line">
                                    <Tech>{name} {b.backend}{b.version ? ` ${b.version}` : ''}</Tech>
                                    {' · '}
                                    {t(b.state === 'installed' ? 'setup.pc.backend.installed' : b.state === 'update_required' ? 'setup.found.update' : 'setup.pc.backend.available')}
                                </span>
                            ))
                            : unknown}
                    </Row>
                </dl>
                </div>
                <div className="found-group">
                    <p className="found-heading">{t('setup.found.pc')}</p>
                    <dl className="found-list">
                    <Row label={t('setup.found.os')}>{sys?.os ?? hardware?.os ? <Tech>{sys?.os ?? hardware?.os}</Tech> : unknown}</Row>
                    <Row label={t('setup.found.cpu')}>
                        {cpu || sys?.processor ? (
                            <span className="found-line">
                                <Tech>{cpu?.name ?? sys?.processor}</Tech>
                                {cpu?.cores ? <> · {t('setup.found.cores', { cores: String(cpu.cores), threads: String(cpu.threads ?? cpu.cores) })}</> : null}
                            </span>
                        ) : unknown}
                    </Row>
                    <Row label={t('setup.found.gpu')}>
                        {gpus.length ? gpus.map((d) => <span key={d.name} className="found-line"><Tech>{deviceText(d)}</Tech></span>)
                            : hardware?.gpu_name ? <Tech>{hardware.gpu_name}</Tech> : <span className="found-muted">{t('setup.found.none')}</span>}
                    </Row>
                    <Row label={t('setup.found.npu')}>
                        {npus.length ? npus.map((d) => <span key={d.name} className="found-line"><Tech>{deviceText(d)}</Tech></span>)
                            : <span className="found-muted">{t('setup.found.none')}</span>}
                    </Row>
                    <Row label={t('setup.found.memory')}>
                        <span className="found-line">
                            {sys?.physical_memory ? <Tech>{sys.physical_memory}</Tech> : hardware?.ram_gb != null ? <Tech>{`${hardware.ram_gb} GB`}</Tech> : unknown}
                            {plan.capacity_gb != null && <> · {t('setup.found.forModels', { size: formatSize(plan.capacity_gb) })}</>}
                        </span>
                    </Row>
                    <Row label={t('setup.found.disk')}>
                        <span className="found-line">
                            {freeGb != null ? t('setup.found.diskFree', { size: formatSize(freeGb) }) : unknown}
                            {plan.disk?.path && <> · <Tech>{plan.disk.path}</Tech></>}
                        </span>
                    </Row>
                </dl>
                </div>
            </div>
            {warnings.length > 0 && (
                <ul className="found-warnings">
                    {warnings.map((w) => <li key={w}><AlertCircle size={13} aria-hidden="true" /> <Tech>{w}</Tech></li>)}
                </ul>
            )}
        </section>
    );
}
