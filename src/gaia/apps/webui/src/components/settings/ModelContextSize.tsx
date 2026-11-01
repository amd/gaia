// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import { ChevronDown, Loader2 } from 'lucide-react';
import * as api from '../../services/api';
import { ref } from '../../i18n/registry';
import { useT } from '../../i18n/useT';
import { ctxLabel } from '../modelMeta';
import type { ContextChoice } from '../../types';

const AUTOMATIC = 'auto';

/** Context size for one local model: Automatic, or a size up to the most that fits this PC. */
export function ModelContextSize({ model }: { model: string }) {
    const t = useT();
    const [view, setView] = useState<ContextChoice | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [notice, setNotice] = useState<string | null>(null);

    useEffect(() => {
        setView(null);
        setError(null);
        setNotice(null);
        api.getContextChoice(model)
            .then(setView)
            .catch((err) => setError(err instanceof Error ? err.message : String(err)));
    }, [model]);

    const apply = useCallback(async (value: string) => {
        setBusy(true);
        setError(null);
        setNotice(null);
        try {
            const next = await api.setContextChoice(model, value === AUTOMATIC ? null : Number(value));
            setView(next);
            const params = {
                name: next.display_name,
                size: next.chosen ? ctxLabel(next.chosen) : ref('settings.ctx.automaticSize'),
            };
            const reason = next.reload?.reason;
            if (next.reload?.started) setNotice(t('settings.ctx.reloading', params));
            else if (reason === 'already_loaded') setNotice(t('settings.ctx.alreadyLoaded', params));
            else if (reason === 'warm_up') setNotice(t('settings.ctx.warmUp', params));
            else if (reason === 'busy') setNotice(t('settings.ctx.busy', params));
            else if (reason === 'not_gaias_server') setNotice(t('settings.ctx.ownServer', params));
            else if (reason === 'restarting') setNotice(t('settings.ctx.restarting'));
            else setNotice(t('settings.ctx.nextLoad'));
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setBusy(false);
        }
    }, [model, t]);

    if (error && !view) return <p className="settings-error" role="alert">{error}</p>;
    if (!view) return <p className="settings-hint"><Loader2 size={13} className="spin" /> {t('settings.ctx.checking')}</p>;

    const selected = view.chosen != null ? String(view.chosen) : AUTOMATIC;
    const loadedDiffers = view.chosen != null && view.loaded_ctx != null && view.loaded_ctx !== view.chosen;
    return (
        <>
            <div className="model-in-use-field">
                <label className="settings-label" htmlFor="ctx-size">{t('settings.ctx.label')}</label>
                <select
                    id="ctx-size"
                    className="settings-input"
                    value={selected}
                    onChange={(e) => void apply(e.target.value)}
                    disabled={busy || view.options.length === 0}
                >
                    <option value={AUTOMATIC}>
                        {view.automatic ? t.bare('settings.ctx.automaticAt', { size: ctxLabel(view.automatic) }) : t.bare('settings.ctx.automatic')}
                    </option>
                    {view.options.map((o) => (
                        <option key={o} value={String(o)}>{t.bare('settings.ctx.tokens', { size: ctxLabel(o) })}</option>
                    ))}
                </select>
            </div>
            {view.note && <span className="settings-hint"><bdi>{view.note}</bdi></span>}
            {!view.note && !!view.max_fit && (
                <details className="settings-more">
                    <summary><ChevronDown size={13} className="settings-disclosure-chevron" aria-hidden="true" /> {t('settings.ctx.about')}</summary>
                    <span className="settings-hint">
                        {t('settings.ctx.explain', { size: ctxLabel(view.max_fit), name: view.display_name, memory: view.memory_source })}
                    </span>
                </details>
            )}
            {loadedDiffers && <span className="settings-hint">{t('settings.ctx.loadedAt', { size: ctxLabel(view.loaded_ctx) })}</span>}
            {busy && <span className="settings-hint"><Loader2 size={13} className="spin" /> {t('settings.ctx.applying')}</span>}
            {notice && <p className="settings-ok" role="status">{notice}</p>}
            {error && <p className="settings-error" role="alert">{error}</p>}
        </>
    );
}
