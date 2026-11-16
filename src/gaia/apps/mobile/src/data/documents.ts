// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { AppState } from 'react-native';

import { useMutation, useQueryClient, type QueryClient } from '@tanstack/react-query';

import { INDEX_POLL_MS, REMOVE_UNDO_MS, isIndexing } from '@webui/utils/documentLibrary.core';

import type {
    CancelIndexingResult,
    DocumentListResponse,
    DocumentResponse,
    IndexProgress,
} from '@/api/client.types';

import { useGaiaClient } from './GaiaClientProvider';
import { queryKeys, requireClient } from './queries';

type LibraryDoc = DocumentListResponse['documents'][number];

/** Stops a document's indexing: `mutate(docId)`. The library is read again so the row shows Cancelled. */
export function useCancelIndexing() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<CancelIndexingResult, Error, string>({
        mutationFn: (docId) => requireClient(client).cancelDocumentIndexing(docId),
        onSettled: () => queryClient.invalidateQueries({ queryKey: queryKeys.documents }),
    });
}

/** Indexes a document again: `mutate(docId)`. The library is read again either way, so a second failure shows its new reason. */
export function useReindexDocument() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<DocumentResponse, Error, string>({
        mutationFn: (docId) => requireClient(client).reindexDocument(docId),
        onSettled: () => queryClient.invalidateQueries({ queryKey: queryKeys.documents }),
    });
}

function dropDocument(queryClient: QueryClient, docId: string): void {
    queryClient.setQueryData<DocumentListResponse>(queryKeys.documents, (list) =>
        list && { ...list, documents: list.documents.filter((d) => d.id !== docId), total: list.total - 1 },
    );
}

export interface PendingRemoval {
    id: string;
    name: string;
}

/**
 * Removing a document waits REMOVE_UNDO_MS for Undo before the DELETE goes out. A newer removal, leaving the
 * screen or sending the app to the background sends the waiting one at once; only Undo cancels it.
 */
export function useDocumentRemoval(onFailed: (removal: PendingRemoval, error: unknown) => void) {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    const [pending, setPending] = useState<PendingRemoval | null>(null);
    const pendingRef = useRef<PendingRemoval | null>(null);
    const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const failed = useRef(onFailed);
    useEffect(() => {
        failed.current = onFailed;
    });

    const commit = useCallback(
        async (removal: PendingRemoval) => {
            try {
                await requireClient(client).deleteDocument(removal.id);
            } catch (error) {
                setPending((p) => (p?.id === removal.id ? null : p));
                failed.current(removal, error);
                return;
            }
            dropDocument(queryClient, removal.id);
            setPending((p) => (p?.id === removal.id ? null : p));
        },
        [client, queryClient],
    );

    const flush = useCallback(() => {
        if (timer.current !== null) clearTimeout(timer.current);
        timer.current = null;
        const waiting = pendingRef.current;
        pendingRef.current = null;
        if (waiting) void commit(waiting);
    }, [commit]);

    const remove = useCallback(
        (removal: PendingRemoval) => {
            flush();
            pendingRef.current = removal;
            setPending(removal);
            timer.current = setTimeout(flush, REMOVE_UNDO_MS);
        },
        [flush],
    );

    const undo = useCallback(() => {
        if (timer.current !== null) clearTimeout(timer.current);
        timer.current = null;
        pendingRef.current = null;
        setPending(null);
    }, []);

    const flushRef = useRef(flush);
    useEffect(() => {
        flushRef.current = flush;
    }, [flush]);
    useEffect(() => {
        const sub = AppState.addEventListener('change', (state) => {
            if (state !== 'active') flushRef.current();
        });
        return () => {
            sub.remove();
            flushRef.current();
        };
    }, []);

    return { pending, remove, undo };
}

export interface IndexingView {
    /** Progress by document, while the PC reports it. */
    progress: Record<string, IndexProgress>;
    /** When each indexing row was first seen indexing, as epoch ms. */
    startedAt: Record<string, number>;
    /** Updates every poll so elapsed times tick; a row shows no time until the first poll. */
    now: number;
}

/**
 * Asks for the status of every indexing or pending row each INDEX_POLL_MS while the app is in the foreground,
 * and reads the library again once one has finished.
 */
export function useIndexingView(documents: readonly LibraryDoc[]): IndexingView {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    const [progress, setProgress] = useState<Record<string, IndexProgress>>({});
    const [startedAt, setStartedAt] = useState<Record<string, number>>({});
    const [now, setNow] = useState(() => Date.now());
    const active = documents.filter((d) => isIndexing(d.indexing_status)).map((d) => d.id);
    const key = active.join('\n');

    useEffect(() => {
        const ids = key ? key.split('\n') : [];
        if (ids.length === 0 || !client) return undefined;

        let polling = false;
        const timer = setInterval(async () => {
            const stamp = Date.now();
            setNow(stamp);
            setStartedAt((m) => (ids.every((id) => id in m) && Object.keys(m).length === ids.length ? m : Object.fromEntries(ids.map((id) => [id, m[id] ?? stamp]))));
            if (polling || AppState.currentState !== 'active') return;
            polling = true;
            try {
                // A missed poll is asked again next tick.
                const statuses = await Promise.all(ids.map((id) => client.getDocumentStatus(id).catch(() => null)));
                setProgress((m) => {
                    const next = Object.fromEntries(Object.entries(m).filter(([id]) => ids.includes(id)));
                    statuses.forEach((s, i) => {
                        if (s?.progress) next[ids[i]!] = s.progress;
                        else if (s) delete next[ids[i]!];
                    });
                    return next;
                });
                if (statuses.some((s) => s && !isIndexing(s.indexing_status))) {
                    await queryClient.invalidateQueries({ queryKey: queryKeys.documents });
                }
            } finally {
                polling = false;
            }
        }, INDEX_POLL_MS);
        return () => clearInterval(timer);
    }, [key, client, queryClient]);

    return { progress, startedAt, now };
}
