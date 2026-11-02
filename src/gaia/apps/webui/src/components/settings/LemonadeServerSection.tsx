// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import { AlertCircle, Loader2 } from 'lucide-react';
import * as api from '../../services/api';
import { useT } from '../../i18n/useT';
import { lemonadeKeyRejected } from '../../utils/lemonade';
import type { LemonadeTarget, LemonadeTargetBody } from '../../types';
import '../SettingsModal.css';

/** Which Lemonade Server GAIA uses: its own, or one the user already runs. */
export function LemonadeServerSection() {
    const t = useT();
    const [target, setTarget] = useState<LemonadeTarget | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [url, setUrl] = useState('');
    const [key, setKey] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [keyAsked, setKeyAsked] = useState(false);

    useEffect(() => {
        api.getLemonadeTarget()
            .then((t) => {
                setTarget(t);
                setUrl(t.saved_base_url ?? t.base_url ?? '');
            })
            .catch((err) => setLoadError(err instanceof Error ? err.message : String(err)));
    }, []);

    const save = useCallback(async (body: LemonadeTargetBody) => {
        setBusy(true);
        setError(null);
        try {
            const next = await api.setLemonadeTarget(body);
            setTarget(next);
            setKey('');
            setKeyAsked(false);
        } catch (err) {
            setKeyAsked(lemonadeKeyRejected(err));
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setBusy(false);
        }
    }, []);

    const connect = () => void save({ mode: 'external', base_url: url.trim(), api_key: key.trim() || undefined });

    if (loadError) {
        return (
            <section className="settings-section">
                <h4>{t('settings.advanced.modelServer')}</h4>
                <div className="model-warning" role="alert">
                    <AlertCircle size={14} />
                    <div className="model-warning-content">
                        <strong>{t('settings.server.readFailed')}</strong>
                        <p>{loadError}</p>
                    </div>
                </div>
            </section>
        );
    }
    if (!target) return null;

    const external = target.mode === 'external';
    return (
        <section className="settings-section" aria-label={t('settings.advanced.modelServer')}>
            <h4>{t('settings.advanced.modelServer')}</h4>
            <p className="model-override-desc" role="status">
                {target.source === 'env'
                    ? t('settings.server.usesEnv', { url: target.base_url ?? '' })
                    : external
                        ? t('settings.server.usesYours', { url: target.base_url ?? '' })
                        : t('settings.server.usesOwn')}
            </p>
            {target.source === 'env' ? (
                <p className="model-status-hint">{t('settings.server.unsetEnv')}</p>
            ) : (
                <>
                    <p className="model-status-hint">
                        {t('settings.server.useYours')}
                        {' '}<bdi>{target.what_gaia_does}</bdi>
                    </p>
                    <form className="model-input-row" onSubmit={(e) => { e.preventDefault(); connect(); }}>
                        <input
                            type="text"
                            className="model-input"
                            placeholder="http://localhost:13305"
                            value={url}
                            onChange={(e) => { setUrl(e.target.value); setError(null); }}
                            disabled={busy}
                            spellCheck={false}
                            autoCapitalize="off"
                            autoCorrect="off"
                            aria-label={t('settings.server.address')}
                        />
                        <input
                            type="password"
                            className="model-input"
                            autoComplete="off"
                            placeholder={target.api_key_stored ? t.bare('settings.server.keySaved') : t.bare('settings.server.keyOptional')}
                            value={key}
                            onChange={(e) => setKey(e.target.value)}
                            disabled={busy}
                            aria-label={t('settings.server.key')}
                        />
                        <div className="model-btn-group">
                            <button type="submit" className="btn-model-save" disabled={busy || !url.trim()}>
                                {busy ? <><Loader2 size={13} className="btn-spinner" /> {t('settings.server.checking')}</> : t('settings.server.use')}
                            </button>
                        </div>
                    </form>
                    {external && (
                        <div className="setting-actions">
                            <button type="button" className="btn-secondary" disabled={busy} onClick={() => void save({ mode: 'gaia' })}>
                                {t('settings.server.useOwn')}
                            </button>
                        </div>
                    )}
                    {error && (
                        <div className="model-warning" role="alert">
                            <AlertCircle size={14} />
                            <div className="model-warning-content">
                                <strong>{keyAsked ? t('settings.server.needsKey') : t('settings.server.notSwitched')}</strong>
                                <p>{error}</p>
                            </div>
                        </div>
                    )}
                </>
            )}
        </section>
    );
}
