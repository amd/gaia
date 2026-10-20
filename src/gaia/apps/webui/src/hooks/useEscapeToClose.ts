// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect } from 'react';

/** Mark an inline field or menu whose Escape must not reach the page-wide handlers. */
export const CLAIMS_ESCAPE = { 'data-claims-escape': '' } as const;

let openLayers = 0;

/**
 * True when Escape belongs to something other than the page: an open dialog,
 * menu or popover, a field marked with CLAIMS_ESCAPE, or a handler that already
 * called preventDefault. Stopping the reply and denying a tool both check this,
 * so they no longer depend on which listener happens to run first.
 */
export function isEscapeClaimed(e: KeyboardEvent): boolean {
    if (e.defaultPrevented || openLayers > 0) return true;
    const target = e.target;
    return target instanceof Element && target.closest('[data-claims-escape]') !== null;
}

/** Register an open overlay so Escape closes it instead of stopping the reply. */
export function useEscapeLayer(isOpen: boolean): void {
    useEffect(() => {
        if (!isOpen) return;
        openLayers += 1;
        return () => { openLayers -= 1; };
    }, [isOpen]);
}

/** Close a modal on Escape, without the key also stopping a streaming reply. */
export function useEscapeToClose(isOpen: boolean, onClose: () => void, enabled = true): void {
    useEscapeLayer(isOpen);
    useEffect(() => {
        if (!isOpen || !enabled) return;
        const onKey = (e: KeyboardEvent) => {
            if (e.key !== 'Escape' || e.defaultPrevented) return;
            e.preventDefault();
            onClose();
        };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [isOpen, enabled, onClose]);
}
