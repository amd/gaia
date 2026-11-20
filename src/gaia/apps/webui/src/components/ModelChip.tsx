// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Fragment, useCallback, useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import { AlertCircle, Check, ChevronDown, Cloud, Cpu, Download, HelpCircle, KeyRound, Link2, Loader2 } from 'lucide-react';
import { Popover } from './Popover';
import './ModelPicker.css';
import * as api from '../services/api';
import { requestErrorText } from '../utils/requestErrors';
import { useChatStore } from '../stores/chatStore';
import {
    useModelStore, useInferencePlace, selectChipModel, displayModelName, distinctModelLabels, sameModelId, UNKNOWN_PLACE_TITLE,
} from '../stores/modelStore';
import { STATE_LABELS, isDownloading, modelState, placementText } from './modelMeta';
import { InfoBubble } from './InfoBubble';
import { formatPrice } from './settings/cloudPrice';
import { useT } from '../i18n/useT';
import { providerName, providerNotice } from '../utils/providerText';
import { MarkedText, markLtr } from './MarkedText';
import type { ProviderInfo, ProviderModel } from '../types';

interface ProviderModels {
    provider: ProviderInfo;
    models: ProviderModel[];
    error?: string;
}

const DOWNLOAD_POLL_MS = 2000;
const KEY_WAITING = 'waiting_for_os_permission';
const KEY_LOCKED = 'store_locked';
// How many polls a picked model may show no download before the pick is given up.
const PICK_START_POLLS = 5;

/** What the info bubble lists for *m*: where it runs, size, speed, rank, price. */
function modelDetails(m: ProviderModel, remote: boolean): string[] {
    const rank = m.rank ? `#${m.rank} ${m.note ?? ''}`.trim() : null;
    if (remote) return [rank, formatPrice(m.price)].filter((x): x is string => !!x);
    return [
        placementText(m.placement),
        m.size_gb ? `${m.size_gb} GB` : null,
        m.tokens_per_second ? `${Math.round(m.tokens_per_second)} tok/s on this PC` : null,
        rank,
        m.custom ? 'added by you' : null,
    ].filter((x): x is string => !!x);
}

/** Not on this PC yet, so listed under "Available to download"; the model in use never is. */
function toDownload(m: ProviderModel, current: string | null | undefined): boolean {
    return !sameModelId(current, m.id) && (isDownloading(m) || modelState(m) === 'not_downloaded');
}

/** The provider in use first; in a local list, the models on this PC before the ones to download. */
function orderGroups(groups: ProviderModels[], activeProvider: string | undefined, current: string | null | undefined): ProviderModels[] {
    const inUse = (g: ProviderModels) => (g.provider.id === activeProvider ? 0 : 1);
    return [...groups]
        .sort((a, b) => inUse(a) - inUse(b))
        .map((g) => (g.provider.remote ? g : {
            ...g,
            models: [...g.models].sort((a, b) => Number(toDownload(a, current)) - Number(toDownload(b, current))),
        }));
}

/** Loaded (green dot), downloaded, or a download button — always with a text label. */
function StateMark({ m, onDownload, busy }: { m: ProviderModel; onDownload: () => void; busy: boolean }) {
    const t = useT();
    if (isDownloading(m)) {
        const pct = Math.round(m.download_progress?.percent ?? 0);
        return (
            <span className="model-state is-downloading" title={`Downloading, ${pct}%`}>
                <Loader2 size={12} className="spin" aria-hidden="true" /> {pct}%
            </span>
        );
    }
    const state = modelState(m);
    if (state === 'not_downloaded') {
        return (
            <button
                type="button"
                className="model-state-btn"
                onClick={(e) => { e.stopPropagation(); onDownload(); }}
                disabled={busy}
                aria-label={t('modelMenu.downloadModel', { name: m.display_name ?? m.id })}
                title={m.size_gb ? `${STATE_LABELS.not_downloaded} (${m.size_gb} GB)` : STATE_LABELS.not_downloaded}
                // Picking the row downloads it too, so the list keeps one Tab stop.
                tabIndex={-1}
            >
                <Download size={13} aria-hidden="true" />
            </button>
        );
    }
    if (state === 'loaded') {
        return <span className="model-state is-loaded" role="img" aria-label={STATE_LABELS.loaded} title={STATE_LABELS.loaded} />;
    }
    return <span className="model-state is-ready" title={STATE_LABELS.downloaded}>{t('modelMenu.ready')}</span>;
}

/** The model the open chat runs on, where it runs, and a picker to change it. */
export function ModelChip() {
    const t = useT();
    const active = useModelStore((s) => s.active);
    const select = useModelStore((s) => s.select);
    const queued = useModelStore((s) => s.queued);
    const currentModel = queued ?? active?.model;
    const queue = useModelStore((s) => s.queue);
    const queueError = useModelStore((s) => s.queueError);
    const rememberNames = useModelStore((s) => s.rememberNames);
    useModelStore((s) => s.names); // re-render when friendly names arrive
    const place = useInferencePlace();
    const chipModel = useChatStore((s) => selectChipModel(active, s.sessions, s.currentSessionId));
    const turnRunning = useChatStore((s) => s.isStreaming);
    const openSettings = useChatStore((s) => s.openSettings);
    const anchor = useRef<HTMLButtonElement>(null);
    const [open, setOpen] = useState(false);
    const [groups, setGroups] = useState<ProviderModels[] | null>(null);
    const [showAllFireworksModels, setShowAllFireworksModels] = useState(false);
    const [unavailable, setUnavailable] = useState(0);
    const [error, setError] = useState<string | null>(null);
    const [pendingCloud, setPendingCloud] = useState<{ model: string; provider: ProviderInfo } | null>(null);
    // A download asked for, waiting on the user's yes; `pick` switches to it once downloaded.
    const [pendingDownload, setPendingDownload] = useState<{ model: string; provider: ProviderInfo; pick: boolean } | null>(null);
    const [switching, setSwitching] = useState<string | null>(null);
    const [downloading, setDownloading] = useState<string | null>(null);
    // A model picked from the list before it was downloaded: switch to it once it is.
    const [pickAfterDownload, setPickAfterDownload] = useState<{ model: string; provider: ProviderInfo } | null>(null);
    // Download requests still waiting on an answer; until then a row still reads "not downloaded".
    const [requestsPending, setRequestsPending] = useState(0);
    // The switch after a download failed, possibly with the list closed: say so on the chip.
    const [autoSwitchError, setAutoSwitchError] = useState<string | null>(null);
    // List refreshes that found the picked model missing, or neither downloading nor downloaded.
    const pollsWithoutProgress = useRef(0);
    const lastCountedList = useRef<ProviderModels[] | null>(null);
    const listId = useId();
    const list = useRef<HTMLDivElement>(null);
    const consent = useRef<HTMLDivElement>(null);
    // The row holding the list's one Tab stop, and the row whose info bubble a key opened.
    const [activeRow, setActiveRow] = useState<string | null>(null);
    const [infoRow, setInfoRow] = useState<string | null>(null);
    const closeInfo = useCallback(() => setInfoRow(null), []);
    // Stable, so Popover's focus-on-open doesn't re-run (and refocus its first button) on each render.
    const close = useCallback(() => setOpen(false), []);
    // Set when a fresh list lands after opening, so focus moves to the current model once.
    const focusOnLoad = useRef(false);
    // Set when the Fireworks list expands, so focus moves to its first newly visible model.
    const focusFirstShown = useRef<string | null>(null);

    const load = useCallback(async (quiet = false) => {
        if (!quiet) {
            focusOnLoad.current = false;
            setError(null);
            setGroups(null);
        }
        try {
            const { providers, lemonade_error } = await api.listProviders();
            if (lemonade_error) throw new Error(lemonade_error);
            // Fireworks is always offered (with "Add your key"); the gateway once registered.
            const shown = providers.filter((p) => !p.remote || p.models_discovered || p.id === 'fireworks' || p.registered);
            const lists = await Promise.all(shown.map(async (provider) => {
                if (provider.blocked) return { provider, models: [], error: t('settings.privacy.localOnlyBlocked') };
                if (provider.remote && !provider.models_discovered) return { provider, models: [] };
                try {
                    const response = await api.listProviderModels(provider.id);
                    return { provider, models: response.models, error: response.blocked ? t('settings.privacy.localOnlyBlocked') : undefined };
                } catch (err) {
                    return { provider, models: [], error: requestErrorText(err) };
                }
            }));
            rememberNames(lists.flatMap((g) => g.models));
            setUnavailable(lists.flatMap((g) => g.models).filter((m) => m.available === false).length);
            if (!quiet) focusOnLoad.current = true;
            setGroups(lists.map((g) => ({ ...g, models: g.models.filter((m) => m.available !== false) })));
        } catch (err) {
            setError(requestErrorText(err));
        }
    }, [rememberNames, t]);

    // The user closed the OS unlock prompt; GAIA asks again only on this click.
    const retryKeyStore = useCallback(async () => {
        try {
            await api.retryKeyStore();
        } catch (err) {
            setError(requestErrorText(err));
            return;
        }
        await load(true);
    }, [load]);

    useEffect(() => {
        if (open) {
            setPendingCloud(null);
            setPendingDownload(null);
            setShowAllFireworksModels(false);
            setActiveRow(null);
            setInfoRow(null);
            void load();
        }
    }, [open, load]);

    useEffect(() => {
        if (!open || !groups || !focusOnLoad.current) return;
        const row = list.current?.querySelector<HTMLElement>('[role="option"][tabindex="0"]');
        if (!row) return;
        focusOnLoad.current = false;
        row.focus();
        row.scrollIntoView?.({ block: 'nearest' });
    }, [open, groups]);

    useEffect(() => {
        const providerId = focusFirstShown.current;
        if (!open || !groups || !providerId || !showAllFireworksModels) return;
        const group = groups.find((g) => g.provider.id === providerId);
        const firstShown = group?.models.find((m) => m.rank === null && !sameModelId(currentModel, m.id));
        if (!firstShown) return;
        const row = Array.from(list.current?.querySelectorAll<HTMLElement>('[role="option"]') ?? [])
            .find((candidate) => candidate.dataset.row === firstShown.id);
        if (!row) return;
        focusFirstShown.current = null;
        setActiveRow(firstShown.id);
        row.focus();
        row.scrollIntoView?.({ block: 'nearest' });
    }, [open, groups, showAllFireworksModels, currentModel]);

    useEffect(() => {
        if (pendingCloud || pendingDownload) consent.current?.scrollIntoView?.({ block: 'nearest' });
    }, [pendingCloud, pendingDownload]);

    const onListKey = (e: KeyboardEvent<HTMLDivElement>) => {
        if (e.altKey || e.ctrlKey || e.metaKey) return;
        const rows = Array.from(list.current?.querySelectorAll<HTMLElement>('[role="option"]:not(:disabled)') ?? []);
        if (!rows.length) return;
        const at = rows.indexOf(document.activeElement as HTMLElement);
        let next: number;
        switch (e.key) {
            case 'ArrowDown': next = at < 0 ? 0 : Math.min(at + 1, rows.length - 1); break;
            case 'ArrowUp': next = at < 0 ? rows.length - 1 : Math.max(at - 1, 0); break;
            case 'Home': next = 0; break;
            case 'End': next = rows.length - 1; break;
            case 'ArrowRight':
            case 'i': {
                const row = at < 0 ? undefined : rows[at].dataset.row;
                if (!row) return;
                e.preventDefault();
                setInfoRow((r) => (e.key === 'i' && r === row ? null : row));
                return;
            }
            case 'ArrowLeft':
                e.preventDefault();
                setInfoRow(null);
                return;
            default:
                return;
        }
        e.preventDefault();
        rows[next].focus();
        rows[next].scrollIntoView?.({ block: 'nearest' });
    };

    const anyDownloading = !!groups?.some((g) => g.models.some(isDownloading));
    const awaitingPermission = !!groups?.some((g) => g.provider.key_state === KEY_WAITING);
    useEffect(() => {
        if (!(open || pickAfterDownload) || !(anyDownloading || pickAfterDownload || awaitingPermission)) return;
        const t = setInterval(() => { void load(true); }, DOWNLOAD_POLL_MS);
        return () => clearInterval(t);
    }, [open, anyDownloading, pickAfterDownload, awaitingPermission, load]);

    const download = useCallback(async (model: string) => {
        setDownloading(model);
        setRequestsPending((n) => n + 1);
        setError(null);
        try {
            await api.downloadModel(model);
            await load(true);
        } catch (err) {
            setPickAfterDownload((p) => (p?.model === model ? null : p));
            setError(requestErrorText(err));
        } finally {
            setDownloading(null);
            setRequestsPending((n) => n - 1);
        }
    }, [load]);

    const startDownload = useCallback((model: string, provider: ProviderInfo, pick: boolean) => {
        setPendingDownload(null);
        if (pick) {
            pollsWithoutProgress.current = 0;
            setAutoSwitchError(null);
            setPickAfterDownload({ model, provider });
        }
        void download(model);
    }, [download]);

    /** Switch to *model*; returns why it failed, or null. */
    const choose = useCallback(async (model: string, provider: ProviderInfo): Promise<string | null> => {
        if (provider.remote && !active?.remote && pendingCloud?.model !== model) {
            setPendingCloud({ model, provider });
            return null;
        }
        setPendingCloud(null);
        if (turnRunning) {
            // The running reply keeps its model; this one starts with the next message.
            queue(model === active?.model ? null : model);
            setOpen(false);
            return null;
        }
        setSwitching(model);
        setError(null);
        try {
            await select(model);
            setAutoSwitchError(null);
            setOpen(false);
            return null;
        } catch (err) {
            const message = requestErrorText(err);
            setError(message);
            return message;
        } finally {
            setSwitching(null);
        }
    }, [active?.model, active?.remote, pendingCloud, queue, select, turnRunning]);

    useEffect(() => {
        // Wait out the download request itself; until it answers, the row still reads "not downloaded".
        if (!pickAfterDownload || !groups || requestsPending > 0) return;
        const { model, provider } = pickAfterDownload;
        // Count each refreshed list once, not every effect run (`choose` changes too).
        const missedPoll = () => {
            if (lastCountedList.current !== groups) {
                lastCountedList.current = groups;
                pollsWithoutProgress.current += 1;
            }
            return pollsWithoutProgress.current >= PICK_START_POLLS;
        };
        const m = groups.flatMap((g) => g.models).find((x) => x.id === model);
        if (!m) {
            if (!missedPoll()) return;
            setPickAfterDownload(null);
            setAutoSwitchError(`${displayModelName(model)} is no longer in the model list, so it wasn't switched to.`);
            return;
        }
        if (isDownloading(m)) {
            pollsWithoutProgress.current = 0;
            return;
        }
        const progress = m.download_progress;
        if (progress?.state === 'error') {
            setPickAfterDownload(null);
            setAutoSwitchError(`Downloading ${displayModelName(model)} failed: ${progress.message ?? 'no reason given'}`);
            return;
        }
        // The model list can lag the finished download by a poll; "complete" already means downloaded.
        if (progress?.state !== 'complete' && modelState(m) === 'not_downloaded') {
            if (!missedPoll()) return;
            setPickAfterDownload(null);
            setAutoSwitchError(`The download of ${displayModelName(model)} never started. Try the download button again.`);
            return;
        }
        setPickAfterDownload(null);
        void choose(model, provider).then((failed) => {
            if (failed) setAutoSwitchError(`${displayModelName(model)} downloaded, but switching to it failed: ${failed}`);
        });
    }, [groups, pickAfterDownload, requestsPending, choose]);

    // The row Tab lands on: the last one focused, else the current model, else the first.
    const fireworksCollapsed = (provider: ProviderInfo, models: ProviderModel[]) =>
        provider.id === 'fireworks' && !showAllFireworksModels && models.some((m) => m.rank !== null);
    const fireworksVisibleModels = (provider: ProviderInfo, models: ProviderModel[]) =>
        fireworksCollapsed(provider, models)
            ? models.filter((m) => m.rank !== null || sameModelId(currentModel, m.id))
            : models;
    const fireworksHasHiddenModels = (provider: ProviderInfo, models: ProviderModel[]) =>
        fireworksCollapsed(provider, models)
        && models.some((m) => m.rank === null && !sameModelId(currentModel, m.id));
    const pickable = groups?.flatMap(({ provider, models }) => [
        ...(provider.remote && !provider.models_discovered ? [`key:${provider.id}`] : []),
        ...fireworksVisibleModels(provider, models)
            .filter((m) => switching === null && !isDownloading(m))
            .map((m) => m.id),
        ...(fireworksHasHiddenModels(provider, models) ? [`showall:${provider.id}`] : []),
    ]) ?? [];
    const tabRow = activeRow && pickable.includes(activeRow)
        ? activeRow
        : currentModel && pickable.includes(currentModel) ? currentModel : pickable[0];
    const rowProps = (row: string) => ({
        role: 'option' as const,
        tabIndex: row === tabRow ? 0 : -1,
        'data-row': row,
        onFocus: () => setActiveRow(row),
        onBlur: () => setInfoRow(null),
    });

    const Icon = place?.remote ? Cloud : place ? Cpu : HelpCircle;
    const name = chipModel ? displayModelName(chipModel) : 'Model';
    const label = `${name}${place ? ` · ${place.label}` : ''}`;
    const rowLabels = Object.fromEntries((groups ?? []).map((g) => [g.provider.id, distinctModelLabels(g.models)]));
    const shownGroups = groups && orderGroups(groups, active?.provider, currentModel);
    const notDownloaded = active?.downloaded === false && chipModel === active.model;
    const title = autoSwitchError
        ? autoSwitchError
        : queueError
        ? queueError
        : queued
            ? `${displayModelName(queued)} is used from your next message.`
            : active?.config_error
                ? active.config_error
                : notDownloaded
                    ? `${name} isn't downloaded yet. Open the list to download it.`
                    : !place
                        ? UNKNOWN_PLACE_TITLE
                        // The server writes the model name as Markdown code; a tooltip shows backticks literally.
                        : place.description?.replace(/`/g, '')
                            ?? (place.remote ? t('modelStatus.runsRemote', { name: place.label }) : t('modelStatus.runsHere'));

    return (
        <div className="composer-chip-wrap">
            <button
                ref={anchor}
                type="button"
                className={`composer-chip${notDownloaded || queueError || autoSwitchError ? ' is-warning' : ''}`}
                onClick={() => setOpen((v) => !v)}
                aria-haspopup="dialog"
                aria-expanded={open}
                title={title}
            >
                <Icon size={13} aria-hidden="true" />
                <span className="composer-chip-label" dir="auto">{label}</span>
                {queued && <span className="composer-chip-next">→ {displayModelName(queued)} next</span>}
                {notDownloaded && <Download size={12} aria-label="Not downloaded" />}
                {autoSwitchError && <AlertCircle size={12} aria-label="Model switch failed" />}
                <ChevronDown size={12} aria-hidden="true" />
            </button>
            <Popover open={open} onClose={close} anchor={anchor} label={t('modelMenu.label')} align="end" className="model-menu">
                {turnRunning && (
                    <div className="popover-message" role="status">
                        {chipModel
                            ? t('modelMenu.keepsModel', { model: displayModelName(chipModel) })
                            : t('modelMenu.keepsItsModel')}
                    </div>
                )}
                {autoSwitchError && <div className="popover-message is-error" role="alert">{autoSwitchError}</div>}
                {queueError && <div className="popover-message is-error" role="alert">{queueError}</div>}
                {error && <div className="popover-message is-error" role="alert">{error}</div>}
                {!groups && !error && (
                    <div className="popover-message"><Loader2 size={13} className="spin" /> {t('modelMenu.loading')}</div>
                )}
                {groups && (
                <div ref={list} role="listbox" aria-label={t('modelMenu.models')} onKeyDown={onListKey}>
                {shownGroups!.map(({ provider, models, error: groupError }) => (
                    <div key={provider.id} role="group" aria-labelledby={`${listId}-${provider.id}`}>
                        <div className="popover-heading" id={`${listId}-${provider.id}`}>{providerName(t, provider)}</div>
                        {groupError && <div className="popover-message is-error">{groupError}</div>}
                        {provider.key_state === KEY_WAITING ? (
                            <div className="popover-message" role="status">
                                <Loader2 size={13} className="spin" aria-hidden="true" /> {provider.key_message}
                            </div>
                        ) : provider.key_state === KEY_LOCKED ? (
                            <button
                                type="button"
                                className="popover-item"
                                {...rowProps(`unlock:${provider.id}`)}
                                aria-selected={false}
                                onClick={() => void retryKeyStore()}
                            >
                                <span className="popover-item-check"><KeyRound size={13} aria-hidden="true" /></span>
                                <span className="popover-item-body">
                                    <span className="popover-item-title">{t('settings.key.unlockAgain')}</span>
                                    <span className="popover-item-note">{t('settings.key.storeLocked')}</span>
                                </span>
                            </button>
                        ) : provider.remote && !provider.models_discovered && (
                            <button
                                type="button"
                                className="popover-item"
                                {...rowProps(`key:${provider.id}`)}
                                aria-selected={false}
                                onClick={() => { setOpen(false); openSettings('model', provider.id); }}
                            >
                                <span className="popover-item-check"><KeyRound size={13} aria-hidden="true" /></span>
                                <span className="popover-item-body">
                                    <span className="popover-item-title">{t('modelMenu.addKey')}</span>
                                    <span className="popover-item-note">{t('modelMenu.connectInSettings', { provider: provider.name })}</span>
                                </span>
                            </button>
                        )}
                        {!groupError && models.length === 0 && provider.models_discovered !== false && (
                            <div className="popover-message">{t('modelMenu.noModels')}</div>
                        )}
                        {fireworksVisibleModels(provider, models).map((m, i, visible) => {
                            const firstToDownload = !provider.remote
                                && toDownload(m, currentModel)
                                && i === visible.findIndex((v) => toDownload(v, currentModel));
                            const current = sameModelId(queued ?? active?.model, m.id);
                            const needsDownload = !provider.remote && modelState(m) === 'not_downloaded';
                            const modelName = rowLabels[provider.id][m.id];
                            const details = modelDetails(m, provider.remote);
                            const infoId = `${listId}-info-${provider.id}-${m.id}`;
                            return (
                                <Fragment key={m.id}>
                                    {firstToDownload && (
                                        <div className="popover-subheading">{t('modelMenu.availableToDownload')}</div>
                                    )}
                                    <div className="popover-row">
                                        <button
                                            type="button"
                                            className="popover-item"
                                            onClick={() => {
                                                if (!needsDownload) {
                                                    // A pick made since outranks one waiting on a download.
                                                    setPickAfterDownload(null);
                                                    setPendingDownload(null);
                                                    return void choose(m.id, provider);
                                                }
                                                // One question open at a time.
                                                setPendingCloud(null);
                                                setPendingDownload({ model: m.id, provider, pick: true });
                                            }}
                                            disabled={switching !== null || isDownloading(m)}
                                            {...rowProps(m.id)}
                                            aria-selected={current}
                                            aria-describedby={infoRow === m.id ? infoId : undefined}
                                        >
                                            <span className="popover-item-check" aria-hidden="true">
                                                {switching === m.id ? <Loader2 size={13} className="spin" /> : current ? <Check size={13} /> : null}
                                            </span>
                                            <span className="popover-item-body is-inline">
                                                <span className="popover-item-title is-model">{modelName}</span>
                                                {!provider.remote && m.size_gb ? (
                                                    <span className="popover-item-meta"><bdi dir="ltr">{m.size_gb} GB</bdi></span>
                                                ) : null}
                                            </span>
                                        </button>
                                        <InfoBubble
                                            label={`About ${modelName}`}
                                            warning={!!m.placement?.warning}
                                            id={infoId}
                                            pinned={infoRow === m.id}
                                            onUnpin={closeInfo}
                                            // The row's ArrowRight opens it, so the icon needs no Tab stop.
                                            tabIndex={-1}
                                        >
                                            <div className="info-bubble-title">{modelName}</div>
                                            {details.length > 0 && (
                                                <ul className="info-bubble-lines">
                                                    {details.map((d) => <li key={d}>{d}</li>)}
                                                </ul>
                                            )}
                                            {m.placement?.warning && (
                                                <ul className="info-bubble-lines">
                                                    <li className="is-warning">{m.placement.warning}</li>
                                                </ul>
                                            )}
                                            <div className="info-bubble-id">{m.id}</div>
                                        </InfoBubble>
                                        <span className="popover-row-state">
                                            {!provider.remote && (
                                                <StateMark
                                                    m={m}
                                                    busy={downloading !== null}
                                                    onDownload={() => { setPendingCloud(null); setPendingDownload({ model: m.id, provider, pick: false }); }}
                                                />
                                            )}
                                        </span>
                                    </div>
                                    {pendingDownload?.model === m.id && pendingDownload.provider.id === provider.id && (
                                        <div ref={consent} className="popover-message model-download-confirm" role="alert">
                                            <span>
                                                <MarkedText text={m.size_gb
                                                    ? t('modelMenu.downloadConfirm', { name: markLtr(modelName), size: markLtr(`${m.size_gb} GB`) })
                                                    : t('modelMenu.downloadConfirmNoSize', { name: markLtr(modelName) })} />
                                                {pendingDownload.pick && ` ${t('modelMenu.switchWhenDone')}`}
                                            </span>
                                            <span className="model-download-confirm-actions">
                                                <button
                                                    type="button"
                                                    className="btn-primary"
                                                    onClick={() => startDownload(m.id, provider, pendingDownload.pick)}
                                                >
                                                    {t('modelMenu.download')}
                                                </button>
                                                <button type="button" className="btn-secondary" onClick={() => setPendingDownload(null)}>
                                                    {t('modelMenu.cancel')}
                                                </button>
                                            </span>
                                        </div>
                                    )}
                                    {pendingCloud?.model === m.id && pendingCloud.provider.id === provider.id && (
                                        // Next to the clicked row, so a long list can't hide it.
                                        <div ref={consent} className="popover-message" role="alert">
                                            {providerNotice(t, pendingCloud.provider)}{' '}
                                            <button type="button" className="link-btn" onClick={() => choose(pendingCloud.model, pendingCloud.provider)}>
                                                {t('modelMenu.useModel', { name: displayModelName(pendingCloud.model) })}
                                            </button>
                                        </div>
                                    )}
                                </Fragment>
                            );
                        })}
                        {fireworksHasHiddenModels(provider, models) && (
                            <button
                                type="button"
                                className="popover-item"
                                {...rowProps(`showall:${provider.id}`)}
                                aria-selected={false}
                                onClick={() => {
                                    focusFirstShown.current = provider.id;
                                    setShowAllFireworksModels(true);
                                }}
                            >
                                <span className="popover-item-check" />
                                <span className="popover-item-body">
                                    <span className="popover-item-title">{t('settings.model.showAll')}</span>
                                </span>
                            </button>
                        )}
                    </div>
                ))}
                </div>
                )}
                {unavailable > 0 && (
                    <button
                        type="button"
                        className="popover-item"
                        onClick={() => { setOpen(false); openSettings('model'); }}
                    >
                        <span className="popover-item-check" />
                        <span className="popover-item-body">
                            <span className="popover-item-note">{t.plural('modelMenu.unavailable', unavailable)}</span>
                        </span>
                    </button>
                )}
                <div className="popover-divider" />
                <button
                    type="button"
                    className="popover-item"
                    onClick={() => { setOpen(false); openSettings('model'); }}
                >
                    <span className="popover-item-check"><Link2 size={13} aria-hidden="true" /></span>
                    <span className="popover-item-body">
                        <span className="popover-item-title">{t('modelMenu.addFromLink')}</span>
                        <span className="popover-item-note">{t('modelMenu.addFromLinkNote')}</span>
                    </span>
                </button>
                <button
                    type="button"
                    className="popover-item"
                    onClick={() => { setOpen(false); openSettings('model'); }}
                >
                    <span className="popover-item-check" />
                    <span className="popover-item-body"><span className="popover-item-title">{t('modelMenu.providers')}</span></span>
                </button>
            </Popover>
        </div>
    );
}
