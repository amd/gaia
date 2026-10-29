// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import * as api from '../services/api';
import { log } from '../utils/logger';
import type { ContextUsage } from '../types';

/** How full the chat's context window is, for the composer's meter. */
export function useContextUsage(sessionId: string, isStale: () => boolean) {
    const [contextUsage, setContextUsage] = useState<ContextUsage | null>(null);
    const [contextUnavailable, setContextUnavailable] = useState<string | null>(null);

    useEffect(() => {
        setContextUsage(null);
        setContextUnavailable(null);
    }, [sessionId]);

    const refreshContext = useCallback(() => {
        api.getContextUsage(sessionId)
            .then((r) => {
                if (isStale()) return;
                if (r.available) {
                    setContextUsage(r);
                    setContextUnavailable(null);
                } else {
                    setContextUsage(null);
                    setContextUnavailable(r.reason);
                }
            })
            .catch((err) => log.chat.error(`Could not measure the context for session=${sessionId}`, err));
    }, [sessionId, isStale]);
    useEffect(() => { refreshContext(); }, [refreshContext]);

    /** A measurement the running turn streamed. */
    const showContextUsage = useCallback((usage: ContextUsage) => {
        setContextUsage(usage);
        setContextUnavailable(null);
    }, []);

    return { contextUsage, contextUnavailable, refreshContext, showContextUsage };
}
