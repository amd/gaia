// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, type ReactNode } from 'react';

import { FilePreviewHost } from './FilePreview';
import { usePreviewStore } from './previewStore';
import { FileScopeContext } from './scopeContext';

/** Puts a chat's id in scope for the file links below it, and hosts the preview they open. */
export function FileScope({ sessionId, children }: { sessionId: string; children: ReactNode }) {
    // A preview belongs to the chat that opened it.
    useEffect(() => () => usePreviewStore.getState().close(), [sessionId]);
    return (
        <FileScopeContext.Provider value={sessionId}>
            {children}
            <FilePreviewHost />
        </FileScopeContext.Provider>
    );
}
