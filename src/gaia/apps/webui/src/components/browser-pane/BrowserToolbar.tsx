// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useState, type FormEvent, type RefObject } from 'react';
import { ArrowLeft, ArrowRight, Ellipsis, Eraser, ExternalLink, PanelRightClose, Plus, RotateCw, SquareCode, X } from 'lucide-react';
import { Popover } from '../Popover';
import { CLAIMS_ESCAPE } from '../../hooks/useEscapeToClose';
import { log } from '../../utils/logger';
import { formatShortcut } from '../../utils/platform';
import { normalizeAddress } from './address';
import type { GaiaBrowserPaneAPI, PaneState, TabState } from '../../../services/browser-pane/contract';

export const TOGGLE_SHORTCUT = 'Mod+Shift+B';

/** Run a pane call; its failure is a bug or a closed tab, so it is logged rather than shown. */
function run(what: string, call: Promise<unknown>): void {
    call.catch((err) => log.ui.error(`Browser pane: ${what} failed`, err));
}

interface BrowserToolbarProps {
    pane: PaneState;
    api: GaiaBrowserPaneAPI;
    onClosePane: () => void;
    addressRef: RefObject<HTMLInputElement | null>;
}

export function BrowserToolbar({ pane, api, onClosePane, addressRef }: BrowserToolbarProps) {
    const active = pane.tabs.find((t) => t.id === pane.activeTabId) as TabState;
    const hasPage = active.url !== null;
    return (
        <div className="bp-toolbar">
            <div className="bp-tabs" role="tablist" aria-label="Browser tabs">
                {pane.tabs.map((tab) => {
                    const selected = tab.id === pane.activeTabId;
                    return (
                        <div key={tab.id} className={`bp-tab${selected ? ' is-active' : ''}`}>
                            <button
                                type="button"
                                role="tab"
                                aria-selected={selected}
                                className="bp-tab-title"
                                title={tab.url ?? tab.title}
                                onClick={() => run('select tab', api.selectTab(tab.id))}
                            >
                                {tab.loading && <span className="bp-tab-spinner" aria-hidden="true" />}
                                <span>{tab.title || tab.url || 'New tab'}</span>
                            </button>
                            <button
                                type="button"
                                className="bp-tab-close"
                                aria-label={`Close tab ${tab.title}`}
                                title="Close tab"
                                onClick={() => run('close tab', api.closeTab(tab.id))}
                            >
                                <X size={12} />
                            </button>
                        </div>
                    );
                })}
                <button
                    type="button"
                    className="btn-icon-sm bp-new-tab"
                    aria-label="New tab"
                    title="New tab"
                    onClick={() => run('open tab', api.openTab(null))}
                >
                    <Plus size={14} />
                </button>
            </div>
            <div className="bp-nav">
                <button
                    type="button"
                    className="btn-icon-sm"
                    aria-label="Back"
                    title="Back"
                    disabled={!active.canGoBack}
                    onClick={() => run('back', api.history(active.id, 'back'))}
                >
                    <ArrowLeft size={15} />
                </button>
                <button
                    type="button"
                    className="btn-icon-sm"
                    aria-label="Forward"
                    title="Forward"
                    disabled={!active.canGoForward}
                    onClick={() => run('forward', api.history(active.id, 'forward'))}
                >
                    <ArrowRight size={15} />
                </button>
                {active.loading ? (
                    <button
                        type="button"
                        className="btn-icon-sm"
                        aria-label="Stop loading"
                        title="Stop loading"
                        onClick={() => run('stop', api.history(active.id, 'stop'))}
                    >
                        <X size={15} />
                    </button>
                ) : (
                    <button
                        type="button"
                        className="btn-icon-sm"
                        aria-label="Reload"
                        title="Reload"
                        disabled={!hasPage}
                        onClick={() => run('reload', api.history(active.id, 'reload'))}
                    >
                        <RotateCw size={15} />
                    </button>
                )}
                <AddressBar
                    key={active.id}
                    tab={active}
                    inputRef={addressRef}
                    onSubmit={(url) => run('navigate', api.navigate(active.id, url))}
                />
                <button
                    type="button"
                    className={`btn-icon-sm${active.devToolsOpen ? ' active' : ''}`}
                    aria-label="Developer tools"
                    aria-pressed={active.devToolsOpen}
                    title={active.devToolsOpen ? 'Hide developer tools' : 'Show developer tools'}
                    disabled={!hasPage}
                    onClick={() => run('developer tools', api.toggleDevTools(active.id))}
                >
                    <SquareCode size={15} />
                </button>
                <PaneMenu api={api} tab={active} />
                <button
                    type="button"
                    className="btn-icon-sm"
                    aria-label="Close browser pane"
                    title={`Close browser pane (${formatShortcut(TOGGLE_SHORTCUT)})`}
                    onClick={onClosePane}
                >
                    <PanelRightClose size={15} />
                </button>
            </div>
        </div>
    );
}

function AddressBar({ tab, inputRef, onSubmit }: {
    tab: TabState;
    inputRef: RefObject<HTMLInputElement | null>;
    onSubmit: (url: string) => void;
}) {
    const [draft, setDraft] = useState<string | null>(null);
    const [invalid, setInvalid] = useState(false);

    // A navigation the page made replaces whatever was half-typed.
    useEffect(() => {
        setDraft(null);
        setInvalid(false);
    }, [tab.url]);

    const submit = (e: FormEvent) => {
        e.preventDefault();
        const url = normalizeAddress(draft ?? tab.url ?? '');
        if (url === null) {
            setInvalid(true);
            return;
        }
        setDraft(null);
        setInvalid(false);
        onSubmit(url);
        inputRef.current?.blur();
    };

    return (
        <form className="bp-address" onSubmit={submit} role="search">
            <input
                ref={inputRef}
                type="text"
                className={`bp-address-input${invalid ? ' is-invalid' : ''}`}
                aria-label="Address"
                aria-invalid={invalid}
                title={invalid ? 'Not a web address. Type one like example.com or localhost:5173.' : undefined}
                placeholder="Type a web address"
                spellCheck={false}
                autoComplete="off"
                value={draft ?? tab.url ?? ''}
                onChange={(e) => {
                    setDraft(e.target.value);
                    setInvalid(false);
                }}
                onFocus={(e) => e.currentTarget.select()}
                onKeyDown={(e) => {
                    if (e.key !== 'Escape') return;
                    e.preventDefault();
                    setDraft(null);
                    setInvalid(false);
                    e.currentTarget.blur();
                }}
                {...CLAIMS_ESCAPE}
            />
        </form>
    );
}

function PaneMenu({ api, tab }: { api: GaiaBrowserPaneAPI; tab: TabState }) {
    const [open, setOpen] = useState(false);
    const anchor = useRef<HTMLButtonElement>(null);
    const close = () => setOpen(false);
    return (
        <div className="bp-menu">
            <button
                ref={anchor}
                type="button"
                className={`btn-icon-sm${open ? ' active' : ''}`}
                aria-label="More browser actions"
                aria-expanded={open}
                title="More"
                onClick={() => setOpen((o) => !o)}
            >
                <Ellipsis size={15} />
            </button>
            <Popover open={open} onClose={close} anchor={anchor} label="Browser actions" placement="below" align="end">
                <button
                    type="button"
                    className="popover-item"
                    disabled={tab.url === null}
                    onClick={() => {
                        close();
                        run('open in system browser', api.openExternal(tab.id));
                    }}
                >
                    <ExternalLink size={14} aria-hidden="true" />
                    <span className="popover-item-body">
                        <span className="popover-item-title">Open in system browser</span>
                    </span>
                </button>
                <button
                    type="button"
                    className="popover-item"
                    onClick={() => {
                        close();
                        run('clear browsing data', api.clearBrowsingData());
                    }}
                >
                    <Eraser size={14} aria-hidden="true" />
                    <span className="popover-item-body">
                        <span className="popover-item-title">Clear browsing data</span>
                        <span className="popover-item-note">Cookies, site data, cache and recent pages in this pane</span>
                    </span>
                </button>
            </Popover>
        </div>
    );
}
