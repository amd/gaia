// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The session panel's layout (#5279): docked open or closed, its width, and
 * which sections are expanded — remembered across chats and reloads. The
 * overlay used on narrow windows is never remembered open. Closed until the
 * user opens it or a chat first makes a file (`autoOpen`).
 */

import { create } from 'zustand';
import { log } from '../utils/logger';

export type PanelSectionId =
    | 'slide' | 'answer' | 'environment' | 'context' | 'outputs' | 'changes' | 'skills' | 'tools' | 'session'
    | 'plan' | 'schedule' | 'documents' | 'commands';

/** What a chat's sections found, shared so the panel can tell what the chat is about without reading twice. */
export interface PanelSignals {
    /** Files the chat has changed on disk. */
    changedFiles?: number;
    /** How many of those are source code or project files. */
    changedCode?: number;
    /** Names of the skills it has loaded. */
    skills?: string[];
}

export const PANEL_MIN_WIDTH = 280;
export const PANEL_MAX_WIDTH = 640;
export const PANEL_DEFAULT_WIDTH = 320;

const OPEN_KEY = 'gaia.sessionPanel.open';
const WIDTH_KEY = 'gaia.sessionPanel.width';
const SECTIONS_KEY = 'gaia.sessionPanel.sections';

const DEFAULT_SECTIONS: Record<PanelSectionId, boolean> = {
    slide: true,
    answer: true,
    environment: true,
    context: false,
    outputs: true,
    changes: true,
    skills: true,
    tools: false,
    session: false,
    plan: true,
    schedule: true,
    documents: true,
    commands: true,
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
    /** The user closed the docked panel and that choice is remembered; nothing opens it for them. */
    closedByUser: boolean;
    /** Overlay shown (narrow windows); never remembered. */
    overlayOpen: boolean;
    width: number;
    sections: Record<PanelSectionId, boolean>;
    /** A section someone asked to see, e.g. the context meter's "Details". */
    focus: { section: PanelSectionId; nonce: number } | null;
    signals: Record<string, PanelSignals>;
    setSignals: (sessionId: string, patch: PanelSignals) => void;
    setOpen: (open: boolean) => void;
    /** Open the docked panel for the user without remembering it as their choice. */
    autoOpen: () => void;
    /** Shown by `autoOpen`, not by the user; giving the side to another pane then remembers nothing. */
    autoOpened: boolean;
    /** Give the right side to another pane: an auto-opened panel just hides, a user-opened one closes as before. */
    yieldSide: () => void;
    setOverlayOpen: (open: boolean) => void;
    setWidth: (width: number) => void;
    setSection: (id: PanelSectionId, open: boolean) => void;
    reveal: (id: PanelSectionId) => void;
    /** Re-read remembered settings (tests, and a second window's changes). */
    reload: () => void;
}

function loadPersisted() {
    const stored = read<string | null>(OPEN_KEY, (r) => r, null);
    return {
        open: stored === '1',
        closedByUser: stored === '0',
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
    signals: {},
    setSignals: (sessionId, patch) => set((s) => ({ signals: { ...s.signals, [sessionId]: { ...s.signals[sessionId], ...patch } } })),
    autoOpened: false,
    setOpen: (open) => {
        write(OPEN_KEY, open ? '1' : '0');
        set({ open, closedByUser: !open, autoOpened: false });
    },
    autoOpen: () => {
        if (get().closedByUser || get().open) return;
        set({ open: true, autoOpened: true });
    },
    yieldSide: () => {
        if (get().autoOpened) set({ open: false, autoOpened: false });
        else get().setOpen(false);
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
        set({ sections, open: true, closedByUser: false, autoOpened: false, overlayOpen: true, focus: { section: id, nonce: Date.now() } });
    },
    reload: () => set({ ...loadPersisted(), autoOpened: false, overlayOpen: false, focus: null, signals: {} }),
}));

/** How long a request to show a section stays one: long enough for the panel to open, not to haunt a later render. */
export const FOCUS_FRESH_MS = 2000;

/** Whether *focus* was just asked for, as opposed to left over from an earlier click. */
export function isFreshFocus(focus: { nonce: number } | null): boolean {
    return focus !== null && Date.now() - focus.nonce < FOCUS_FRESH_MS;
}
