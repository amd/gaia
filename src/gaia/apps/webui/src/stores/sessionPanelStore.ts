// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The session panel's layout (#5279): docked open or closed, its width, and
 * which sections are expanded — remembered across chats and reloads. The
 * overlay used on narrow windows is never remembered open.
 */

import { create } from 'zustand';
import { log } from '../utils/logger';

export type PanelSectionId = 'answer' | 'environment' | 'context' | 'outputs' | 'changes';

export const PANEL_MIN_WIDTH = 280;
export const PANEL_MAX_WIDTH = 640;
export const PANEL_DEFAULT_WIDTH = 320;

const OPEN_KEY = 'gaia.sessionPanel.open';
const WIDTH_KEY = 'gaia.sessionPanel.width';
const SECTIONS_KEY = 'gaia.sessionPanel.sections';

const DEFAULT_SECTIONS: Record<PanelSectionId, boolean> = {
    answer: true,
    environment: true,
    context: false,
    outputs: true,
    changes: true,
};

function read<T>(key: string, parse: (raw: string) => T, fallback: T): T {
    try {
        const raw = window.localStorage.getItem(key);
        return raw == null ? fallback : parse(raw);
    } catch (err) {
        log.ui.warn(`Session panel setting ${key} unavailable; using the default`, err);
        return fallback;
    }
}

function write(key: string, value: string): void {
    try {
        window.localStorage.setItem(key, value);
    } catch (err) {
        log.ui.warn(`Could not remember session panel setting ${key}`, err);
    }
}

export function clampWidth(width: number): number {
    const viewportCap = typeof window !== 'undefined' ? Math.floor(window.innerWidth * 0.5) : PANEL_MAX_WIDTH;
    return Math.round(Math.max(PANEL_MIN_WIDTH, Math.min(width, PANEL_MAX_WIDTH, Math.max(PANEL_MIN_WIDTH, viewportCap))));
}

interface SessionPanelState {
    /** Docked panel shown (wide windows). */
    open: boolean;
    /** Overlay shown (narrow windows); never remembered. */
    overlayOpen: boolean;
    width: number;
    sections: Record<PanelSectionId, boolean>;
    /** A section someone asked to see, e.g. the context meter's "Details". */
    focus: { section: PanelSectionId; nonce: number } | null;
    setOpen: (open: boolean) => void;
    setOverlayOpen: (open: boolean) => void;
    setWidth: (width: number) => void;
    setSection: (id: PanelSectionId, open: boolean) => void;
    reveal: (id: PanelSectionId) => void;
    /** Re-read remembered settings (tests, and a second window's changes). */
    reload: () => void;
}

function loadPersisted() {
    return {
        open: read(OPEN_KEY, (r) => r !== '0', true),
        width: clampWidth(read(WIDTH_KEY, (r) => Number(r) || PANEL_DEFAULT_WIDTH, PANEL_DEFAULT_WIDTH)),
        sections: {
            ...DEFAULT_SECTIONS,
            ...read<Partial<Record<PanelSectionId, boolean>>>(SECTIONS_KEY, (r) => JSON.parse(r), {}),
        },
    };
}

export const useSessionPanelStore = create<SessionPanelState>((set, get) => ({
    ...loadPersisted(),
    overlayOpen: false,
    focus: null,
    setOpen: (open) => {
        write(OPEN_KEY, open ? '1' : '0');
        set({ open });
    },
    setOverlayOpen: (overlayOpen) => set({ overlayOpen }),
    setWidth: (width) => {
        const w = clampWidth(width);
        write(WIDTH_KEY, String(w));
        set({ width: w });
    },
    setSection: (id, open) => {
        const sections = { ...get().sections, [id]: open };
        write(SECTIONS_KEY, JSON.stringify(sections));
        set({ sections });
    },
    reveal: (id) => {
        const sections = { ...get().sections, [id]: true };
        write(SECTIONS_KEY, JSON.stringify(sections));
        write(OPEN_KEY, '1');
        set({ sections, open: true, overlayOpen: true, focus: { section: id, nonce: Date.now() } });
    },
    reload: () => set({ ...loadPersisted(), overlayOpen: false, focus: null }),
}));
