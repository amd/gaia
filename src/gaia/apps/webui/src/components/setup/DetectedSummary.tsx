// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { AlertCircle, Check, ChevronRight, Copy, FolderOpen } from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useT } from '../../i18n/useT';
import * as api from '../../services/api';
import type { PreflightReport, SetupPlan } from '../../types';
import { deviceText, facts, installed, JOB_KEYS, specText } from './detected';
import { formatSize } from './format';

export { specText };

/** A technical value: names, versions and paths read the same in every language. */
const Tech = ({ children }: { children: ReactNode }) => <bdi dir="ltr">{children}</bdi>;

function Row({ label, children }: { label: ReactNode; children: ReactNode }) {
    return (
        <div className="found-row">
            <dt>{label}</dt>
            <dd>{children}</dd>
        </div>
    );
}

/**
 * What setup found, so the user can check it read this PC right before anything
 * downloads: the few facts that decide the setup up front, where things are
 * kept, and the rest one click away.
 */
export function DetectedSummary({ plan, hardware, onChangeMemory }: { plan: SetupPlan; hardware: PreflightReport | null; onChangeMemory?: () => void }) {
    const t = useT();
    const f = facts(plan, hardware);
    const [open, setOpen] = useState(false);
    const [copied, setCopied] = useState<'done' | 'failed' | null>(null);
    const [openError, setOpenError] = useState<string | null>(null);
    // The folder itself, in the PC's file browser; the full path is in the link's tooltip and the copy.
    const openFolder = async (path: string) => {
        setOpenError(null);
        try {
            await api.openFileOrFolder(path, false);
        } catch (err) {
            setOpenError(t('setup.found.openFailed', { error: err instanceof Error ? err.message : String(err) }));
        }
    };
    const unknown = <span className="found-muted">{t('setup.found.unknown')}</span>;
    // Before a model server runs, the NPU can't be asked about yet, so that warning would be noise.
    const warnings = [...(hardware?.blockers ?? []), ...(hardware?.warnings ?? []).filter((w) => hardware?.lemonade_running || !/\bNPU\b/.test(w))];

    // The note shows for 2 s from when the copy finished; a new click starts it again.
    const fade = useRef<ReturnType<typeof setTimeout> | null>(null);
    useEffect(() => () => { if (fade.current) clearTimeout(fade.current); }, []);
    const settle = (outcome: 'done' | 'failed') => {
        setCopied(outcome);
        if (fade.current) clearTimeout(fade.current);
        fade.current = setTimeout(() => setCopied(null), 2000);
    };
    const copy = () => {
        navigator.clipboard.writeText(specText(plan, hardware)).then(
            () => settle('done'),
            (err) => {
                console.warn('[DetectedSummary] could not copy the details', err);
                settle('failed');
            },
        );
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
                    {onChangeMemory && <button type="button" className="link-btn found-link" onClick={onChangeMemory}>{t('setup.found.changeMemory')}</button>}
                </Row>
                <Row label={t('setup.found.space')}>
                    {f.freeGb != null ? <Tech>{formatSize(f.freeGb)}</Tech> : unknown}
                </Row>
                <Row label={t('setup.found.software')}>
                    {f.software.length ? <Tech>{f.software.join(' · ')}</Tech> : unknown}
                    {f.build && f.build !== 'release' && <span className="found-chip">{t(`setup.found.build.${f.build}`)}</span>}
                </Row>
            </dl>

            <div className="found-paths">
                {([['setup.found.modelsFolder', f.modelsPath], ['setup.found.gaiaFolder', f.gaiaHome]] as const).map(([key, path]) => (
                    path && (
                        <button key={key} type="button" className="link-btn found-open" onClick={() => void openFolder(path)} title={path}>
                            <FolderOpen size={13} aria-hidden="true" /> {t(key)}
                        </button>
                    )
                ))}
                {openError && <p className="setup-error" role="alert">{openError}</p>}
            </div>

            {/* The backend's own words, in English, wait behind a translated heading. */}
            {warnings.length > 0 && (
                <details className="dl-details found-warnings">
                    <summary><AlertCircle size={13} aria-hidden="true" /> {t('setup.found.noticed')}</summary>
                    <ul>
                        {warnings.map((w) => <li key={w}><Tech>{w}</Tech></li>)}
                    </ul>
                </details>
            )}

            <button type="button" className="setup-log-toggle found-more" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
                <ChevronRight size={13} className={open ? 'is-open' : ''} aria-hidden="true" /> {t('setup.found.details')}
            </button>
            {open && (
                <div className="found-details">
                    <h4 className="found-group">{t('setup.found.software')}</h4>
                    <dl className="found-list">
                        <Row label={<Tech>GAIA</Tech>}>
                            {f.gaia ? <Tech>{f.gaia}</Tech> : unknown}
                            {f.build && f.build !== 'release' && <span className="found-chip">{t(`setup.found.build.${f.build}`)}</span>}
                        </Row>
                        <Row label={t('setup.found.agent')}>
                            {f.sys?.agent_version ? <Tech>{f.sys.agent_version}</Tech> : <span className="found-muted">{t('setup.found.agentLater')}</span>}
                        </Row>
                        <Row label={t('setup.found.server')}>
                            {plan.lemonade.mode === 'external' ? t('setup.found.serverYours') : t('setup.found.serverOwn')}
                            {f.sys?.lemonade_version && <> <Tech>{f.sys.lemonade_version}</Tech></>}
                            {plan.lemonade.url && <span className="found-sub"><Tech>{plan.lemonade.url}</Tech></span>}
                        </Row>
                    </dl>

                    <h4 className="found-group">{t('setup.found.runtime')}</h4>
                    {f.runtimes.length ? (
                        <div className="found-table-wrap"><table className="found-table">
                            <thead className="visually-hidden">
                                <tr>
                                    <th scope="col">{t('setup.found.col.runtime')}</th>
                                    <th scope="col">{t('setup.found.col.device')}</th>
                                    <th scope="col">{t('setup.found.col.version')}</th>
                                    <th scope="col">{t('setup.found.col.state')}</th>
                                </tr>
                            </thead>
                            <tbody>
                                {f.runtimes.map(({ name, b }) => (
                                    <tr key={b.recipe}>
                                        <th scope="row"><Tech>{name}</Tech></th>
                                        <td><Tech>{b.backend}</Tech></td>
                                        <td><Tech>{b.version ?? ''}</Tech></td>
                                        <td className={installed(b) ? 'is-installed' : undefined}>
                                            {t(installed(b) ? 'setup.pc.backend.installed' : 'setup.pc.backend.available')}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table></div>
                    ) : <p className="found-muted">{t('setup.found.unknown')}</p>}

                    <h4 className="found-group">{t('setup.found.models')}</h4>
                    {(plan.catalog ?? []).length ? (
                        <div className="found-table-wrap"><table className="found-table">
                            <thead className="visually-hidden">
                                <tr>
                                    <th scope="col">{t('setup.found.col.model')}</th>
                                    <th scope="col">{t('setup.found.col.job')}</th>
                                    <th scope="col">{t('setup.found.col.tested')}</th>
                                    <th scope="col">{t('setup.found.col.size')}</th>
                                    <th scope="col">{t('setup.found.col.state')}</th>
                                </tr>
                            </thead>
                            <tbody>
                                {(plan.catalog ?? []).map((e) => (
                                    <tr key={e.id}>
                                        <th scope="row" title={e.id}><bdi>{e.name}</bdi></th>
                                        <td>{e.roles.map((r) => t(JOB_KEYS[r])).join(' · ')}</td>
                                        {/* The same label Customize gives it; an optional extra has none. */}
                                        <td>
                                            {e.support === 'verified' && <span className="mc-chip is-verified">{t('setup.label.verified')}</span>}
                                            {e.support === 'untested' && <span className="mc-chip is-untested">{t('setup.label.untested')}</span>}
                                        </td>
                                        <td><Tech>{e.full_size_gb ?? e.size_gb ? formatSize((e.full_size_gb ?? e.size_gb) as number) : ''}</Tech></td>
                                        <td className={e.downloaded ? 'is-installed' : undefined}>
                                            {t(e.downloaded ? 'setup.models.status.onPc' : 'setup.found.notDownloaded')}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table></div>
                    ) : <p className="found-muted">{t('setup.found.unknown')}</p>}

                    <h4 className="found-group">{t('setup.found.hardware')}</h4>
                    <dl className="found-list">
                        <Row label={t('setup.found.os')}>{f.sys?.os ?? hardware?.os ? <Tech>{f.sys?.os ?? hardware?.os}</Tech> : unknown}</Row>
                        <Row label={t('setup.found.cpu')}>
                            {f.cpu || f.sys?.processor ? <Tech>{f.cpu?.name ?? f.sys?.processor}</Tech> : unknown}
                            {f.cpu?.cores ? <span className="found-sub">{t('setup.found.cores', { cores: String(f.cpu.cores), threads: String(f.cpu.threads ?? f.cpu.cores) })}</span> : null}
                        </Row>
                        <Row label={t('setup.found.gpu')}>
                            {f.graphics ? <Tech>{f.graphics}</Tech> : <span className="found-muted">{t('setup.found.none')}</span>}
                        </Row>
                        <Row label={t('setup.found.npu')}>
                            {f.npus.length ? <Tech>{f.npus.map(deviceText).join('; ')}</Tech> : <span className="found-muted">{t('setup.found.none')}</span>}
                        </Row>
                    </dl>
                </div>
            )}
        </section>
    );
}
