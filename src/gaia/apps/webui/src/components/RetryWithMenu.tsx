// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, Shuffle } from 'lucide-react';
import { Popover } from './Popover';
import * as api from '../services/api';
import { displayModelName } from '../stores/modelStore';

interface LocalModel { id: string; name: string }

/** Downloaded models on this PC, from every local provider. */
async function localModels(): Promise<LocalModel[]> {
    const { providers, lemonade_error } = await api.listProviders();
    if (lemonade_error) throw new Error(lemonade_error);
    const lists = await Promise.all(
        providers.filter((p) => !p.remote).map((p) => api.listProviderModels(p.id)),
    );
    return lists.flatMap((l) => l.models
        .filter((m) => m.downloaded)
        .map((m) => ({ id: m.id, name: m.display_name ?? displayModelName(m.id) })));
}

/** "Retry with…": pick another local model to answer the same question. */
export function RetryWithMenu({ answeredBy, onPick }: { answeredBy?: string | null; onPick: (model: string) => void }) {
    const anchor = useRef<HTMLButtonElement>(null);
    const [open, setOpen] = useState(false);
    const [models, setModels] = useState<LocalModel[] | null>(null);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        setModels(null);
        setError(null);
        try {
            setModels(await localModels());
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        }
    }, []);

    useEffect(() => { if (open) void load(); }, [open, load]);

    const others = models?.filter((m) => m.id !== answeredBy) ?? [];
    return (
        <>
            <button
                ref={anchor}
                type="button"
                className="msg-action-btn"
                onClick={() => setOpen((v) => !v)}
                title="Retry with another model"
                aria-label="Retry with another model"
                aria-expanded={open}
            >
                <Shuffle size={12} />
            </button>
            <Popover open={open} onClose={() => setOpen(false)} anchor={anchor} label="Retry with" placement="below" align="end">
                <div className="retry-menu">
                    <div className="retry-menu-head">Retry with…</div>
                    {error && <div className="retry-menu-error" role="alert">{error}</div>}
                    {!error && models === null && (
                        <div className="retry-menu-empty"><Loader2 size={12} className="spin" aria-hidden="true" /> Loading models…</div>
                    )}
                    {models !== null && others.length === 0 && (
                        <div className="retry-menu-empty">No other model is downloaded on this PC. Add one in Settings → Model.</div>
                    )}
                    {others.map((m) => (
                        <button
                            key={m.id}
                            type="button"
                            className="retry-menu-item"
                            title={m.id}
                            onClick={() => { setOpen(false); onPick(m.id); }}
                        >
                            {m.name}
                        </button>
                    ))}
                </div>
            </Popover>
        </>
    );
}
