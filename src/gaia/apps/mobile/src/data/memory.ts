// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import type {
    ConsolidateMemoryResult,
    DiscoveryFinding,
    EmbeddingCoverage,
    InferenceInsight,
    KnowledgeCreateBody,
    KnowledgeListParams,
    KnowledgeSaveResult,
    KnowledgeUpdateBody,
    MemoryActivityDay,
    MemoryConversation,
    MemoryEntity,
    MemoryKnowledgeEntry,
    MemoryKnowledgeItem,
    MemoryCleared,
    MemoryReinitializeResult,
    MemorySettings,
    MemorySettingsUpdateResponse,
    MemoryStats,
    MemoryToolStat,
    MemoryTurn,
    MemoryUpcomingItem,
    RebuildEmbeddingsResult,
    ReconcileMemoryResult,
    RefreshSystemContextResult,
} from '@/api/client.types';

import { ABOUT_PAGE_SIZE } from '@webui/utils/memoryFormat.core';

import { useGaiaClient } from './GaiaClientProvider';
import { requireClient } from './queries';

/** Every memory query starts with `memoryKeys.all`, so one invalidation re-reads them all. */
export const memoryKeys = {
    all: ['memory'] as const,
    stats: ['memory', 'stats'] as const,
    settings: ['memory', 'settings'] as const,
    coverage: ['memory', 'coverage'] as const,
    activity: (days: number) => ['memory', 'activity', days] as const,
    tools: ['memory', 'tools'] as const,
    knowledge: (want: number) => ['memory', 'knowledge', want] as const,
    browse: (params: KnowledgeListParams) => ['memory', 'browse', params] as const,
    entities: ['memory', 'entities'] as const,
    conversations: (limit: number) => ['memory', 'conversations', limit] as const,
    conversationSearch: (query: string) => ['memory', 'conversation-search', query] as const,
    conversation: (sessionId: string) => ['memory', 'conversation', sessionId] as const,
    upcoming: (days: number) => ['memory', 'upcoming', days] as const,
};

export function useMemoryStats() {
    const client = useGaiaClient();
    return useQuery<MemoryStats>({
        queryKey: memoryKeys.stats,
        enabled: client !== null,
        queryFn: ({ signal }) => requireClient(client).getMemoryStats({ signal }),
    });
}

/** Whether memory is on (`memory_enabled`), and the other two memory switches. */
export function useMemorySettings() {
    const client = useGaiaClient();
    return useQuery<MemorySettings>({
        queryKey: memoryKeys.settings,
        enabled: client !== null,
        queryFn: ({ signal }) => requireClient(client).getMemorySettings({ signal }),
    });
}

/** Turns a memory switch on or off: `mutate({ memory_enabled: false })`. */
export function useUpdateMemorySettings() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<MemorySettingsUpdateResponse, Error, Partial<MemorySettings>>({
        mutationFn: (settings) => requireClient(client).updateMemorySettings(settings),
        onSettled: () => queryClient.invalidateQueries({ queryKey: memoryKeys.all }),
    });
}

/** Deletes every memory: `mutate()`. Re-reads the memory queries afterwards. */
export function useClearAllMemory() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<MemoryCleared, Error, void>({
        mutationFn: () => requireClient(client).clearAllMemory(),
        onSettled: () => queryClient.invalidateQueries({ queryKey: memoryKeys.all }),
    });
}

/** Deletes every memory, then re-collects this PC's facts: `mutate()`. */
export function useReinitializeMemory() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<MemoryReinitializeResult, Error, void>({
        mutationFn: () => requireClient(client).reinitializeMemory(),
        onSettled: () => queryClient.invalidateQueries({ queryKey: memoryKeys.all }),
    });
}

export function useEmbeddingCoverage() {
    const client = useGaiaClient();
    return useQuery<EmbeddingCoverage>({
        queryKey: memoryKeys.coverage,
        enabled: client !== null,
        queryFn: ({ signal }) => requireClient(client).getEmbeddingCoverage({ signal }),
    });
}

/** The last `days` days of activity. */
export function useMemoryActivity(days: number) {
    const client = useGaiaClient();
    return useQuery<MemoryActivityDay[]>({
        queryKey: memoryKeys.activity(days),
        enabled: client !== null,
        queryFn: ({ signal }) => requireClient(client).getMemoryActivity(days, { signal }),
    });
}

export function useMemoryTools() {
    const client = useGaiaClient();
    return useQuery<MemoryToolStat[]>({
        queryKey: memoryKeys.tools,
        enabled: client !== null,
        queryFn: ({ signal }) => requireClient(client).getMemoryTools({ signal }),
    });
}

export function useMemoryUpcoming(days: number) {
    const client = useGaiaClient();
    return useQuery<MemoryUpcomingItem[]>({
        queryKey: memoryKeys.upcoming(days),
        enabled: client !== null,
        queryFn: ({ signal }) => requireClient(client).getMemoryUpcoming(days, { signal }),
    });
}

/** Saves the findings or insights the person kept, then re-reads every memory query. */
export function useCommitProfile() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<{ stored: number }, Error, { kind: 'discovery'; items: DiscoveryFinding[] } | { kind: 'inference'; items: InferenceInsight[] }>({
        mutationFn: (commit) =>
            commit.kind === 'discovery' ? requireClient(client).commitMemoryDiscovery(commit.items) : requireClient(client).commitMemoryInference(commit.items),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: memoryKeys.all }),
    });
}

/** Re-reads every memory query; resolves when the ones on screen have answered. */
export function useRefreshMemory(): () => Promise<void> {
    const queryClient = useQueryClient();
    return () => queryClient.invalidateQueries({ queryKey: memoryKeys.all });
}

export type MemoryMaintenanceAction = 'consolidate' | 'rebuildEmbeddings' | 'reconcile' | 'refreshSystemContext' | 'rebuildFts';

export type MemoryMaintenanceOutcome =
    | { action: 'consolidate'; result: ConsolidateMemoryResult }
    | { action: 'rebuildEmbeddings'; result: RebuildEmbeddingsResult }
    | { action: 'reconcile'; result: ReconcileMemoryResult }
    | { action: 'refreshSystemContext'; result: RefreshSystemContextResult }
    | { action: 'rebuildFts'; result: { status: string } };

/** Runs one maintenance POST: `mutate(action)`. It re-reads the memory queries afterwards, as the web does. */
export function useMemoryMaintenance() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<MemoryMaintenanceOutcome, Error, MemoryMaintenanceAction>({
        mutationFn: async (action) => {
            const c = requireClient(client);
            switch (action) {
                case 'consolidate':
                    return { action, result: await c.consolidateMemory() };
                case 'rebuildEmbeddings':
                    return { action, result: await c.rebuildEmbeddings() };
                case 'reconcile':
                    return { action, result: await c.reconcileMemory() };
                case 'refreshSystemContext':
                    return { action, result: await c.refreshSystemContext() };
                case 'rebuildFts':
                    return { action, result: await c.rebuildMemoryFts() };
            }
        },
        onSuccess: (outcome) =>
            queryClient.invalidateQueries({ queryKey: outcome.action === 'rebuildEmbeddings' ? memoryKeys.coverage : memoryKeys.all }),
    });
}

export interface MemoryKnowledgeResult {
    items: MemoryKnowledgeItem[];
    /** Every memory the PC holds for this tab, past the rows read. */
    total: number;
}

/** The first `want` memories about the person, newest first, read page by page as the web does. */
export function useMemoryKnowledge(want: number) {
    const client = useGaiaClient();
    return useQuery<MemoryKnowledgeResult>({
        queryKey: memoryKeys.knowledge(want),
        enabled: client !== null,
        placeholderData: keepPreviousData,
        queryFn: async ({ signal }) => {
            const c = requireClient(client);
            const rows: MemoryKnowledgeItem[] = [];
            const seen = new Set<string>();
            let offset = 0;
            let found = 0;
            for (;;) {
                const limit = Math.min(ABOUT_PAGE_SIZE, want - rows.length);
                const page = await c.listMemoryKnowledge(
                    { about_user: true, include_sensitive: true, sort_by: 'updated_at', order: 'desc', offset, limit },
                    { signal },
                );
                offset += page.items.length;
                // A memory saved between two pages shifts the rows, repeating one at the page edge.
                for (const item of page.items) {
                    if (seen.has(item.id)) continue;
                    seen.add(item.id);
                    rows.push(item);
                }
                found = typeof page.total === 'number' ? page.total : rows.length;
                if (page.items.length < limit || rows.length >= Math.min(want, found)) break;
            }
            return { items: rows, total: found };
        },
    });
}

/** Saves a memory (also what Undo does after a forget). */
export function useAddMemory() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<KnowledgeSaveResult, Error, KnowledgeCreateBody>({
        mutationFn: (body) => requireClient(client).addMemoryKnowledge(body),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: memoryKeys.all }),
    });
}

export function useEditMemory() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<KnowledgeSaveResult, Error, { id: string; body: KnowledgeUpdateBody }>({
        mutationFn: ({ id, body }) => requireClient(client).updateMemoryKnowledge(id, body),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: memoryKeys.all }),
    });
}

export function useForgetMemory() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<unknown, Error, string>({
        mutationFn: (id) => requireClient(client).deleteMemoryKnowledge(id),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: memoryKeys.all }),
    });
}

/** One page of the Advanced tab's knowledge browser, filtered as `params` say. */
export function useKnowledgeBrowse(params: KnowledgeListParams) {
    const client = useGaiaClient();
    return useQuery<{ items: MemoryKnowledgeEntry[]; total: number }>({
        queryKey: memoryKeys.browse(params),
        enabled: client !== null,
        placeholderData: keepPreviousData,
        queryFn: async ({ signal }) => {
            const page = await requireClient(client).listMemoryKnowledge(params, { signal });
            return { items: page.items, total: typeof page.total === 'number' ? page.total : page.items.length };
        },
    });
}

export function useMemoryEntities() {
    const client = useGaiaClient();
    return useQuery<MemoryEntity[]>({
        queryKey: memoryKeys.entities,
        enabled: client !== null,
        queryFn: ({ signal }) => requireClient(client).listMemoryEntities({ signal }),
    });
}

/** The most recent `limit` conversations in memory. */
export function useMemoryConversations(limit: number) {
    const client = useGaiaClient();
    return useQuery<MemoryConversation[]>({
        queryKey: memoryKeys.conversations(limit),
        enabled: client !== null,
        queryFn: ({ signal }) => requireClient(client).listMemoryConversations(limit, { signal }),
    });
}

/** Turns matching `query`; idle while it is blank. */
export function useConversationSearch(query: string, limit: number) {
    const client = useGaiaClient();
    const q = query.trim();
    return useQuery<MemoryTurn[]>({
        queryKey: memoryKeys.conversationSearch(q),
        enabled: client !== null && q !== '',
        queryFn: ({ signal }) => requireClient(client).searchMemoryConversations(q, limit, { signal }),
    });
}

/** One conversation's turns; idle until a conversation is chosen. */
export function useMemoryConversation(sessionId: string | null) {
    const client = useGaiaClient();
    return useQuery<MemoryTurn[]>({
        queryKey: memoryKeys.conversation(sessionId ?? ''),
        enabled: client !== null && sessionId !== null,
        queryFn: ({ signal }) => requireClient(client).getMemoryConversation(sessionId!, { signal }),
    });
}
