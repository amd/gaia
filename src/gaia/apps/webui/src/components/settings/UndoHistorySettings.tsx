// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import * as api from '../../services/api';
import { useT } from '../../i18n/useT';
import type { UndoSettings } from '../../types/changes';
import '../TurnChanges.css';

function isWholeInRange(value: string, min: number, max: number): boolean {
    if (!/^\d+$/.test(value.trim())) return false;
    const n = Number(value);
    return n >= min && n <= max;
}

function formatBytes(bytes: number): string {
    if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** How long, and how much, GAIA keeps the copies that make "Undo this turn" work. */
export function UndoHistorySettings() {
    const t = useT();
    const [settings, setSettings] = useState<UndoSettings | null>(null);
    const [days, setDays] = useState('');
    const [mb, setMb] = useState('');
    const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        api.getUndoSettings().then(
            (s) => { setSettings(s); setDays(String(s.retention_days)); setMb(String(s.max_mb)); },
            (err: unknown) => setResult({ ok: false, text: t('settings.undo.readFailed', { reason: err instanceof Error ? err.message : String(err) }) }),
        );
    }, [t]);

    const [rejected, setRejected] = useState<{ days: boolean; mb: boolean }>({ days: false, mb: false });
    const daysInvalid = !isWholeInRange(days, 1, 365) || rejected.days;
    const mbInvalid = !isWholeInRange(mb, 10, 100000) || rejected.mb;
    const dirty = settings !== null && (days !== String(settings.retention_days) || mb !== String(settings.max_mb));

    const save = async () => {
        if (daysInvalid || mbInvalid) return;
        setSaving(true);
        setResult(null);
        try {
            const s = await api.updateUndoSettings(Number(days), Number(mb));
            setSettings(s);
            setResult({ ok: true, text: t('settings.undo.saved') });
        } catch (err) {
            const fields = err instanceof api.ApiError && err.httpStatus === 422 ? err.fields : [];
            const badDays = fields.includes('retention_days');
            const badMb = fields.includes('max_mb');
            if (badDays || badMb) {
                // Reword the backend's pydantic errors per field instead of showing them raw.
                setRejected({ days: badDays, mb: badMb });
                setResult({ ok: false, text: t('settings.undo.fix') });
            } else {
                setResult({ ok: false, text: err instanceof Error ? err.message : String(err) });
            }
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="settings-field" aria-label={t('settings.privacy.undo')}>
            <span className="settings-label">{t('settings.privacy.undo')}</span>
            <p className="settings-hint">{t('settings.undo.hint')}</p>
            {settings && (
                <>
                    <div className="setting-row">
                        <label htmlFor="undo-days">{t('settings.undo.days')}</label>
                        <input
                            id="undo-days"
                            type="number"
                            className="model-input undo-history-input"
                            min={1}
                            max={365}
                            value={days}
                            aria-invalid={daysInvalid}
                            aria-describedby={daysInvalid ? 'undo-days-error' : undefined}
                            onChange={(e) => { setDays(e.target.value); setRejected((r) => ({ ...r, days: false })); }}
                        />
                    </div>
                    {daysInvalid && <p id="undo-days-error" className="settings-error">{t('settings.undo.daysError')}</p>}
                    <div className="setting-row">
                        <label htmlFor="undo-mb">{t('settings.undo.mb')}</label>
                        <input
                            id="undo-mb"
                            type="number"
                            className="model-input undo-history-input"
                            min={10}
                            max={100000}
                            value={mb}
                            aria-invalid={mbInvalid}
                            aria-describedby={mbInvalid ? 'undo-mb-error' : undefined}
                            onChange={(e) => { setMb(e.target.value); setRejected((r) => ({ ...r, mb: false })); }}
                        />
                    </div>
                    {mbInvalid && <p id="undo-mb-error" className="settings-error">{t('settings.undo.mbError')}</p>}
                    <div className="setting-row setting-row-path">
                        <span>{t('settings.undo.inUse')}</span>
                        <span>
                            {t.plural('settings.undo.used', settings.turns, { size: formatBytes(settings.used_bytes) })}
                            {' '}<bdi dir="ltr"><code className="setting-path">{settings.path}</code></bdi>
                        </span>
                    </div>
                    <button type="button" className="btn-model-save" onClick={() => void save()} disabled={!dirty || saving || daysInvalid || mbInvalid}>
                        {saving ? t('settings.models.applying') : t('settings.advanced.save')}
                    </button>
                </>
            )}
            {result && <p className={result.ok ? 'settings-ok' : 'settings-error'} role={result.ok ? 'status' : 'alert'}>{result.text}</p>}
        </div>
    );
}
