// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The browser pane in the renderer: a read-only mirror of the PaneState that
 * Electron main pushes, plus the layout the renderer owns (open, width),
 * remembered like the session panel's. The pane and the docked session panel
 * share the right side, so opening one closes the other.
 */

import { create } from 'zustand';
import { log } from '../utils/logger';
import { useSessionPanelStore } from './sessionPanelStore';
import type { GaiaBrowserPaneAPI, PaneState } from '../../services/browser-pane/contract';

export const PANE_MIN_WIDTH = 360;
export const PANE_MAX_WIDTH = 1200;
export const PANE_DEFAULT_WIDTH = 520;

const OPEN_KEY = 'gaia.browserPane.open';
const WIDTH_KEY = 'gaia.browserPane.width';

/** The pane exists only in the desktop app, where preload exposes this. */
export function browserPaneApi(): GaiaBrowserPaneAPI | undefined {
    return typeof window === 'undefined' ? undefined : window.gaiaBrowserPane;
}

function read(key: string): string | null {
    try {
        return window.localStorage.getItem(key);
    } catch (err) {
        log.ui.warn(`Browser pane setting ${key} unavailable; using the default`, err);
        return null;
    }
}

function write(key: string, value: string): void {
    try {
        window.localStorage.setItem(key, value);
    } catch (err) {
        log.ui.warn(`Could not remember browser pane setting ${key}`, err);
    }
}

/** Narrower than this and the composer's controls stop fitting. */
export const CHAT_MIN_WIDTH = 560;

/** Leaves the chat column at least CHAT_MIN_WIDTH beside the sidebar, whatever its width. */
export function clampPaneWidth(width: number): number {
    const room = typeof document !== 'undefined' ? window.innerWidth - chatLeftEdge() - CHAT_MIN_WIDTH : PANE_MAX_WIDTH;
    return Math.round(Math.max(PANE_MIN_WIDTH, Math.min(width, PANE_MAX_WIDTH, room)));
}

function chatLeftEdge(): number {
    return document.querySelector('.main-content')?.getBoundingClientRect().left ?? 0;
}

interface BrowserPaneStore {
    /** The last state main pushed. Only connectBrowserPane writes it. */
    pane: PaneState | null;
    open: boolean;
    width: number;
    setOpen: (open: boolean) => void;
    toggle: () => void;
    setWidth: (width: number) => void;
}

export const useBrowserPaneStore = create<BrowserPaneStore>((set, get) => ({
    pane: null,
    open: browserPaneApi() !== undefined && read(OPEN_KEY) === '1',
    width: clampPaneWidth(Number(read(WIDTH_KEY)) || PANE_DEFAULT_WIDTH),
    setOpen: (open) => {
        write(OPEN_KEY, open ? '1' : '0');
        set({ open });
        if (open && useSessionPanelStore.getState().open) useSessionPanelStore.getState().setOpen(false);
    },
    toggle: () => get().setOpen(!get().open),
    setWidth: (width) => {
        const w = clampPaneWidth(width);
        write(WIDTH_KEY, String(w));
        set({ width: w });
    },
}));

if (useBrowserPaneStore.getState().open && useSessionPanelStore.getState().open) {
    useSessionPanelStore.getState().setOpen(false);
}

useSessionPanelStore.subscribe((now, before) => {
    if (now.open && !before.open && useBrowserPaneStore.getState().open) {
        useBrowserPaneStore.getState().setOpen(false);
    }
});

/** Mirror main's PaneState into the store until the returned unsubscribe runs. */
export function connectBrowserPane(api: GaiaBrowserPaneAPI): () => void {
    let live = true;
    let pushed = false;
    const off = api.onState((pane) => {
        pushed = true;
        useBrowserPaneStore.setState({ pane });
    });
    api.getState()
        .then((pane) => {
            // A push that arrived first is newer than this reply.
            if (live && !pushed) useBrowserPaneStore.setState({ pane });
        })
        .catch((err) => log.ui.error('Could not read the browser pane state', err));
    return () => {
        live = false;
        off();
    };
}
