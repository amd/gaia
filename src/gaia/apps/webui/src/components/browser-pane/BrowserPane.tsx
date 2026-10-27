// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The browser pane: a docked, resizable browser beside the chat, desktop app
 * only. Pages live in Electron main as native views; this component draws the
 * toolbar and a placeholder, and tells main where the placeholder is. While
 * an app overlay is open the live page hides and a still frame of it shows.
 */

import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Globe } from 'lucide-react';
import { useBrowserPaneStore, browserPaneApi, connectBrowserPane, clampPaneWidth, PANE_DEFAULT_WIDTH, PANE_MAX_WIDTH, PANE_MIN_WIDTH } from '../../stores/browserPaneStore';
import { useAppOverlayOpen } from '../../hooks/useAppOverlayOpen';
import { useDockedResize } from '../../hooks/useDockedResize';
import { formatShortcut } from '../../utils/platform';
import { log } from '../../utils/logger';
import { BrowserToolbar, TOGGLE_SHORTCUT } from './BrowserToolbar';
import type { GaiaBrowserPaneAPI, PaneNotice, PaneState, TabState } from '../../../services/browser-pane/contract';
import './BrowserPane.css';

function isToggleKey(e: KeyboardEvent): boolean {
    return (e.ctrlKey || e.metaKey) && e.shiftKey && !e.altKey && e.key.toLowerCase() === 'b';
}

/** Mounted with the chat in the desktop app; renders the pane while it is open. */
export function BrowserPane({ api }: { api: GaiaBrowserPaneAPI }) {
    const open = useBrowserPaneStore((s) => s.open);
    const pane = useBrowserPaneStore((s) => s.pane);

    useEffect(() => connectBrowserPane(api), [api]);

    // The shortcut works from the app and, through main, from inside a pane page.
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if (!isToggleKey(e)) return;
            e.preventDefault();
            useBrowserPaneStore.getState().toggle();
        };
        window.addEventListener('keydown', onKey);
        const off = api.onCommand((command) => {
            if (command === 'toggle') useBrowserPaneStore.getState().toggle();
        });
        return () => {
            window.removeEventListener('keydown', onKey);
            off();
        };
    }, [api]);

    if (!open || !pane) return null;
    return <OpenPane api={api} pane={pane} />;
}

function OpenPane({ api, pane }: { api: GaiaBrowserPaneAPI; pane: PaneState }) {
    const width = useBrowserPaneStore((s) => s.width);
    const setWidth = useBrowserPaneStore((s) => s.setWidth);
    const setOpen = useBrowserPaneStore((s) => s.setOpen);
    const overlayOpen = useAppOverlayOpen();
    const viewportRef = useRef<HTMLDivElement>(null);
    const addressRef = useRef<HTMLInputElement>(null);
    const [frozen, setFrozen] = useState<string | null>(null);
    const resize = useDockedResize({
        width,
        setWidth,
        clamp: clampPaneWidth,
        min: PANE_MIN_WIDTH,
        max: PANE_MAX_WIDTH,
        defaultWidth: PANE_DEFAULT_WIDTH,
    });
    const active = pane.tabs.find((t) => t.id === pane.activeTabId) as TabState;

    useEffect(() => {
        api.setVisible(true).catch((err) => log.ui.error('Could not show the browser pane', err));
        return () => {
            api.setVisible(false).catch((err) => log.ui.error('Could not hide the browser pane', err));
        };
    }, [api]);

    useLayoutEffect(() => {
        const el = viewportRef.current;
        if (!el) return;
        const report = () => {
            const r = el.getBoundingClientRect();
            api.setBounds({ x: r.x, y: r.y, width: r.width, height: r.height });
        };
        report();
        const observer = new ResizeObserver(report);
        observer.observe(el);
        window.addEventListener('resize', report);
        return () => {
            observer.disconnect();
            window.removeEventListener('resize', report);
        };
    }, [api]);

    useEffect(() => {
        let current = true;
        api.setOccluded(overlayOpen)
            .then((frame) => {
                if (current) setFrozen(overlayOpen ? frame : null);
            })
            .catch((err) => log.ui.error('Could not hide the browser page under an overlay', err));
        return () => {
            current = false;
        };
    }, [api, overlayOpen]);

    // Toasts and the update chip are fixed to the window; this keeps them clear of the native page.
    useEffect(() => {
        const root = document.documentElement;
        root.style.setProperty('--browser-pane-inset', `${resize.width}px`);
        return () => {
            root.style.removeProperty('--browser-pane-inset');
        };
    }, [resize.width]);

    useEffect(() => {
        if (active.url === null) addressRef.current?.focus();
    }, [active.id, active.url]);

    return (
        <aside className="browser-pane" style={{ width: resize.width }} aria-label="Browser pane">
            <div
                className={`bp-resize${resize.dragging ? ' is-dragging' : ''}`}
                aria-label="Resize browser pane"
                {...resize.separatorProps}
            />
            <BrowserToolbar pane={pane} api={api} onClosePane={() => setOpen(false)} addressRef={addressRef} />
            {active.notice && <NoticeBar notice={active.notice} />}
            <div ref={viewportRef} className="bp-viewport">
                {frozen && <img className="bp-frozen" src={frozen} alt="" aria-hidden="true" />}
                {active.url === null && !frozen && (
                    <NewTabPage
                        pane={pane}
                        onOpen={(url) => {
                            api.navigate(active.id, url).catch((err) => log.ui.error('Browser pane: navigate failed', err));
                        }}
                    />
                )}
            </div>
        </aside>
    );
}

const PERMISSION_NAMES: Record<string, string> = {
    media: 'camera and microphone',
    geolocation: 'location',
    notifications: 'notifications',
};

function noticeText(notice: PaneNotice): string {
    switch (notice.kind) {
        case 'blocked':
            return notice.message;
        case 'load-failed':
            return `Couldn't load ${notice.url}: ${notice.message}`;
        case 'permission-denied':
            return `Blocked ${PERMISSION_NAMES[notice.permission] ?? notice.permission} for ${notice.origin || 'this page'}. The browser pane doesn't share devices or alerts with pages.`;
    }
}

function NoticeBar({ notice }: { notice: PaneNotice }) {
    return (
        <div className={`bp-notice is-${notice.kind}`} role={notice.kind === 'load-failed' ? 'alert' : 'status'}>
            {noticeText(notice)}
        </div>
    );
}

function NewTabPage({ pane, onOpen }: { pane: PaneState; onOpen: (url: string) => void }) {
    return (
        <div className="bp-newtab">
            <h2 className="bp-newtab-title">New tab</h2>
            {pane.recent.length === 0 ? (
                <p className="bp-newtab-empty">Pages you open here show up in this list. Type an address above to start.</p>
            ) : (
                <ul className="bp-recent" aria-label="Recent pages">
                    {pane.recent.map((page) => (
                        <li key={page.url}>
                            <button type="button" className="bp-recent-item" onClick={() => onOpen(page.url)} title={page.url}>
                                <span className="bp-recent-title">{page.title}</span>
                                <span className="bp-recent-url">{page.url}</span>
                            </button>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}

/** The chat header's button that shows or hides the browser pane; nothing outside the desktop app. */
export function BrowserPaneToggle() {
    const open = useBrowserPaneStore((s) => s.open);
    const toggle = useBrowserPaneStore((s) => s.toggle);
    if (!browserPaneApi()) return null;
    const label = `${open ? 'Hide' : 'Show'} browser pane (${formatShortcut(TOGGLE_SHORTCUT)})`;
    return (
        <button
            type="button"
            className={`btn-icon-sm bp-toggle${open ? ' active' : ''}`}
            onClick={toggle}
            aria-label={open ? 'Hide browser pane' : 'Show browser pane'}
            aria-pressed={open}
            title={label}
        >
            <Globe size={15} />
        </button>
    );
}
