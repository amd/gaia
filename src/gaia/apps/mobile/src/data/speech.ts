// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import type { SpeechSettings, SpeechSettingsUpdate } from '@/api/client.types';

import { useGaiaClient } from './GaiaClientProvider';
import { requireClient } from './queries';

export const speechKeys = {
    settings: ['speech', 'settings'] as const,
};

/** The read-aloud voice, speed and auto switch; the terminal app shares them, so each screen re-reads them on mount. */
export function useSpeechSettings() {
    const client = useGaiaClient();
    return useQuery<SpeechSettings>({
        queryKey: speechKeys.settings,
        enabled: client !== null,
        queryFn: ({ signal }) => requireClient(client).getSpeechSettings({ signal }),
    });
}

export function useUpdateSpeechSettings() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<SpeechSettings, Error, SpeechSettingsUpdate>({
        mutationFn: (change) => requireClient(client).updateSpeechSettings(change),
        onSuccess: (saved) => queryClient.setQueryData(speechKeys.settings, saved),
    });
}
