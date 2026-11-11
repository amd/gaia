// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState, type FormEvent, type RefObject } from 'react';
import { ArrowLeft, ArrowRight, Ellipsis, Eraser, ExternalLink, Lock, LockOpen, MonitorSmartphone, PanelRightClose, Plus, RotateCcwSquare, RotateCw, SquareCode, SquareDashedMousePointer, X } from 'lucide-react';
import { Popover } from '../Popover';
import { CLAIMS_ESCAPE } from '../../hooks/useEscapeToClose';
import { log } from '../../utils/logger';
import { formatShortcut } from '../../utils/platform';
import { normalizeAddress } from './address';
import { useT } from '../../i18n/useT';
import type { MessageKey } from '../../i18n/registry';
import { DevServersMenu } from './DevServersMenu';
import type { GaiaBrowserPaneAPI, PaneState, TabState, ViewportChoice } from '../../../services/browser-pane/contract';

export const TOGGLE_SHORTCUT = 'Mod+Shift+B';
export const PICK_SHORTCUT = 'Mod+Shift+S';

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
    const active = pane.tabs.find((tab) => tab.id === pane.activeTabId) as TabState;
    const hasPage = active.url !== null;
    const t = useT();
    return (
        <div className="bp-toolbar">
            <div className="bp-tabs" role="tablist" aria-label={t('browserPane.tabs')}>
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
                                {tab.control === 'private' && <Lock size={11} aria-label={t('browserPane.private')} />}
                                <span>{tab.title || tab.url || t('browserPane.newTab')}</span>
                            </button>
                            <button
                                type="button"
                                className="bp-tab-close"
                                aria-label={t('browserPane.closeTabNamed', { name: tab.title })}
                                title={t('browserPane.closeTab')}
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
                    aria-label={t('browserPane.newTab')}
                    title={t('browserPane.newTab')}
                    onClick={() => run('open tab', api.openTab(null))}
                >
                    <Plus size={14} />
                </button>
            </div>
            <div className="bp-nav">
                <button
                    type="button"
                    className="btn-icon-sm"
                    aria-label={t('browserPane.back')}
                    title={t('browserPane.back')}
                    disabled={!active.canGoBack}
                    onClick={() => run('back', api.history(active.id, 'back'))}
                >
                    <ArrowLeft size={15} />
                </button>
                <button
                    type="button"
                    className="btn-icon-sm"
                    aria-label={t('browserPane.forward')}
                    title={t('browserPane.forward')}
                    disabled={!active.canGoForward}
                    onClick={() => run('forward', api.history(active.id, 'forward'))}
                >
                    <ArrowRight size={15} />
                </button>
                {active.loading ? (
                    <button
                        type="button"
                        className="btn-icon-sm"
                        aria-label={t('browserPane.stopLoading')}
                        title={t('browserPane.stopLoading')}
                        onClick={() => run('stop', api.history(active.id, 'stop'))}
                    >
                        <X size={15} />
                    </button>
                ) : (
                    <button
                        type="button"
                        className="btn-icon-sm"
                        aria-label={t('browserPane.reload')}
                        title={t('browserPane.reload')}
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
                    className={`btn-icon-sm${pane.picking ? ' active' : ''}`}
                    aria-label={t('browserPane.select')}
                    aria-pressed={pane.picking}
                    title={t.hint(t(pane.picking ? 'browserPane.selectStop' : 'browserPane.selectStart'), formatShortcut(PICK_SHORTCUT))}
                    disabled={!hasPage}
                    onClick={() => run('select element', api.togglePicking())}
                >
                    <SquareDashedMousePointer size={15} />
                </button>
                <button
                    type="button"
                    className={`btn-icon-sm${active.devToolsOpen ? ' active' : ''}`}
                    aria-label={t('browserPane.devTools')}
                    aria-pressed={active.devToolsOpen}
                    title={t(active.devToolsOpen ? 'browserPane.devToolsHide' : 'browserPane.devToolsShow')}
                    disabled={!hasPage}
                    onClick={() => run('developer tools', api.toggleDevTools(active.id))}
                >
                    <SquareCode size={15} />
                </button>
                <ResponsiveMenu api={api} tab={active} />
                <DevServersMenu api={api} servers={pane.devServers} />
                <PaneMenu api={api} tab={active} />
                <button
                    type="button"
                    className="btn-icon-sm"
                    aria-label={t('browserPane.close')}
                    title={t.hint(t('browserPane.close'), formatShortcut(TOGGLE_SHORTCUT))}
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
    const t = useT();

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
                aria-label={t('browserPane.address')}
                aria-invalid={invalid}
                title={invalid ? t('browserPane.addressInvalid') : undefined}
                placeholder={t('browserPane.addressPlaceholder')}
                dir="ltr"
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
    const t = useT();
    return (
        <div className="bp-menu">
            <button
                ref={anchor}
                type="button"
                className={`btn-icon-sm${open ? ' active' : ''}`}
                aria-label={t('browserPane.moreActions')}
                aria-expanded={open}
                title={t('browserPane.more')}
                onClick={() => setOpen((o) => !o)}
            >
                <Ellipsis size={15} />
            </button>
            <Popover open={open} onClose={close} anchor={anchor} label={t('browserPane.actions')} placement="below" align="end">
                <button
                    type="button"
                    className="popover-item"
                    onClick={() => {
                        close();
                        run('tab privacy', api.setControl(tab.id, tab.control === 'private' ? 'shared' : 'private'));
                    }}
                >
                    {tab.control === 'private' ? <LockOpen size={14} aria-hidden="true" /> : <Lock size={14} aria-hidden="true" />}
                    <span className="popover-item-body">
                        <span className="popover-item-title">{t(tab.control === 'private' ? 'browserPane.shareTab' : 'browserPane.privateTab')}</span>
                        <span className="popover-item-note">
                            {t(tab.control === 'private' ? 'browserPane.shareTabNote' : 'browserPane.privateTabNote')}
                        </span>
                    </span>
                </button>
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
                        <span className="popover-item-title">{t('browserPane.openExternal')}</span>
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
                        <span className="popover-item-title">{t('browserPane.clearData')}</span>
                        <span className="popover-item-note">{t('browserPane.clearDataNote')}</span>
                    </span>
                </button>
            </Popover>
        </div>
    );
}

/** Screens the Responsive menu offers. Fill pane lets the page fill the pane, and lets the agent choose again. */
export const DEVICE_PRESETS: { name: string; width: number; height: number; mobile: boolean; label?: MessageKey }[] = [
    { name: 'iPhone SE', width: 375, height: 667, mobile: true },
    { name: 'iPhone 15', width: 393, height: 852, mobile: true },
    { name: 'Pixel 8', width: 412, height: 915, mobile: true },
    { name: 'iPad mini', width: 768, height: 1024, mobile: true },
    { name: 'iPad Air', width: 820, height: 1180, mobile: true },
    { name: 'Laptop', width: 1280, height: 800, mobile: false, label: 'browserPane.laptop' },
];

const SCHEMES: { label: MessageKey; value: ViewportChoice['colorScheme'] }[] = [
    { label: 'browserPane.scheme.system', value: null },
    { label: 'browserPane.scheme.light', value: 'light' },
    { label: 'browserPane.scheme.dark', value: 'dark' },
];

const MIN_SIDE = 200;
const MAX_SIDE = 4000;

/** Device presets, an exact size, rotate, and light or dark. What the user picks here wins over GAIA. */
function ResponsiveMenu({ api, tab }: { api: GaiaBrowserPaneAPI; tab: TabState }) {
    const [open, setOpen] = useState(false);
    // Stable, so the popover does not refocus its first button on every keystroke.
    const close = useCallback(() => setOpen(false), []);
    const anchor = useRef<HTMLButtonElement>(null);
    const current = tab.viewport;
    const size = current?.size ?? null;
    const scheme = current?.colorScheme ?? null;
    const [width, setWidth] = useState('');
    const [height, setHeight] = useState('');
    const t = useT();

    useEffect(() => {
        setWidth(size ? String(size.width) : '');
        setHeight(size ? String(size.height) : '');
    }, [size?.width, size?.height]);

    const choose = (choice: ViewportChoice) => {
        const filled = choice.size === null && choice.colorScheme === null;
        run('responsive', api.setViewport(tab.id, filled ? null : choice));
    };
    const parsed = (v: string) => {
        const n = Number(v);
        return Number.isInteger(n) && n >= MIN_SIDE && n <= MAX_SIDE ? n : null;
    };
    const w = parsed(width);
    const h = parsed(height);

    return (
        <div className="bp-menu">
            <button
                ref={anchor}
                type="button"
                className={`btn-icon-sm${current ? ' active' : ''}`}
                aria-label={t('browserPane.responsive')}
                aria-expanded={open}
                aria-pressed={current !== null}
                title={size ? t('browserPane.responsiveSize', { width: size.width, height: size.height }) : t('browserPane.responsive')}
                disabled={tab.url === null}
                onClick={() => setOpen((o) => !o)}
            >
                <MonitorSmartphone size={15} />
            </button>
            <Popover open={open} onClose={close} anchor={anchor} label={t('browserPane.responsive')} placement="below" align="end">
                <div className="bp-responsive">
                    {current?.source === 'agent' && <p className="bp-responsive-note">{t('browserPane.agentChose')}</p>}
                    <div className="bp-responsive-presets" role="group" aria-label={t('browserPane.screenSize')}>
                        <button
                            type="button"
                            className={`bp-chip${size === null ? ' is-on' : ''}`}
                            aria-pressed={size === null}
                            onClick={() => choose({ size: null, colorScheme: scheme })}
                        >
                            {t('browserPane.fillPane')}
                        </button>
                        {DEVICE_PRESETS.map((d) => {
                            const on = size !== null && size.width === d.width && size.height === d.height;
                            return (
                                <button
                                    key={d.name}
                                    type="button"
                                    className={`bp-chip${on ? ' is-on' : ''}`}
                                    aria-pressed={on}
                                    title={`${d.width}×${d.height}`}
                                    onClick={() => choose({ size: { width: d.width, height: d.height, mobile: d.mobile }, colorScheme: scheme })}
                                >
                                    {d.label ? t(d.label) : d.name}
                                </button>
                            );
                        })}
                    </div>
                    <form
                        className="bp-responsive-size"
                        onSubmit={(e) => {
                            e.preventDefault();
                            if (w === null || h === null) return;
                            choose({ size: { width: w, height: h, mobile: w < 768 }, colorScheme: scheme });
                        }}
                    >
                        <input aria-label={t('browserPane.width')} inputMode="numeric" value={width} placeholder={t('browserPane.width')} onChange={(e) => setWidth(e.target.value)} />
                        <span aria-hidden="true">×</span>
                        <input aria-label={t('browserPane.height')} inputMode="numeric" value={height} placeholder={t('browserPane.height')} onChange={(e) => setHeight(e.target.value)} />
                        <button type="submit" className="bp-chip" disabled={w === null || h === null}>
                            {t('browserPane.setSize')}
                        </button>
                        <button
                            type="button"
                            className="btn-icon-sm"
                            aria-label={t('browserPane.rotate')}
                            title={t('browserPane.rotate')}
                            disabled={size === null}
                            onClick={() => size && choose({ size: { ...size, width: size.height, height: size.width }, colorScheme: scheme })}
                        >
                            <RotateCcwSquare size={15} />
                        </button>
                    </form>
                    <div className="bp-responsive-presets" role="group" aria-label={t('browserPane.colorScheme')}>
                        {SCHEMES.map((sch) => (
                            <button
                                key={sch.label}
                                type="button"
                                className={`bp-chip${scheme === sch.value ? ' is-on' : ''}`}
                                aria-pressed={scheme === sch.value}
                                onClick={() => choose({ size, colorScheme: sch.value })}
                            >
                                {t(sch.label)}
                            </button>
                        ))}
                    </div>
                </div>
            </Popover>
        </div>
    );
}

/** While picking: how to pick, and the way out. */
export function PickStrip({ api }: { api: GaiaBrowserPaneAPI }) {
    const t = useT();
    return (
        <div className="bp-agent-strip is-picking" role="status">
            <span>{t('browserPane.pickHelp')}</span>
            <button type="button" className="bp-chip" onClick={() => run('stop selecting', api.togglePicking())}>
                {t('browserPane.done')}
            </button>
        </div>
    );
}

/** What the agent is doing in this tab, with Pause and Resume. Clicking the page itself also pauses it. */
export function AgentStrip({ tab, api }: { tab: TabState; api: GaiaBrowserPaneAPI }) {
    const t = useT();
    if (tab.control === 'private') {
        return (
            <div className="bp-agent-strip is-private" role="status">
                <span>{t('browserPane.privateStrip')}</span>
            </div>
        );
    }
    if (tab.control === 'user') {
        return (
            <div className="bp-agent-strip is-paused" role="status">
                <span>{t('browserPane.paused')}</span>
                <button type="button" className="bp-chip" onClick={() => run('resume', api.setControl(tab.id, 'shared'))}>
                    {t('browserPane.resume')}
                </button>
            </div>
        );
    }
    if (!tab.agentActing && !tab.agentBusy) return null;
    return (
        <div className="bp-agent-strip" role="status">
            <span>{t(tab.agentActing ? 'browserPane.agentUsing' : 'browserPane.agentReading')}</span>
            <button type="button" className="bp-chip" onClick={() => run('pause', api.setControl(tab.id, 'user'))}>
                {t('browserPane.pause')}
            </button>
        </div>
    );
}
