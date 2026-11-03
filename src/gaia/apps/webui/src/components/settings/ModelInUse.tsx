// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useT } from '../../i18n/useT';
import { useModelStore, displayModelName, sameModelId } from '../../stores/modelStore';
import { ctxLabel } from '../modelMeta';
import { ModelContextSize } from './ModelContextSize';
import type { ProviderModel } from '../../types';

interface Props {
    /** The in-use model's row, once a list held it; carries its device and size. */
    row: ProviderModel | null;
    providerName: string | null;
    /** Models the Switch model dropdown offers: ready to use, on the provider being viewed. */
    choices: ProviderModel[];
    labels: Record<string, string>;
    disabled: boolean;
    onSwitch: (model: string) => void;
}

/** The answer to "what am I using?": the model, what it runs on, and its context size. */
export function ModelInUse({ row, providerName, choices, labels, disabled, onSwitch }: Props) {
    const t = useT();
    const active = useModelStore((s) => s.active);
    const model = active?.model ?? null;
    const remote = !!active?.remote;
    const offered = choices.some((m) => sameModelId(model, m.id));
    const downloaded = active?.downloaded ?? row?.downloaded;
    const state = remote ? null
        : row?.state === 'loaded' ? t('settings.model.loaded')
        : downloaded === true ? t('settings.model.downloaded')
        : downloaded === false ? t('settings.model.notDownloaded')
        : null;
    const device = row?.placement?.device;

    return (
        <section className="settings-card model-in-use" aria-label={t('settings.model.inUseTitle')}>
            <span className="model-in-use-label">{t('settings.model.inUseTitle')}</span>
            {model ? (
                <bdi className="model-in-use-name" title={model}>{row?.display_name || active?.display_name || displayModelName(model)}</bdi>
            ) : (
                <span className="settings-hint">{t('settings.model.noneInUse')}</span>
            )}
            {active?.config_error && <p className="settings-error" role="alert"><bdi>{active.config_error}</bdi></p>}
            {model && (
                <span className="model-chips">
                    {remote && <span className="model-badge is-device">{t('settings.model.cloud')}</span>}
                    {remote && providerName && <bdi className="model-badge is-ready">{providerName}</bdi>}
                    {device && <span className="model-badge is-device">{device.toUpperCase()}</span>}
                    {state && <span className={`model-badge ${row?.state === 'loaded' ? 'is-loaded' : 'is-ready'}`}>{state}</span>}
                    {row?.fits === true && <span className="model-badge is-fit">{t('settings.model.fits')}</span>}
                    {!!row?.size_gb && <bdi dir="ltr" className="model-badge is-size">{row.size_gb} GB</bdi>}
                    {remote && !!row?.context_length && (
                        <span className="model-badge is-ready">{t('settings.model.contextChip', { size: ctxLabel(row.context_length) })}</span>
                    )}
                </span>
            )}
            {(choices.length > 1 || (choices.length === 1 && !offered)) && (
                <div className="model-in-use-field">
                    <label className="settings-label" htmlFor="model-switch">{t('settings.model.switch')}</label>
                    <select
                        id="model-switch"
                        className="settings-input"
                        value={offered ? choices.find((m) => sameModelId(model, m.id))?.id : ''}
                        onChange={(e) => onSwitch(e.target.value)}
                        disabled={disabled}
                    >
                        {!offered && <option value="" disabled>{t.bare('settings.model.switchPlaceholder')}</option>}
                        {choices.map((m) => <option key={m.id} value={m.id}>{labels[m.id]}</option>)}
                    </select>
                </div>
            )}
            {model && !remote && active?.downloaded !== false && <ModelContextSize model={model} />}
        </section>
    );
}
