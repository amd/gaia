// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import * as api from '../services/api';
import { log } from '../utils/logger';
import type { ContextUsage } from '../types';

/** How full the chat's context window is, for the composer's meter; null when it can't be measured. */
export function useContextUsage(sessionId: string, isStale: () => boolean) {
    const [contextUsage, setContextUsage] = useState<ContextUsage | null>(null);

    useEffect(() => {
        setContextUsage(null);
    }, [sessionId]);

    const refreshContext = useCallback(() => {
        api.getContextUsage(sessionId)
            .then((r) => {
                if (isStale()) return;
                setContextUsage(r.available ? r : null);
            })
            .catch((err) => log.chat.error(`Could not measure the context for session=${sessionId}`, err));
    }, [sessionId, isStale]);
    useEffect(() => { refreshContext(); }, [refreshContext]);

    /** A measurement the running turn streamed. */
    const showContextUsage = useCallback((usage: ContextUsage) => {
        setContextUsage(usage);
    }, []);

    return { contextUsage, refreshContext, showContextUsage };
}
