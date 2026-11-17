// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import * as memoryApi from '../../services/memoryApi';
import { useT } from '../../i18n/useT';
import { isolate } from '../../i18n/registry';

/** Value of the "default device" option. */
const PROFILE = 'profile';

/** Where documents and memory are embedded, and the choice to move it. */
export function EmbedderSetting() {
    const t = useT();
    const [view, setView] = useState<memoryApi.EmbedderView | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [notice, setNotice] = useState(false);

    useEffect(() => {
        memoryApi.getEmbedder()
            .then(setView)
            .catch((err) => setError(err instanceof Error ? err.message : String(err)));
    }, []);

    const apply = useCallback(async (value: string) => {
        setBusy(true);
        setError(null);
        setNotice(false);
        try {
            const next = await memoryApi.setEmbedder(value === PROFILE ? null : (value as memoryApi.EmbedderDevice));
            setView(next);
            setNotice(next.changed !== false);
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
            // Show what is saved now, not the choice that was refused.
            await memoryApi.getEmbedder().then(setView).catch(() => undefined);
        } finally {
            setBusy(false);
        }
    }, []);

    if (!view) {
        if (error) return <p className="settings-error" role="alert">{error}</p>;
        return <p className="settings-hint"><Loader2 size={13} className="spin" /> {t('settings.memory.embedder.checking')}</p>;
    }
    const current = view.options.find((o) => o.device === view.device);
    const deviceName = (device: string) => t.bare(device === 'npu' ? 'settings.memory.embedder.npu' : 'settings.memory.embedder.gpu');
    return (
        <>
            <div className="model-in-use-field">
                <label className="settings-label" htmlFor="embedder-device">{t('settings.memory.embedder.label')}</label>
                <select
                    id="embedder-device"
                    className="settings-input"
                    value={view.chosen ? view.device : PROFILE}
                    onChange={(e) => void apply(e.target.value)}
                    disabled={busy}
                >
                    <option value={PROFILE}>
                        {t.bare('settings.memory.embedder.default', { device: deviceName(view.default_device) })}
                    </option>
                    {view.options.map((o) => (
                        <option key={o.device} value={o.device} disabled={o.available === false}>
                            {o.present === false
                                ? t.bare('settings.memory.embedder.unavailable', { device: deviceName(o.device) })
                                : !o.proven
                                    ? t.bare('settings.memory.embedder.unproven', { device: deviceName(o.device) })
                                    : deviceName(o.device)}
                        </option>
                    ))}
                </select>
            </div>
            <span className="settings-hint">
                {t('settings.memory.embedder.runsOn', { name: isolate(current?.display_name ?? view.device, 'ltr'), device: deviceName(view.device) })}
                {' '}{t('settings.memory.embedder.why')}
            </span>
            {view.options.some((o) => o.device === 'npu' && !o.proven && o.present) && (
                <span className="settings-hint">{t('settings.memory.embedder.npuUnproven')}</span>
            )}
            {current?.downloaded === false && (
                <span className="settings-hint">{t('settings.memory.embedder.notDownloaded')}</span>
            )}
            {busy && <span className="settings-hint"><Loader2 size={13} className="spin" /> {t('settings.memory.embedder.applying')}</span>}
            {notice && <p className="settings-ok" role="status">{t('settings.memory.embedder.changed')}</p>}
            {error && <p className="settings-error" role="alert">{error}</p>}
        </>
    );
}
