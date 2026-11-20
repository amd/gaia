// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useId, type ReactNode } from 'react';
import { sheetStore } from './sheetStore';

export interface SheetProps {
    open: boolean;
    onClose: () => void;
    title?: string;
    children?: ReactNode;
    footer?: ReactNode;
    /** Whether the handle, the scrim, Android back and the escape gesture close it. Default true. */
    dismissible?: boolean;
}

// Renders nothing in place: the content shows in the SheetHost, one sheet at a time.
export function Sheet({ open, onClose, title, children, footer, dismissible = true }: SheetProps) {
    const id = useId();
    useEffect(() => {
        if (open) sheetStore.present({ id, open: true, onClose, title, children, footer, dismissible });
        else sheetStore.release(id);
    });
    useEffect(() => () => sheetStore.release(id), [id]);
    return null;
}
