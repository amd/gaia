// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The session panel (#5279): one docked, resizable pane per chat that keeps
 * the facts that build trust in view — this answer's sources, where the chat
 * runs and what it may touch, what fills the context, what it produced, and
 * what it changed. It absorbs the per-answer side rail (#5222) as its first
 * section, so the transcript loses one column's width, not two.
 *
 * Nothing in it polls: sections read on open, on expand, and when a reply
 * ends; closed or collapsed, they are unmounted.
 */

import { useCallback, useEffect, useRef } from 'react';
import { AlertTriangle, PanelRightClose, PanelRightOpen, X } from 'lucide-react';
import { useSessionPanelStore, clampWidth, PANEL_DEFAULT_WIDTH, PANEL_MAX_WIDTH, PANEL_MIN_WIDTH } from '../../stores/sessionPanelStore';
import { useMediaQuery } from '../../hooks/useMediaQuery';
import { useEscapeToClose } from '../../hooks/useEscapeToClose';
import { useFocusTrap } from '../../hooks/useFocusTrap';
import { useDockedResize } from '../../hooks/useDockedResize';
import { liveDataFailed, railState, ThisAnswer } from '../SideRail';
import { ContextDetails } from '../ContextPanel';
import { PanelSection } from './PanelSection';
import { EnvironmentSection } from './EnvironmentSection';
import { OutputsSection } from './OutputsSection';
import { ChangesSection } from './ChangesSection';
import { BrowserPaneToggle } from '../browser-pane/BrowserPane';
import type { ContextUsage, Message } from '../../types';
import './SessionPanel.css';

/** Below this window width the panel opens over the chat instead of beside it. */
export const PANEL_OVERLAY_QUERY = '(max-width: 1099px)';

interface SessionPanelProps {
    sessionId: string;
    messages: Message[];
    /** A reply is running; sections refresh when it ends. */
    busy: boolean;
    onUsage?: (usage: ContextUsage) => void;
    /** Scroll the transcript to the answer with this id. */
    onJumpToMessage: (messageId: number) => boolean;
}

export function SessionPanel({ sessionId, messages, busy, onUsage, onJumpToMessage }: SessionPanelProps) {
    const overlay = useMediaQuery(PANEL_OVERLAY_QUERY);
    const open = useSessionPanelStore((s) => (overlay ? s.overlayOpen : s.open));
    const width = useSessionPanelStore((s) => s.width);
    const setWidth = useSessionPanelStore((s) => s.setWidth);
    const setOpen = useSessionPanelStore((s) => s.setOpen);
    const setOverlayOpen = useSessionPanelStore((s) => s.setOverlayOpen);
    const focus = useSessionPanelStore((s) => s.focus);
    const closeRef = useRef<HTMLButtonElement>(null);
    const resize = useDockedResize({
        width,
        setWidth,
        clamp: clampWidth,
        min: PANEL_MIN_WIDTH,
        max: PANEL_MAX_WIDTH,
        defaultWidth: PANEL_DEFAULT_WIDTH,
    });

    const close = useCallback(() => {
        if (overlay) setOverlayOpen(false);
        else setOpen(false);
    }, [overlay, setOpen, setOverlayOpen]);

    useEscapeToClose(open && overlay, close);
    const panelRef = useRef<HTMLElement>(null);
    useFocusTrap(panelRef, open && overlay);

    // An overlay takes focus when it opens, as a dialog does.
    useEffect(() => {
        if (open && overlay) closeRef.current?.focus();
    }, [open, overlay]);

    // Asked to show a section (e.g. the context meter's "Details"): open where we are.
    useEffect(() => {
        if (focus && overlay) setOverlayOpen(true);
    }, [focus, overlay, setOverlayOpen]);

    if (!open) return null;

    const answer = railState(messages, busy);
    return (
        <>
            {overlay && <div className="sp-backdrop" onClick={close} aria-hidden="true" />}
            <aside
                ref={panelRef}
                className={`session-panel ${overlay ? 'is-overlay' : 'is-docked'}`}
                style={overlay ? undefined : { width: resize.width }}
                aria-label="Session panel"
                role={overlay ? 'dialog' : 'complementary'}
                aria-modal={overlay ? true : undefined}
            >
                {!overlay && (
                    <div
                        className={`sp-resize${resize.dragging ? ' is-dragging' : ''}`}
                        aria-label="Resize session panel"
                        {...resize.separatorProps}
                    />
                )}
                <header className="sp-header">
                    <h2 className="sp-title">This chat</h2>
                    <button
                        ref={closeRef}
                        type="button"
                        className="btn-icon-sm"
                        onClick={close}
                        aria-label="Close session panel"
                        title="Close"
                    >
                        {overlay ? <X size={14} /> : <PanelRightClose size={14} />}
                    </button>
                </header>
                <div className="sp-body">
                    {answer && (
                        <PanelSection id="answer" title="This answer">
                            <ThisAnswer messages={messages} busy={busy} />
                        </PanelSection>
                    )}
                    <EnvironmentSection sessionId={sessionId} busy={busy} />
                    <PanelSection id="context" title="Context & performance">
                        <div className="sp-context">
                            <ContextDetails sessionId={sessionId} busy={busy} onUsage={onUsage} />
                        </div>
                    </PanelSection>
                    <OutputsSection sessionId={sessionId} busy={busy} onJumpToMessage={onJumpToMessage} />
                    <ChangesSection sessionId={sessionId} busy={busy} onJumpToMessage={onJumpToMessage} />
                </div>
            </aside>
        </>
    );
}

/** The chat header's buttons for the right side: the browser pane's (desktop app) and the panel's. */
export function SessionPanelToggle({ messages, busy }: { messages: Message[]; busy: boolean }) {
    const overlay = useMediaQuery(PANEL_OVERLAY_QUERY);
    const open = useSessionPanelStore((s) => (overlay ? s.overlayOpen : s.open));
    const setOpen = useSessionPanelStore((s) => s.setOpen);
    const setOverlayOpen = useSessionPanelStore((s) => s.setOverlayOpen);
    const state = railState(messages, busy);
    const warn = !open && state?.kind === 'turn' && state.turn.turnSummary
        ? liveDataFailed(state.turn.turnSummary)
        : false;
    const label = open ? 'Hide session panel' : warn ? "Show session panel: couldn't get live data" : 'Show session panel';
    return (
        <>
            <BrowserPaneToggle />
            <button
                type="button"
                className={`btn-icon-sm sp-toggle${open ? ' active' : ''}${warn ? ' is-warn' : ''}`}
                onClick={() => (overlay ? setOverlayOpen(!open) : setOpen(!open))}
                aria-label={label}
                aria-pressed={open}
                title={label}
            >
                {warn ? <AlertTriangle size={15} /> : open ? <PanelRightClose size={15} /> : <PanelRightOpen size={15} />}
            </button>
        </>
    );
}
