// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import * as api from '../../services/api';
import { ctxLabel } from '../modelMeta';
import type { ContextChoice } from '../../types';

const AUTOMATIC = 'auto';

/** Context size for one local model: Automatic, or a size up to the most that fits this PC. */
export function ModelContextSize({ model }: { model: string }) {
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
            const size = next.chosen ? ctxLabel(next.chosen) : 'the automatic size';
            if (next.reload?.started) {
                setNotice(`Reloading ${next.display_name} at ${size}. Chat pauses until it's back.`);
            } else if (next.reload?.reason === 'busy') {
                setNotice(`Saved. GAIA is answering right now, so ${next.display_name} switches to ${size} the next time it loads.`);
            } else if (next.reload?.reason === 'not_gaias_server') {
                setNotice(`Saved. GAIA doesn't reload models on your own server; ${next.display_name} uses ${size} the next time it loads.`);
            } else if (next.reload?.reason === 'restarting') {
                setNotice('Saved. A reload is already running; this size applies the next time the model loads.');
            } else {
                setNotice('Saved. It applies the next time this model loads.');
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setBusy(false);
        }
    }, [model]);

    if (error && !view) return <p className="settings-error" role="alert">{error}</p>;
    if (!view) return <p className="settings-hint"><Loader2 size={13} className="spin" /> Checking context sizes…</p>;

    const selected = view.chosen != null ? String(view.chosen) : AUTOMATIC;
    const loadedDiffers = view.chosen != null && view.loaded_ctx != null && view.loaded_ctx !== view.chosen;
    return (
        <div className="settings-field">
            <label className="settings-label" htmlFor="ctx-size">Context size</label>
            <select
                id="ctx-size"
                className="settings-input"
                value={selected}
                onChange={(e) => void apply(e.target.value)}
                disabled={busy || view.options.length === 0}
            >
                <option value={AUTOMATIC}>Automatic{view.automatic ? ` (${ctxLabel(view.automatic)})` : ''}</option>
                {view.options.map((o) => (
                    <option key={o} value={String(o)}>{ctxLabel(o)} tokens</option>
                ))}
            </select>
            <span className="settings-hint">
                {view.note ?? (
                    view.max_fit
                        ? `Up to ${ctxLabel(view.max_fit)}, the most ${view.display_name} can hold on this PC (${view.memory_source}). A larger window uses more memory. GAIA reloads the model to apply it, except on a Lemonade you run yourself.`
                        : null
                )}
                {loadedDiffers && ` Loaded now at ${ctxLabel(view.loaded_ctx)}.`}
            </span>
            {busy && <span className="settings-hint"><Loader2 size={13} className="spin" /> Applying…</span>}
            {notice && <p className="settings-ok" role="status">{notice}</p>}
            {error && <p className="settings-error" role="alert">{error}</p>}
        </div>
    );
}
