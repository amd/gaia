// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ReactNode } from 'react';

export interface SheetEntry {
    id: string;
    open: boolean;
    onClose: () => void;
    title?: string;
    children?: ReactNode;
    footer?: ReactNode;
    dismissible: boolean;
}

export interface SheetState {
    /** The sheet on screen, including while it animates out. */
    active?: string;
    /** Sheets that asked to open and are waiting for the active one to go. */
    queue: string[];
    entries: Record<string, SheetEntry>;
}

const EMPTY: SheetState = { queue: [], entries: {} };

let state: SheetState = EMPTY;
const listeners = new Set<() => void>();

function set(next: SheetState) {
    state = next;
    listeners.forEach((l) => l());
}

function promote(s: SheetState): SheetState {
    if (s.active || s.queue.length === 0) return s;
    const [active, ...queue] = s.queue;
    return { ...s, active, queue };
}

export const sheetStore = {
    subscribe(listener: () => void) {
        listeners.add(listener);
        return () => {
            listeners.delete(listener);
        };
    },
    getState: () => state,

    /** A `Sheet` with `open` set: shows it now, or after the sheet on screen has fully gone. */
    present(entry: SheetEntry) {
        const known = state.entries[entry.id];
        const entries = { ...state.entries, [entry.id]: entry };
        if (known) return set({ ...state, entries });
        set(promote({ ...state, entries, queue: [...state.queue, entry.id] }));
    },

    /** A `Sheet` closed or unmounted. The active one keeps its place until `finished`. */
    release(id: string) {
        const entry = state.entries[id];
        if (!entry) return;
        if (state.active === id) {
            if (entry.open) set({ ...state, entries: { ...state.entries, [id]: { ...entry, open: false } } });
            return;
        }
        const { [id]: _gone, ...entries } = state.entries;
        set({ ...state, entries, queue: state.queue.filter((q) => q !== id) });
    },

    /** The active sheet has finished animating out: the next one may open. */
    finished(id: string) {
        if (state.active !== id) return;
        const { [id]: _gone, ...entries } = state.entries;
        set(promote({ ...state, entries, active: undefined }));
    },

    reset() {
        set(EMPTY);
    },
};
