// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Check, ChevronDown, Loader2 } from 'lucide-react';
import { useT } from '../../i18n/useT';
import { isDownloading } from '../modelMeta';
import { formatPrice } from './cloudPrice';
import type { ProviderModel } from '../../types';

interface Props {
    model: ProviderModel;
    label: string;
    current: boolean;
    remote: boolean;
    /** What the page is doing: a model id, or `dl:<id>` / `rm:<id>`. */
    busy: string | null;
    confirmingRemove: boolean;
    onUse: () => void;
    onDownload: () => void;
    onAskRemove: () => void;
    onKeep: () => void;
    onRemove: () => void;
}

/** One model: its name and chips, with the long facts one click away. */
export function ModelRow({ model: m, label, current, remote, busy, confirmingRemove, onUse, onDownload, onAskRemove, onKeep, onRemove }: Props) {
    const t = useT();
    const progress = isDownloading(m) ? m.download_progress : null;
    const placement = m.placement?.label;
    const facts = t.compose((say) => [
        placement && (m.placement?.source === 'expected' ? say('settings.model.expected', { label: placement }) : placement),
        m.size_gb ? say(m.downloaded ? 'settings.model.sizeOnDisk' : 'settings.model.sizeDownload', { size: `${m.size_gb} GB` }) : null,
        m.tokens_per_second ? say('settings.model.speed', { speed: `${Math.round(m.tokens_per_second)} tok/s` }) : null,
        !m.placement && m.context_length ? say('settings.model.contextTokens', { size: m.context_length.toLocaleString() }) : null,
    ].filter(Boolean).join(' · '));
    const hasDetails = !!(m.display_name || facts || m.evidence || (m.custom && m.source_url));

    return (
        <li className={`model-row${current ? ' is-current' : ''}`}>
            <div className="model-row-text">
                <bdi className="model-row-name" title={m.id}>{label}</bdi>
                <div className="model-chips">
                    {m.placement?.device && <span className="model-badge is-device">{m.placement.device.toUpperCase()}</span>}
                    {m.rank && <span className="model-badge is-rank"><bdi dir="ltr">#{m.rank}</bdi> {m.note}</span>}
                    {m.state === 'loaded' && <span className="model-badge is-loaded">{t('settings.model.loaded')}</span>}
                    {m.fits === true && m.available !== false && <span className="model-badge is-fit">{t('settings.model.fits')}</span>}
                    {m.custom && <span className="model-badge is-custom">{t('settings.model.custom')}</span>}
                    {!!m.size_gb && <bdi dir="ltr" className="model-badge is-size">{m.size_gb} GB</bdi>}
                    {hasDetails && (
                        <details className="settings-more">
                            <summary><ChevronDown size={13} className="settings-disclosure-chevron" aria-hidden="true" /> {t('settings.model.details')}</summary>
                            {m.display_name && <span className="model-row-meta"><bdi>{m.id}</bdi></span>}
                            {facts && <span className="model-row-meta">{facts}</span>}
                            {m.evidence && <span className="model-row-meta">{t('settings.model.measured', { evidence: m.evidence })}</span>}
                            {m.custom && m.source_url && (
                                <span className="model-row-meta">{t('settings.model.from', { url: m.source_url })}</span>
                            )}
                        </details>
                    )}
                </div>
                {remote && (
                    <span className={`model-row-meta${m.price ? '' : ' is-unknown-price'}`}><bdi>{formatPrice(m.price)}</bdi></span>
                )}
                {m.placement?.warning && <span className="model-row-meta is-warning" role="alert"><bdi>{m.placement.warning}</bdi></span>}
                {m.unavailable_reason && <span className="model-row-meta">{t('settings.model.cantRun', { reason: m.unavailable_reason })}</span>}
                {progress && (
                    <progress max={100} value={progress.percent} aria-label={t('settings.model.downloadingAria', { name: label })} />
                )}
            </div>
            <div className="model-row-actions">
                {m.custom && !current && !progress && (
                    confirmingRemove ? (
                        <>
                            <button type="button" className="btn-danger" onClick={onRemove} disabled={busy !== null}>
                                {busy === `rm:${m.id}` ? <Loader2 size={13} className="spin" /> : null}
                                {m.downloaded ? t('settings.model.removeDownload') : t('settings.models.remove')}
                            </button>
                            <button type="button" className="btn-secondary" onClick={onKeep} disabled={busy !== null}>
                                {t('settings.models.keep')}
                            </button>
                        </>
                    ) : (
                        <button
                            type="button"
                            className="btn-secondary"
                            onClick={onAskRemove}
                            disabled={busy !== null}
                            aria-label={t('settings.models.removeAria', { name: label })}
                        >
                            {t('settings.models.remove')}
                        </button>
                    )
                )}
                {current ? (
                    <span className="model-row-current"><Check size={13} aria-hidden="true" /> {t('settings.model.inUse')}</span>
                ) : m.available === false ? null : progress ? (
                    <span className="model-row-current"><Loader2 size={13} className="spin" /> <bdi dir="ltr">{Math.round(progress.percent)}%</bdi></span>
                ) : !m.downloaded ? (
                    <button type="button" className="btn-secondary" onClick={onDownload} disabled={busy !== null} aria-label={t('settings.model.downloadAria', { name: label })}>
                        {busy === `dl:${m.id}` ? <Loader2 size={13} className="spin" /> : null} {t('settings.model.download')}
                    </button>
                ) : (
                    <button type="button" className="btn-secondary" onClick={onUse} disabled={busy !== null} aria-label={t('settings.model.useAria', { name: label })}>
                        {busy === m.id ? <Loader2 size={13} className="spin" /> : null} {t('settings.model.use')}
                    </button>
                )}
            </div>
        </li>
    );
}
