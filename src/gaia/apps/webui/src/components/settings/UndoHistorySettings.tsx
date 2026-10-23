// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import * as api from '../../services/api';
import type { UndoSettings } from '../../types/changes';
import '../TurnChanges.css';

const DAYS_ERROR = 'Keep for: enter a whole number of days from 1 to 365.';
const MB_ERROR = 'Use at most: enter a whole number of MB from 10 to 100,000.';

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
    const [settings, setSettings] = useState<UndoSettings | null>(null);
    const [days, setDays] = useState('');
    const [mb, setMb] = useState('');
    const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        api.getUndoSettings().then(
            (s) => { setSettings(s); setDays(String(s.retention_days)); setMb(String(s.max_mb)); },
            (err: unknown) => setResult({ ok: false, text: `Could not read undo history settings: ${err instanceof Error ? err.message : err}` }),
        );
    }, []);

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
            setResult({ ok: true, text: 'Saved. Older undo history beyond these limits was removed.' });
        } catch (err) {
            const fields = err instanceof api.ApiError && err.httpStatus === 422 ? err.fields : [];
            const badDays = fields.includes('retention_days');
            const badMb = fields.includes('max_mb');
            if (badDays || badMb) {
                // Reword the backend's pydantic errors per field instead of showing them raw.
                setRejected({ days: badDays, mb: badMb });
                setResult({ ok: false, text: 'Not saved. Fix the limit marked above.' });
            } else {
                setResult({ ok: false, text: err instanceof Error ? err.message : String(err) });
            }
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="settings-field" aria-label="Undo history">
            <span className="settings-label">Undo history</span>
            <p className="settings-hint">
                Before GAIA changes a file it keeps a copy, so you can undo a turn. Copies older than the
                time limit, or past the space limit (oldest first), are deleted. The space limit never deletes the latest turn.
            </p>
            {settings && (
                <>
                    <div className="setting-row">
                        <label htmlFor="undo-days">Keep for (days)</label>
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
                    {daysInvalid && <p id="undo-days-error" className="settings-error">{DAYS_ERROR}</p>}
                    <div className="setting-row">
                        <label htmlFor="undo-mb">Use at most (MB)</label>
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
                    {mbInvalid && <p id="undo-mb-error" className="settings-error">{MB_ERROR}</p>}
                    <div className="setting-row setting-row-path">
                        <span>In use</span>
                        <span>
                            {formatBytes(settings.used_bytes)} for {settings.turns} turn{settings.turns === 1 ? '' : 's'}
                            {' '}in <code className="setting-path">{settings.path}</code>
                        </span>
                    </div>
                    <button type="button" className="btn-model-save" onClick={() => void save()} disabled={!dirty || saving || daysInvalid || mbInvalid}>
                        {saving ? 'Saving…' : 'Save'}
                    </button>
                </>
            )}
            {result && <p className={result.ok ? 'settings-ok' : 'settings-error'} role={result.ok ? 'status' : 'alert'}>{result.text}</p>}
        </div>
    );
}
