// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The parts of the session panel that exist only for a kind of work: the
 * chips that name what the chat is doing, the plan a running turn is working
 * through, the schedule that runs the chat, and the documents it holds. Each
 * shows up when its work does and is gone when it isn't, so a plain
 * conversation keeps a short panel.
 */

import { useState } from 'react';
import {
    Ban, CalendarClock, Check, Circle, Code2, FileText, Globe, ListChecks, Loader2, Mail, MessageSquare, Pause, Play, Square, Terminal,
    X,
    Zap,
    type LucideIcon,
} from 'lucide-react';
import { useChatStore } from '../../stores/chatStore';
import { useScheduleStore } from '../../stores/scheduleStore';
import { useSessionPanelStore } from '../../stores/sessionPanelStore';
import { describeSchedule, formatNextRun } from '../../utils/scheduleText';
import type { PlanItemStatus } from '../AgentActivity';
import { PanelSection } from './PanelSection';
import { useAppear } from './appear';
import { sameFile, type ChatDocument, type CommandState, type FocusFacet, type FocusKind, type LivePlan, type RanCommand } from './sessionFocus';
import type { Message, Schedule } from '../../types';
import type { MessageKey } from '../../i18n/registry';
import { dateLocale, translator, useT } from '../../i18n/useT';

const ICON = 14;

const FACET: Record<FocusKind, { label: MessageKey; Icon: LucideIcon }> = {
    chat: { label: 'focus.chat', Icon: MessageSquare },
    coding: { label: 'focus.coding', Icon: Code2 },
    commands: { label: 'focus.commands', Icon: Terminal },
    documents: { label: 'focus.documents', Icon: FileText },
    scheduled: { label: 'focus.scheduled', Icon: CalendarClock },
    plan: { label: 'focus.plan', Icon: ListChecks },
    web: { label: 'focus.web', Icon: Globe },
    email: { label: 'focus.email', Icon: Mail },
};

/** What the chat is doing, as chips; one that has a section takes you to it. */
export function FocusBar({ facets }: { facets: FocusFacet[] }) {
    const t = useT();
    const reveal = useSessionPanelStore((s) => s.reveal);
    return (
        <ul className="sp-focus" aria-label={t('focus.label')}>
            {facets.map((facet) => <FocusChip key={facet.kind} facet={facet} onGo={reveal} />)}
        </ul>
    );
}

function FocusChip({ facet, onGo }: { facet: FocusFacet; onGo: (id: NonNullable<FocusFacet['section']>) => void }) {
    const t = useT();
    const appear = useAppear();
    const { label, Icon } = FACET[facet.kind];
    const body = (
        <>
            <Icon size={12} aria-hidden="true" />
            {t(label)}
            {facet.count != null && <bdi className="sp-focus-count" dir="ltr">{facet.count}</bdi>}
        </>
    );
    return (
        <li className={`sp-focus-item${appear}`}>
            {facet.section
                ? <button type="button" className={`sp-chip is-${facet.kind}`} onClick={() => onGo(facet.section!)}>{body}</button>
                : <span className={`sp-chip is-${facet.kind}`}>{body}</span>}
        </li>
    );
}

const PLAN_STATE: Record<PlanItemStatus, { label: MessageKey; Icon: LucideIcon }> = {
    done: { label: 'activity.planStatus.done', Icon: Check },
    running: { label: 'activity.planStatus.running', Icon: Loader2 },
    failed: { label: 'activity.planStatus.failed', Icon: X },
    stopped: { label: 'activity.planStatus.stopped', Icon: Ban },
    pending: { label: 'activity.planStatus.pending', Icon: Circle },
};

/** The plan a turn is working through, ticking over as it runs and kept as it ended until the next message. */
export function PlanSection({ plan }: { plan: LivePlan | null }) {
    const t = useT();
    if (!plan) return null;
    const total = plan.steps.length;
    const done = plan.statuses.filter((s) => s === 'done').length;
    return (
        <PanelSection id="plan" title={t('plan.title')} summary={t('plan.progress', { done, total })}>
            <div
                className="sp-progress"
                role="progressbar"
                aria-label={t('plan.title')}
                aria-valuemin={0}
                aria-valuemax={total}
                aria-valuenow={done}
            >
                <span style={{ width: `${(done / total) * 100}%` }} />
            </div>
            <ol className={`sp-list sp-plan${plan.finished ? '' : ' is-live'}`} aria-label={t('plan.listLabel')}>
                {plan.steps.map((step, i) => {
                    const state = plan.statuses[i] ?? 'pending';
                    const { label, Icon } = PLAN_STATE[state];
                    return (
                        <li key={`${i}-${step}`} className={`sp-row-line sp-plan-step is-${state}`} aria-current={state === 'running' ? 'step' : undefined}>
                            <span className="sp-row-icon" title={t(label)}>
                                <Icon
                                    size={ICON}
                                    className={state === 'running' ? 'spin' : state === 'done' ? 'plan-icon-done' : undefined}
                                    aria-label={t(label)}
                                />
                            </span>
                            <span className="sp-plan-text"><bdi>{step}</bdi></span>
                        </li>
                    );
                })}
            </ol>
        </PanelSection>
    );
}

/** The schedules that run in this chat: when, what happened last, and the two things you'd do about it. */
export function ScheduleSection({ schedules }: { schedules: Schedule[] }) {
    const t = useT();
    const setShowSchedules = useChatStore((s) => s.setShowSchedules);
    // The store keeps one error for every screen. This panel shows only how its own last action ended.
    const [error, setError] = useState<string | null>(null);
    // One action at a time across the rows: the store has a single error, so two at once could hide a failure.
    const [working, setWorking] = useState(false);
    if (schedules.length === 0) return null;
    return (
        <PanelSection id="schedule" title={t('sched.title')}>
            <ul className="sp-list" aria-label={t('sched.listLabel')}>
                {schedules.map((schedule) => <ScheduleRow key={schedule.id} schedule={schedule} working={working} onWorking={setWorking} onSettled={setError} />)}
            </ul>
            {error && <span className="sp-error" role="alert">{error}</span>}
            <button type="button" className="link-btn sp-more" onClick={() => setShowSchedules(true)}>
                {t('sched.manage')}
            </button>
        </PanelSection>
    );
}

interface ScheduleRowProps {
    schedule: Schedule;
    /** An action from this panel is in flight, on this row or another. */
    working: boolean;
    onWorking: (working: boolean) => void;
    onSettled: (error: string | null) => void;
}

function ScheduleRow({ schedule, working, onWorking, onSettled }: ScheduleRowProps) {
    const t = useT();
    const updateStatus = useScheduleStore((s) => s.updateScheduleStatus);
    const runNow = useScheduleStore((s) => s.runScheduleNow);
    const paused = schedule.status === 'paused';

    const act = (op: Promise<void>) => {
        onWorking(true);
        // The store sets its error when an action fails and clears it when one works, so this is that action's outcome.
        void op.finally(() => {
            onWorking(false);
            onSettled(useScheduleStore.getState().actionError);
        });
    };

    const when = describeSchedule(schedule.description_parts, t) ?? schedule.description;
    const next = t.compose((say, code) => say('schedules.card.next', {
        when: paused ? say('schedules.status.paused') : formatNextRun(schedule.next_run_at, undefined, say, dateLocale(translator(code))),
    }));
    return (
        <li className="sp-row">
            <div className="sp-row-line">
                <span className="sp-row-icon" aria-hidden="true"><CalendarClock size={ICON} /></span>
                <span className="sp-row-label" title={schedule.prompt}><bdi>{schedule.name}</bdi></span>
                <span className="sp-row-end">
                    <button
                        type="button"
                        className="btn-icon-sm sp-info"
                        onClick={() => act(updateStatus(schedule.name, paused ? 'active' : 'paused'))}
                        disabled={working}
                        aria-label={paused ? t('schedules.card.resume') : t('schedules.card.pause')}
                        title={paused ? t('schedules.card.resume') : t('schedules.card.pause')}
                    >
                        {paused ? <Play size={13} /> : <Pause size={13} />}
                    </button>
                    <button
                        type="button"
                        className="btn-icon-sm sp-info"
                        onClick={() => act(runNow(schedule.name))}
                        disabled={working || schedule.running === true}
                        aria-label={t('schedules.card.runNow')}
                        title={t('schedules.card.runNowTitle')}
                    >
                        {schedule.running ? <Loader2 size={13} className="spin" /> : <Zap size={13} />}
                    </button>
                </span>
            </div>
            <span className="sp-row-sub sp-quiet">{when}</span>
            <span className="sp-row-sub sp-quiet">
                {schedule.running ? t('schedules.card.running') : next}
                {' · '}{t('schedules.card.runs', { n: schedule.run_count })}
            </span>
            {schedule.last_error && (
                <span className="sp-row-sub sp-error">{t('schedules.card.lastFailed', { error: schedule.last_error })}</span>
            )}
        </li>
    );
}

/** Commands shown before "Show more": the latest ones, since those are what the chat is on now. */
export const COMMANDS_SHOWN = 5;

const COMMAND_STATE: Record<CommandState, { label: MessageKey; Icon: LucideIcon }> = {
    ok: { label: 'cmd.ok', Icon: Check },
    failed: { label: 'cmd.failed', Icon: X },
    declined: { label: 'cmd.declined', Icon: Ban },
    stopped: { label: 'activity.planStatus.stopped', Icon: Square },
    running: { label: 'cmd.running', Icon: Loader2 },
};

interface CommandsSectionProps {
    commands: RanCommand[];
    /** Scroll the transcript to the answer with this id. */
    onJumpToMessage: (messageId: number) => boolean;
}

/** The shell commands the chat ran on this PC, newest first: what ran, what failed, what was refused. */
export function CommandsSection({ commands, onJumpToMessage }: CommandsSectionProps) {
    const t = useT();
    const [all, setAll] = useState(false);
    if (commands.length === 0) return null;
    const newestFirst = [...commands].reverse();
    const shown = all ? newestFirst : newestFirst.slice(0, COMMANDS_SHOWN);
    const failed = commands.filter((c) => c.state === 'failed').length;
    return (
        <PanelSection
            id="commands"
            title={t('cmd.title')}
            summary={failed > 0 ? t.plural('cmd.failedCount', failed, { n: failed }) : <bdi dir="ltr">{commands.length}</bdi>}
        >
            <ul className="sp-list" aria-label={t('cmd.listLabel')}>
                {shown.map((c) => {
                    const { label, Icon } = COMMAND_STATE[c.state];
                    const line = (
                        <>
                            <span className={`sp-row-icon sp-cmd-icon is-${c.state}`} title={t(label)}>
                                <Icon size={ICON} className={c.state === 'running' ? 'spin' : undefined} aria-label={t(label)} />
                            </span>
                            <code className="sp-row-label sp-mono" dir="ltr" title={c.command}>{c.command}</code>
                        </>
                    );
                    return (
                        <li key={c.key}>
                            {c.messageId != null ? (
                                <button
                                    type="button"
                                    className="sp-row-line sp-row-button"
                                    onClick={() => onJumpToMessage(c.messageId as number)}
                                >
                                    {line}
                                </button>
                            ) : <div className="sp-row-line">{line}</div>}
                        </li>
                    );
                })}
            </ul>
            {commands.length > COMMANDS_SHOWN && (
                <button type="button" className="link-btn sp-more" onClick={() => setAll((v) => !v)}>
                    {all ? t('out.fewer') : t('out.more', { n: commands.length - COMMANDS_SHOWN })}
                </button>
            )}
        </PanelSection>
    );
}

/** Passages of the document at *path* the latest answer drew on, or 0. */
function passagesUsed(messages: Message[], path: string): number {
    const last = messages[messages.length - 1];
    const used = last?.role === 'assistant' ? last.turnSummary?.documents ?? [] : [];
    // By path: two documents can share a file name.
    return used.find((d) => sameFile(d.path, path))?.passages ?? 0;
}

/** The documents this chat can search: whether each is ready, and which the latest answer used. */
export function DocumentsSection({ documents, messages }: { documents: ChatDocument[]; messages: Message[] }) {
    const t = useT();
    if (documents.length === 0) return null;
    return (
        <PanelSection id="documents" title={t('docsp.title')} summary={<bdi dir="ltr">{documents.length}</bdi>}>
            <ul className="sp-list" aria-label={t('docsp.listLabel')}>
                {documents.map(({ entry, doc }) => {
                    const name = entry.name || doc?.filename || entry.path.split(/[\\/]/).pop() || entry.path;
                    const status = doc?.indexing_status;
                    const indexing = status === 'indexing' || status === 'pending';
                    const failed = status === 'failed' || status === 'missing';
                    const used = passagesUsed(messages, entry.path);
                    return (
                        <li key={entry.path} className="sp-row">
                            <div className="sp-row-line">
                                <span className="sp-row-icon" aria-hidden="true">
                                    {indexing ? <Loader2 size={ICON} className="spin" /> : <FileText size={ICON} />}
                                </span>
                                <span className="sp-row-label" dir="ltr" title={entry.path}>{name}</span>
                                <span className="sp-row-end">
                                    {used > 0 && (
                                        <span className="sp-badge" title={t('docsp.usedTitle')}>{t.plural('docsp.used', used, { n: used })}</span>
                                    )}
                                    {indexing && <span className="sp-quiet">{t('docs.indexingLabel')}</span>}
                                    {failed && <span className="sp-error">{t('docs.failed')}</span>}
                                    {!indexing && !failed && doc && doc.chunk_count > 0 && used === 0 && (
                                        <span className="sp-quiet" title={t('docsp.passagesTitle')}>
                                            {t.plural('docsp.passages', doc.chunk_count, { n: doc.chunk_count })}
                                        </span>
                                    )}
                                </span>
                            </div>
                            {failed && doc?.last_error && <span className="sp-row-sub sp-error">{doc.last_error}</span>}
                        </li>
                    );
                })}
            </ul>
        </PanelSection>
    );
}
