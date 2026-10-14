// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect } from 'react';

/** Close a modal on Escape, without the key also stopping a streaming reply. */
export function useEscapeToClose(isOpen: boolean, onClose: () => void): void {
    useEffect(() => {
        if (!isOpen) return;
        const onKey = (e: KeyboardEvent) => {
            if (e.key !== 'Escape' || e.defaultPrevented) return;
            // ChatView's window listener stops the reply unless the key is already claimed.
            e.preventDefault();
            onClose();
        };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [isOpen, onClose]);
}
