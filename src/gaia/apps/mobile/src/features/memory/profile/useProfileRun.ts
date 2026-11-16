// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';

import type { DiscoveryFinding, InferenceInsight, ProfileStreamEvent } from '@/api/client.types';
import { GaiaApiError } from '@/api/errors';
import { useCommitProfile, useGaiaClient } from '@/data';
import { describeErrorWith, technicalDetail, useT } from '@/i18n/useT';

import type { MemoryNotice } from '../useMemoryNotice';

export type ProfileKind = 'discovery' | 'inference';
export type ProfilePhase = 'idle' | 'running' | 'review' | 'saving' | 'done';

export interface LogLine {
    text: string;
    kind: 'log' | 'finding' | 'error';
    /** The backend's own words, shown as sent. */
    server?: boolean;
}

export interface ReviewItem {
    content: string;
    confidence: number;
    /** The finding's source, or the insight's domain. */
    tag: string;
    checked: boolean;
    raw: DiscoveryFinding | InferenceInsight;
}

/** Runs one profile stream: nothing found is saved until `save` is called on the items the person left checked. */
export function useProfileRun(kind: ProfileKind, notify: (notice: MemoryNotice) => void) {
    const t = useT();
    const client = useGaiaClient();
    const commit = useCommitProfile();
    const [phase, setPhase] = useState<ProfilePhase>('idle');
    const [logs, setLogs] = useState<LogLine[]>([]);
    const [items, setItems] = useState<ReviewItem[]>([]);
    const [detail, setDetail] = useState<string | null>(null);
    const [savedCount, setSavedCount] = useState<number | null>(null);
    const controller = useRef<AbortController | null>(null);
    const found = useRef(0);

    useEffect(() => () => controller.current?.abort(), []);

    const pcOnly = useCallback(() => notify({ tone: 'info', text: t('mobileApp.settings.pcOnly') }), [notify, t]);

    const addLog = (line: LogLine) => setLogs((prev) => [...prev, line]);

    const start = useCallback(
        async (includeBrowser = false) => {
            if (!client) return;
            controller.current?.abort();
            const ctrl = new AbortController();
            controller.current = ctrl;
            setPhase('running');
            setLogs([]);
            setItems([]);
            setDetail(null);
            setSavedCount(null);
            found.current = 0;
            const connection = kind === 'discovery' ? 'mem.log.discoveryConnection' : 'mem.log.inferenceConnection';
            const done = kind === 'discovery' ? 'mem.log.discoveryDone' : 'mem.log.inferenceDone';
            const onEvent = (event: ProfileStreamEvent) => {
                if (event.type === 'log') addLog({ text: event.message, kind: 'log', server: true });
                else if (event.type === 'finding' || event.type === 'insight') {
                    const item = event.item;
                    found.current += 1;
                    // Bare: a bracketed English copy would repeat the whole item on every line.
                    addLog({ text: t.bare(event.type === 'finding' ? 'mem.log.found' : 'mem.log.insight', { content: item.content }), kind: 'finding' });
                    const tag = event.type === 'insight' ? event.item.domain : (event.item._source_name ?? event.item.category);
                    setItems((prev) => [...prev, { content: item.content, confidence: item.confidence, tag, checked: true, raw: item }]);
                } else if (event.type === 'error') addLog({ text: t.bare('mem.log.error', { message: event.message }), kind: 'error' });
                else if (event.type === 'done') addLog({ text: t(done, { n: event.total ?? 0 }), kind: 'log' });
            };
            try {
                const stream = kind === 'discovery' ? client.streamMemoryDiscovery({ signal: ctrl.signal }) : client.streamMemoryInference(includeBrowser, { signal: ctrl.signal });
                for await (const event of stream) {
                    if (ctrl.signal.aborted) return;
                    onEvent(event);
                }
                if (!ctrl.signal.aborted) setPhase('review');
            } catch (err) {
                if (ctrl.signal.aborted) return;
                if (err instanceof GaiaApiError && err.status === 403) {
                    pcOnly();
                    setPhase('idle');
                    return;
                }
                addLog({ text: t(connection), kind: 'error' });
                setDetail(technicalDetail(err));
                setPhase(found.current > 0 ? 'review' : 'idle');
            }
        },
        [client, kind, pcOnly, t],
    );

    const stop = useCallback(() => {
        controller.current?.abort();
        controller.current = null;
        setPhase(found.current > 0 ? 'review' : 'idle');
    }, []);

    const setChecked = useCallback((index: number | 'all' | 'none') => {
        setItems((prev) => prev.map((item, i) => ({ ...item, checked: index === 'all' ? true : index === 'none' ? false : i === index ? !item.checked : item.checked })));
    }, []);

    const save = useCallback(async () => {
        const kept = items.filter((item) => item.checked);
        if (kept.length === 0) return;
        setPhase('saving');
        try {
            const result =
                kind === 'discovery'
                    ? await commit.mutateAsync({ kind, items: kept.map((item) => item.raw as DiscoveryFinding) })
                    : await commit.mutateAsync({ kind, items: kept.map((item) => item.raw as InferenceInsight) });
            setSavedCount(result.stored);
            setPhase('done');
            notify({ tone: 'success', text: t(kind === 'discovery' ? 'mem.disc.saved' : 'mem.inf.saved', { n: result.stored }) });
        } catch (err) {
            setPhase('review');
            if (err instanceof GaiaApiError && err.status === 403) return pcOnly();
            const key = kind === 'discovery' ? 'mem.disc.saveFailed' : 'mem.inf.saveFailed';
            notify({ tone: 'danger', text: t.compose((say) => say(key, { error: describeErrorWith(err, say) })) });
        }
    }, [commit, items, kind, notify, pcOnly, t]);

    return { phase, logs, items, detail, savedCount, start, stop, setChecked, save };
}
