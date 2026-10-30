// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import * as api from '../services/api';
import { log } from '../utils/logger';

/** Download the chat as Markdown, with a plain-language error when that fails. */
export function useChatExport(sessionId: string, title: string | undefined) {
    const [exportError, setExportError] = useState<string | null>(null);

    useEffect(() => {
        setExportError(null);
    }, [sessionId]);

    const clearExportError = useCallback(() => setExportError(null), []);

    const exportChat = useCallback(async () => {
        setExportError(null);
        try {
            const data = await api.exportSession(sessionId);
            const blob = new Blob([data.content], { type: 'text/markdown' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            // Sanitize title for use as filename: remove path separators and special chars
            const safeTitle = (title || 'chat').replace(/[/\\:*?"<>|]/g, '_').slice(0, 100);
            a.download = `${safeTitle}.md`;
            a.click();
            URL.revokeObjectURL(url);
        } catch (err) {
            log.chat.error('Export failed', err);
            setExportError(err instanceof api.ApiError
                ? `Couldn't export this chat: GAIA's backend returned an error (HTTP ${err.httpStatus}). Try Export again; if it keeps failing, check the backend log.`
                : "Couldn't export this chat because GAIA's backend isn't reachable. Check that GAIA is running, then try Export again.");
        }
    }, [sessionId, title]);

    return { exportChat, exportError, clearExportError };
}
