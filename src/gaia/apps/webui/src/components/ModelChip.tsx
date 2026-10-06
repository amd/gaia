// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { Check, ChevronDown, Cloud, Cpu, HelpCircle, Loader2 } from 'lucide-react';
import { Popover } from './Popover';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import { useModelStore, useInferencePlace, selectChipModel, shortModelName, UNKNOWN_PLACE_TITLE } from '../stores/modelStore';
import type { ProviderInfo, ProviderModel } from '../types';

interface ProviderModels {
    provider: ProviderInfo;
    models: ProviderModel[];
    error?: string;
}

/** The model the open chat runs on, where it runs, and a picker to change it. */
export function ModelChip({ disabled }: { disabled?: boolean }) {
    const active = useModelStore((s) => s.active);
    const select = useModelStore((s) => s.select);
    const place = useInferencePlace();
    const chipModel = useChatStore((s) => selectChipModel(active, s.sessions, s.currentSessionId));
    const openSettings = useChatStore((s) => s.openSettings);
    const anchor = useRef<HTMLButtonElement>(null);
    const [open, setOpen] = useState(false);
    const [groups, setGroups] = useState<ProviderModels[] | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [pendingCloud, setPendingCloud] = useState<{ model: string; provider: ProviderInfo } | null>(null);
    const [switching, setSwitching] = useState<string | null>(null);

    const load = useCallback(async () => {
        setError(null);
        setGroups(null);
        try {
            const { providers, lemonade_error } = await api.listProviders();
            if (lemonade_error) throw new Error(lemonade_error);
            const usable = providers.filter((p) => !p.remote || p.models_discovered);
            const lists = await Promise.all(usable.map(async (provider) => {
                try {
                    return { provider, models: (await api.listProviderModels(provider.id)).models };
                } catch (err) {
                    return { provider, models: [], error: err instanceof Error ? err.message : String(err) };
                }
            }));
            setGroups(lists);
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        }
    }, []);

    useEffect(() => {
        if (open) {
            setPendingCloud(null);
            void load();
        }
    }, [open, load]);

    const choose = useCallback(async (model: string, provider: ProviderInfo) => {
        if (provider.remote && !active?.remote && pendingCloud?.model !== model) {
            setPendingCloud({ model, provider });
            return;
        }
        setSwitching(model);
        setError(null);
        try {
            await select(model);
            setOpen(false);
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setSwitching(null);
            setPendingCloud(null);
        }
    }, [active?.remote, pendingCloud, select]);

    const Icon = place?.remote ? Cloud : place ? Cpu : HelpCircle;
    const label = chipModel
        ? `${shortModelName(chipModel)}${place ? ` · ${place.label}` : ''}`
        : 'Model';
    const title = !place
        ? UNKNOWN_PLACE_TITLE
        : place.description
            ?? (place.remote ? `Runs on ${place.label} — chat history leaves this PC` : 'Runs on this PC');

    return (
        <div className="composer-chip-wrap">
            <button
                ref={anchor}
                type="button"
                className="composer-chip"
                onClick={() => setOpen((v) => !v)}
                disabled={disabled}
                aria-haspopup="dialog"
                aria-expanded={open}
                title={title}
            >
                <Icon size={13} aria-hidden="true" />
                <span className="composer-chip-label">{label}</span>
                <ChevronDown size={12} aria-hidden="true" />
            </button>
            <Popover open={open} onClose={() => setOpen(false)} anchor={anchor} label="Choose a model" align="end">
                {error && <div className="popover-message is-error" role="alert">{error}</div>}
                {!groups && !error && (
                    <div className="popover-message"><Loader2 size={13} className="spin" /> Loading models…</div>
                )}
                {groups?.map(({ provider, models, error: groupError }) => (
                    <div key={provider.id}>
                        <div className="popover-heading">{provider.name}</div>
                        {groupError && <div className="popover-message is-error">{groupError}</div>}
                        {!groupError && models.length === 0 && (
                            <div className="popover-message">No models yet.</div>
                        )}
                        {models.map((m) => {
                            const current = active?.model === m.id;
                            const needsDownload = !provider.remote && !m.downloaded;
                            return (
                                <button
                                    key={m.id}
                                    type="button"
                                    className="popover-item"
                                    onClick={() => choose(m.id, provider)}
                                    disabled={needsDownload || switching !== null}
                                    aria-current={current ? 'true' : undefined}
                                >
                                    <span className="popover-item-check" aria-hidden="true">
                                        {switching === m.id ? <Loader2 size={13} className="spin" /> : current ? <Check size={13} /> : null}
                                    </span>
                                    <span className="popover-item-body">
                                        <span className="popover-item-title">{shortModelName(m.id)}</span>
                                        {(m.note || needsDownload) && (
                                            <span className="popover-item-note">
                                                {needsDownload ? 'Not downloaded — get it in Settings' : `#${m.rank} ${m.note}`}
                                            </span>
                                        )}
                                    </span>
                                </button>
                            );
                        })}
                    </div>
                ))}
                {pendingCloud && (
                    <div className="popover-message" role="alert">
                        {pendingCloud.provider.privacy_notice}{' '}
                        <button type="button" className="link-btn" onClick={() => choose(pendingCloud.model, pendingCloud.provider)}>
                            Use {shortModelName(pendingCloud.model)}
                        </button>
                    </div>
                )}
                <div className="popover-divider" />
                <button
                    type="button"
                    className="popover-item"
                    onClick={() => { setOpen(false); openSettings('model'); }}
                >
                    <span className="popover-item-check" />
                    <span className="popover-item-body"><span className="popover-item-title">Providers and keys…</span></span>
                </button>
            </Popover>
        </div>
    );
}
