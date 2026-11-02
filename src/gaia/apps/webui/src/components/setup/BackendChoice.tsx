// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { useT } from '../../i18n/useT';
import * as api from '../../services/api';
import type { ModelServerInfo } from '../../types/api.gen';
import { backendLabel } from '../settings/ModelServerPanel';

function errorText(err: unknown): string {
    return err instanceof Error ? err.message : String(err);
}

/**
 * Which llama.cpp backend runs the chat model: the same setting as Settings →
 * Advanced → Model server, so the two always agree. NPU models run on their
 * own backend and are picked in the chat list.
 */
export function BackendChoice({ disabled = false }: { disabled?: boolean }) {
    const t = useT();
    const [info, setInfo] = useState<ModelServerInfo | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [saving, setSaving] = useState<string | null>(null);

    useEffect(() => {
        let live = true;
        api.getModelServer()
            .then((next) => { if (live) setInfo(next); })
            .catch((err) => { if (live) setError(t('setup.backend.readFailed', { error: errorText(err) })); });
        return () => { live = false; };
    }, [t]);

    const choose = useCallback(async (name: string) => {
        setSaving(name);
        setError(null);
        try {
            setInfo(await api.setModelServerBackend(name));
        } catch (err) {
            setError(t('setup.backend.saveFailed', { error: errorText(err) }));
        } finally {
            setSaving(null);
        }
    }, [t]);

    const available = info?.available_backends ?? [];
    const preferred = info?.preferred_backend ?? null;
    const choices = ['auto', ...available.map((b) => b.name)];
    if (preferred && !choices.includes(preferred)) choices.push(preferred);
    const selected = preferred ?? 'auto';
    const toFetch = available.filter((b) => b.state === 'installable').map((b) => backendLabel(b.name));

    return (
        <section className="setup-backend" aria-labelledby="setup-backend-title">
            <h3 className="mc-job-title" id="setup-backend-title">{t('setup.backend.title')}</h3>
            {!info && !error && <p className="setup-hint" role="status"><Loader2 size={13} className="spin" aria-hidden="true" /> {t('setup.backend.reading')}</p>}
            {info && available.length === 0 && !preferred && <p className="setup-hint">{t('setup.backend.none')}</p>}
            {info && (available.length > 0 || preferred) && (
                <>
                    <div className="segmented" role="radiogroup" aria-labelledby="setup-backend-title">
                        {choices.map((name) => (
                            <button
                                key={name}
                                type="button"
                                role="radio"
                                aria-checked={selected === name}
                                className={selected === name ? 'is-active' : ''}
                                disabled={disabled || saving !== null}
                                onClick={() => { if (name !== selected) void choose(name); }}
                            >
                                {saving === name && <Loader2 size={12} className="spin" aria-hidden="true" />}
                                <bdi>{name === 'auto'
                                    ? t(info.default_backend ? 'setup.backend.autoIs' : 'setup.backend.auto', { name: backendLabel(info.default_backend) })
                                    : backendLabel(name)}</bdi>
                            </button>
                        ))}
                    </div>
                    <p className="setup-hint">
                        {t.compose((say) => [
                            say('setup.backend.hint'),
                            toFetch.length ? say('setup.backend.fetch', { names: toFetch.join(', ') }) : null,
                        ].filter(Boolean).join(' '))}
                    </p>
                </>
            )}
            {error && <p className="setup-error" role="alert">{error}</p>}
        </section>
    );
}
