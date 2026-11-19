// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, Shuffle } from 'lucide-react';
import { Popover } from './Popover';
import * as api from '../services/api';
import { displayModelName, useModelStore } from '../stores/modelStore';
import { useT } from '../i18n/useT';
import type { ProviderId } from '../types';

interface RetryModel { id: string; name: string; sizeGb: number | null }

interface RetryChoices {
    /** The other models of the cloud service in use; empty on a local chat. */
    cloud: RetryModel[];
    /** Downloaded models on this PC, from every local provider. */
    local: RetryModel[];
}

function toRetryModel(m: { id: string; display_name?: string; size_gb?: number | null }): RetryModel {
    return { id: m.id, name: m.display_name ?? displayModelName(m.id), sizeGb: m.size_gb ?? null };
}

async function retryChoices(cloudProvider: ProviderId | null): Promise<RetryChoices> {
    const { providers, lemonade_error } = await api.listProviders();
    if (lemonade_error) throw new Error(lemonade_error);
    const localLists = providers.filter((p) => !p.remote).map((p) => api.listProviderModels(p.id));
    const inUse = cloudProvider ? providers.find((p) => p.remote && p.id === cloudProvider) : undefined;
    const [cloudList, ...lists] = await Promise.all([
        inUse ? api.listProviderModels(inUse.id) : Promise.resolve(null),
        ...localLists,
    ]);
    return {
        cloud: cloudList && !cloudList.blocked
            ? cloudList.models.filter((m) => m.available !== false).map((m) => ({ ...toRetryModel(m), sizeGb: null }))
            : [],
        local: lists.flatMap((l) => l.models.filter((m) => m.downloaded).map(toRetryModel)),
    };
}

/** "Retry with…": pick another model to answer the same question. */
export function RetryWithMenu({ answeredBy, onPick }: { answeredBy?: string | null; onPick: (model: string) => void }) {
    const anchor = useRef<HTMLButtonElement>(null);
    const t = useT();
    const active = useModelStore((s) => s.active);
    const cloudName = active?.remote ? active.provider_name ?? null : null;
    const cloudProvider = active?.remote ? active.provider : null;
    const [open, setOpen] = useState(false);
    const [choices, setChoices] = useState<RetryChoices | null>(null);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        setChoices(null);
        setError(null);
        try {
            setChoices(await retryChoices(cloudProvider));
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        }
    }, [cloudProvider]);

    useEffect(() => { if (open) void load(); }, [open, load]);

    const cloud = choices?.cloud.filter((m) => m.id !== answeredBy) ?? [];
    const local = choices?.local.filter((m) => m.id !== answeredBy) ?? [];
    const item = (m: RetryModel, hint: string) => (
        <button
            key={m.id}
            type="button"
            className="retry-menu-item"
            title={m.id}
            onClick={() => { setOpen(false); onPick(m.id); }}
        >
            <span className="retry-menu-name">{m.name}</span>
            <span className="retry-menu-meta">
                {m.sizeGb ? <bdi dir="ltr">{m.sizeGb} GB</bdi> : hint}
            </span>
        </button>
    );
    return (
        <>
            <button
                ref={anchor}
                type="button"
                className="msg-action-btn"
                onClick={() => setOpen((v) => !v)}
                title={t('retry.button')}
                aria-label={t('retry.button')}
                aria-expanded={open}
            >
                <Shuffle size={12} />
            </button>
            <Popover open={open} onClose={() => setOpen(false)} anchor={anchor} label="Retry with" placement="below" align="end">
                <div className="retry-menu">
                    <div className="retry-menu-head">{t('retry.head')}</div>
                    {error && <div className="retry-menu-error" role="alert">{error}</div>}
                    {!error && choices === null && (
                        <div className="retry-menu-empty"><Loader2 size={12} className="spin" aria-hidden="true" /> {t('retry.loading')}</div>
                    )}
                    {cloud.length > 0 && cloudName && (
                        <div className="retry-menu-note">{t('retry.onProvider', { provider: cloudName })}</div>
                    )}
                    {cloud.map((m) => item(m, t('retry.cloudHint')))}
                    {local.length > 0 && cloudName && (
                        <div className="retry-menu-note">{t('retry.onThisPc', { provider: cloudName })}</div>
                    )}
                    {local.map((m) => item(m, t('retry.localHint')))}
                    {choices !== null && cloud.length === 0 && local.length === 0 && (
                        <div className="retry-menu-empty">{t('retry.none')}</div>
                    )}
                </div>
            </Popover>
        </>
    );
}
