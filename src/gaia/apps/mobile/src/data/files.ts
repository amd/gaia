// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import type { BrowseResponse, DocumentResponse, FileSearchResponse } from '@/api/client.types';

import { useGaiaClient } from './GaiaClientProvider';
import { queryKeys, requireClient } from './queries';

/** Most results one search asks the PC for, as the web browser does. */
export const FILE_SEARCH_LIMIT = 30;

/** A folder on the PC; no `path` opens the chat's first folder. */
export function useBrowseFiles(sessionId: string, path: string | undefined) {
    const client = useGaiaClient();
    return useQuery<BrowseResponse>({
        queryKey: ['fileBrowse', sessionId, path ?? ''],
        enabled: client !== null,
        retry: false,
        queryFn: ({ signal }) => requireClient(client).browseFiles(sessionId, path, { signal }),
    });
}

/** Looks for files by name once `query` is set; `fileTypes` is a comma list of extensions. */
export function useSearchFiles(sessionId: string, query: string | null, fileTypes: string) {
    const client = useGaiaClient();
    return useQuery<FileSearchResponse>({
        queryKey: ['fileSearch', sessionId, query, fileTypes],
        enabled: client !== null && query !== null,
        retry: false,
        queryFn: ({ signal }) => requireClient(client).searchFiles(sessionId, query ?? '', fileTypes || undefined, FILE_SEARCH_LIMIT, { signal }),
    });
}

export interface AddPcFilesResult {
    added: DocumentResponse[];
    /** A file GAIA couldn't read has a library row with its reason; it isn't attached. */
    unreadable: DocumentResponse[];
    failures: { path: string; error: Error }[];
}

/** `mutate({ paths, chatId })` indexes PC files into the library and attaches them to the chat, as the web browser's Add to library does. */
export function useAddPcFiles() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<AddPcFilesResult, Error, { paths: string[]; chatId: string }>({
        mutationFn: async ({ paths, chatId }) => {
            const api = requireClient(client);
            const result: AddPcFilesResult = { added: [], unreadable: [], failures: [] };
            for (const path of paths) {
                try {
                    const document = await api.uploadDocumentByPath(path, chatId);
                    if (document.indexing_status === 'failed') {
                        result.unreadable.push(document);
                        continue;
                    }
                    await api.attachDocument(chatId, document.id);
                    result.added.push(document);
                } catch (err) {
                    result.failures.push({ path, error: err instanceof Error ? err : new Error(String(err)) });
                }
            }
            return result;
        },
        onSettled: (_result, _error, { chatId }) => {
            void queryClient.invalidateQueries({ queryKey: queryKeys.documents });
            void queryClient.invalidateQueries({ queryKey: queryKeys.session(chatId) });
            void queryClient.invalidateQueries({ queryKey: queryKeys.sessions });
        },
    });
}
