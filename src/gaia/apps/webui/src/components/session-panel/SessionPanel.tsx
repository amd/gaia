// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The session panel (#5279): one docked, resizable pane per chat that keeps
 * the facts that build trust in view — this answer's sources, where the chat
 * runs and what it may touch, what fills the context, what it produced, what
 * it changed, and which tools its agent has. It absorbs the per-answer side
 * rail (#5222) as its first section, so the transcript loses one column's
 * width, not two.
 *
 * Nothing in it polls: sections read on open, on expand, and when a reply
 * ends. A closed panel is unmounted and reads nothing; a section that shows
 * only with content reads it even while collapsed.
 */

import { useCallback, useEffect, useRef } from 'react';
import { AlertTriangle, PanelRightClose, PanelRightOpen, X } from 'lucide-react';
import { useSessionPanelStore, clampWidth, isFreshFocus, PANEL_DEFAULT_WIDTH, PANEL_MAX_WIDTH, PANEL_MIN_WIDTH } from '../../stores/sessionPanelStore';
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
import { SlidePreview } from './SlidePreview';
import { SLIDE_PREVIEW_ENABLED } from '../../stores/slideDraftStore';
import { BrowserPaneToggle } from '../browser-pane/BrowserPane';
import { AgentInfoSection } from './AgentInfoSection';
import { SkillsSection } from './SkillsSection';
import { SessionInfoSection } from './SessionInfoSection';
import { CommandsSection, DocumentsSection, FocusBar, PlanSection, ScheduleSection } from './FocusSections';
import { useSessionFocus } from './sessionFocus';
import { PanelSettled, useSettled } from './appear';
import { useTurnEnd } from '../../hooks/useTurnEnd';
import { useBrowserPaneOpen } from '../../stores/browserPaneStore';
import type { ContextUsage, Message, TurnSummary } from '../../types';
import { useT } from '../../i18n/useT';
import './SessionPanel.css';

/** Below this window width the panel opens over the chat instead of beside it. */
export const PANEL_OVERLAY_QUERY = '(max-width: 1099px)';

/** A turn created or changed a file: what Outputs and Changes are there to show. */
export function madeFiles(summary: TurnSummary | undefined): boolean {
    if (!summary) return false;
    return (summary.files?.length ?? 0) > 0
        || (summary.outputs ?? []).some((o) => (o.kind === 'image' || o.kind === 'audio') && !!o.path);
}

/**
 * Open the docked panel when a reply that just ended in view is the chat's
 * first to make a file. Never as an overlay, never past the user's close, and
 * never over the browser pane, which shares the right side.
 */
function useOpenOnFirstFile(messages: Message[], busy: boolean, overlay: boolean): void {
    const autoOpen = useSessionPanelStore((s) => s.autoOpen);
    const browserOpen = useBrowserPaneOpen();
    useTurnEnd(busy, () => {
        if (overlay || browserOpen) return;
        const last = messages[messages.length - 1];
        if (last?.role !== 'assistant' || !madeFiles(last.turnSummary)) return;
        if (messages.slice(0, -1).some((m) => madeFiles(m.turnSummary))) return;
        autoOpen();
    });
}

interface SessionPanelProps {
    sessionId: string;
    messages: Message[];
    /** A reply is running; sections refresh when it ends. */
    busy: boolean;
    onUsage?: (usage: ContextUsage) => void;
    /** The chat's measured context use, when the host has one. */
    usage?: ContextUsage | null;
    /** Scroll the transcript to the answer with this id. */
    onJumpToMessage: (messageId: number) => boolean;
}

export function SessionPanel({ sessionId, messages, busy, usage, onUsage, onJumpToMessage }: SessionPanelProps) {
    const t = useT();
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

    useOpenOnFirstFile(messages, busy, overlay);
    useEscapeToClose(open && overlay, close);
    const panelRef = useRef<HTMLElement>(null);
    useFocusTrap(panelRef, open && overlay);

    // An overlay takes focus when it opens, as a dialog does.
    useEffect(() => {
        if (open && overlay) closeRef.current?.focus();
    }, [open, overlay]);

    // Asked to show a section (e.g. the context meter's "Details"): open where we are.
    useEffect(() => {
        if (overlay && isFreshFocus(focus)) setOverlayOpen(true);
    }, [focus, overlay, setOverlayOpen]);

    if (!open) return null;

    return (
        <>
            {overlay && <div className="sp-backdrop" onClick={close} aria-hidden="true" />}
            <aside
                ref={panelRef}
                className={`session-panel ${overlay ? 'is-overlay' : 'is-docked'}`}
                style={overlay ? undefined : { width: resize.width }}
                aria-label={t('sp.label')}
                role={overlay ? 'dialog' : 'complementary'}
                aria-modal={overlay ? true : undefined}
            >
                {!overlay && (
                    <div
                        className={`sp-resize${resize.dragging ? ' is-dragging' : ''}`}
                        aria-label={t('sp.resize')}
                        {...resize.separatorProps}
                    />
                )}
                <header className="sp-header">
                    <h2 className="sp-title">{t('sp.title')}</h2>
                    <button
                        ref={closeRef}
                        type="button"
                        className="btn-icon-sm"
                        onClick={close}
                        aria-label={t('sp.close')}
                        title={t('sp.closeShort')}
                    >
                        {overlay ? <X size={14} /> : <PanelRightClose size={14} />}
                    </button>
                </header>
                <PanelBody
                    sessionId={sessionId}
                    messages={messages}
                    busy={busy}
                    usage={usage}
                    onUsage={onUsage}
                    onJumpToMessage={onJumpToMessage}
                />
            </aside>
        </>
    );
}

/**
 * The panel's content, shaped by what the chat is doing: Environment is always
 * there, and each kind of work brings its own section when it starts.
 */
function PanelBody({ sessionId, messages, busy, usage, onUsage, onJumpToMessage }: SessionPanelProps) {
    const t = useT();
    const focus = useSessionFocus(sessionId, messages, busy);
    const settled = useSettled(sessionId);
    return (
        <PanelSettled.Provider value={settled}>
            <div className="sp-body">
                <FocusBar facets={focus.facets} />
                {SLIDE_PREVIEW_ENABLED && <SlidePreview sessionId={sessionId} />}
                <EnvironmentSection sessionId={sessionId} busy={busy} usage={usage} hideDocuments />
                <PlanSection plan={focus.plan} />
                <ScheduleSection schedules={focus.schedules} />
                {/* Coding leads with what changed; other chats with what they read and made. One key, so moving keeps its state. */}
                {focus.coding && <ChangesSection key="changes" sessionId={sessionId} busy={busy} onJumpToMessage={onJumpToMessage} />}
                <CommandsSection commands={focus.commands} onJumpToMessage={onJumpToMessage} />
                <DocumentsSection documents={focus.documents} messages={messages} />
                {focus.showAnswer && (
                    <PanelSection id="answer" title={t('sp.section.answer')}>
                        <ThisAnswer messages={messages} busy={busy} />
                    </PanelSection>
                )}
                <OutputsSection sessionId={sessionId} busy={busy} onJumpToMessage={onJumpToMessage} />
                {!focus.coding && <ChangesSection key="changes" sessionId={sessionId} busy={busy} onJumpToMessage={onJumpToMessage} />}
                <SkillsSection sessionId={sessionId} busy={busy} />
                <h3 className="sp-group-title">{t('sp.details')}</h3>
                <PanelSection id="context" title={t('sp.section.context')}>
                    <div className="sp-context">
                        <ContextDetails sessionId={sessionId} busy={busy} onUsage={onUsage} />
                    </div>
                </PanelSection>
                <AgentInfoSection sessionId={sessionId} busy={busy} />
                <SessionInfoSection sessionId={sessionId} />
            </div>
        </PanelSettled.Provider>
    );
}

/** The chat header's buttons for the right side: the browser pane's (desktop app) and the panel's. */
export function SessionPanelToggle({ messages, busy }: { messages: Message[]; busy: boolean }) {
    const t = useT();
    const overlay = useMediaQuery(PANEL_OVERLAY_QUERY);
    const open = useSessionPanelStore((s) => (overlay ? s.overlayOpen : s.open));
    const setOpen = useSessionPanelStore((s) => s.setOpen);
    const setOverlayOpen = useSessionPanelStore((s) => s.setOverlayOpen);
    const state = railState(messages, busy);
    const warn = !open && state?.kind === 'turn' && state.turn.turnSummary
        ? liveDataFailed(state.turn.turnSummary)
        : false;
    const label = open ? t('sp.hide') : warn ? t('sp.showWarn') : t('sp.show');
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
