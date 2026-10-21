// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The agent's task list, pinned above the composer while it works. */

import { useEffect, useState } from 'react';
import { Check, ChevronDown, ChevronUp, Circle, Loader2 } from 'lucide-react';
import type { TaskListItem, TaskListState } from '../types';
import { log } from '../utils/logger';
import './TaskListCard.css';

/** "12s", "2m 05s", "1h 04m" — the timer and per-step durations. */
export function formatElapsed(seconds: number): string {
    const s = Math.max(0, Math.floor(seconds));
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ${String(s % 60).padStart(2, '0')}s`;
    return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}

/** Steps the card will draw; a longer list from a misbehaving agent is cut, and says so. */
export const MAX_CARD_ITEMS = 50;
const MAX_CONTENT_CHARS = 200;
/** Tool calls without a list update before the card says the list is stale. */
export const STALE_AFTER_STEPS = 4;

const STATUSES = new Set(['pending', 'in_progress', 'completed']);

function timeOrNull(v: unknown): number | null {
    return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

/**
 * A `task_list` event as the card may trust it, or an error naming what was
 * wrong. Never throws: the stream is untrusted input.
 */
export function parseTaskListEvent(raw: unknown): { state: TaskListState } | { error: string } {
    if (!raw || typeof raw !== 'object') return { error: 'not an object' };
    const ev = raw as Record<string, unknown>;
    if (!Array.isArray(ev.items)) return { error: 'items is not a list' };
    const items: TaskListItem[] = [];
    for (const [i, it] of ev.items.slice(0, MAX_CARD_ITEMS).entries()) {
        if (!it || typeof it !== 'object') return { error: `step ${i + 1} is not an object` };
        const item = it as Record<string, unknown>;
        if (typeof item.content !== 'string' || !STATUSES.has(item.status as string)) {
            return { error: `step ${i + 1} has no text or an unknown status` };
        }
        items.push({
            id: i + 1,
            content: item.content.slice(0, MAX_CONTENT_CHARS),
            status: item.status as TaskListItem['status'],
            started_at: timeOrNull(item.started_at),
            completed_at: timeOrNull(item.completed_at),
        });
    }
    const steps = typeof ev.steps_since_update === 'number' && ev.steps_since_update >= 0
        ? Math.floor(ev.steps_since_update) : 0;
    return {
        state: {
            items,
            hidden: Math.max(0, ev.items.length - items.length),
            source: ev.source === 'plan' ? 'plan' : 'agent',
            current_action: typeof ev.current_action === 'string' ? ev.current_action.slice(0, MAX_CONTENT_CHARS) : null,
            action_started_at: timeOrNull(ev.action_started_at),
            steps_since_update: steps,
            updated_at: timeOrNull(ev.updated_at),
        },
    };
}

/** The newer of the polled and the streamed list; null when neither has steps. */
export function pickTaskList(
    savedRaw: TaskListState | null | undefined,
    live: TaskListState | null | undefined,
): TaskListState | null {
    // The saved copy crossed the API as loose JSON; hold it to the same rules.
    let saved: TaskListState | null = null;
    if (savedRaw) {
        const parsed = parseTaskListEvent(savedRaw);
        if ('state' in parsed) {
            saved = parsed.state;
        } else {
            log.chat.warn(`Ignored this chat's saved task list: ${parsed.error}`);
        }
    }
    const newest = (live?.updated_at ?? 0) >= (saved?.updated_at ?? 0) ? live ?? saved : saved;
    return newest && newest.items.length > 0 ? newest : null;
}

function StepMark({ status }: { status: TaskListItem['status'] }) {
    if (status === 'completed') return <Check size={14} className="task-mark task-mark-done" aria-hidden="true" />;
    if (status === 'in_progress') return <Loader2 size={14} className="task-mark task-mark-running spin" aria-hidden="true" />;
    return <Circle size={12} className="task-mark task-mark-pending" aria-hidden="true" />;
}

const STATUS_WORDS: Record<TaskListItem['status'], string> = {
    completed: 'done',
    in_progress: 'in progress',
    pending: 'to do',
};

function stepDuration(item: TaskListItem): string | null {
    if (item.status !== 'completed' || item.started_at == null || item.completed_at == null) return null;
    return formatElapsed(item.completed_at - item.started_at);
}

interface TaskListCardProps {
    taskList: TaskListState;
    /** A turn is running in this chat; the action line and spinner apply only then. */
    running: boolean;
}

export function TaskListCard({ taskList, running }: TaskListCardProps) {
    const [userExpanded, setUserExpanded] = useState<boolean | null>(null);
    const [now, setNow] = useState(() => Date.now() / 1000);

    // Open while the agent works, folded once it stops — until the user picks.
    useEffect(() => { setUserExpanded(null); }, [running]);

    const action = running ? taskList.current_action : null;
    const actionStart = running ? taskList.action_started_at : null;
    useEffect(() => {
        if (actionStart == null) return undefined;
        setNow(Date.now() / 1000);
        const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000);
        return () => window.clearInterval(timer);
    }, [actionStart]);

    const { items } = taskList;
    const done = items.filter((i) => i.status === 'completed').length;
    const current = items.find((i) => i.status === 'in_progress');
    const allDone = done === items.length && !taskList.hidden;
    const expanded = userExpanded ?? running;
    const actionLine = action
        ? `${action}${actionStart != null ? ` · ${formatElapsed(now - actionStart)}` : ''}`
        : null;
    const total = items.length + (taskList.hidden ?? 0);
    // Never claim "done" while the agent is still working.
    // A list finished in an earlier turn carries no live action and stays "done".
    const progress = allDone
        ? (running && taskList.current_action ? `${done} of ${total} · still working` : `${done} of ${total} done`)
        : `${done} of ${total}`;
    const staleSteps = running ? taskList.steps_since_update ?? 0 : 0;
    const stale = staleSteps >= STALE_AFTER_STEPS;
    const oneLine = actionLine ?? (allDone ? null : current?.content ?? null);
    const title = taskList.source === 'plan' ? 'Plan' : 'Tasks';

    return (
        <section
            className={`task-list-card${expanded ? ' task-list-card-open' : ''}`}
            aria-label={title}
            data-testid="task-list-card"
        >
            <button
                type="button"
                className="task-list-head"
                aria-expanded={expanded}
                onClick={() => setUserExpanded(!expanded)}
            >
                <span className="task-list-title">{title}</span>
                <span className="task-list-progress" aria-live="polite">{progress}</span>
                {stale && (
                    <span className="task-list-stale" data-testid="task-list-stale">
                        · not updated for {staleSteps} steps
                    </span>
                )}
                {!expanded && oneLine && <span className="task-list-oneline">· {oneLine}</span>}
                {expanded
                    ? <ChevronDown size={14} className="task-list-chevron" aria-hidden="true" />
                    : <ChevronUp size={14} className="task-list-chevron" aria-hidden="true" />}
            </button>
            {expanded && (
                <>
                    <ol className="task-list-items">
                        {items.map((item) => {
                            // A list saved by a stopped turn shows its step as next, not spinning.
                            const status = !running && item.status === 'in_progress' ? 'pending' : item.status;
                            const duration = stepDuration(item);
                            return (
                                <li key={item.id} className={`task-item task-item-${status}`}>
                                    <StepMark status={status} />
                                    <span className="task-item-text">{item.content}</span>
                                    <span className="visually-hidden">, {STATUS_WORDS[status]}</span>
                                    {duration && <span className="task-item-time">{duration}</span>}
                                </li>
                            );
                        })}
                        {(taskList.hidden ?? 0) > 0 && (
                            <li className="task-item task-item-more">+{taskList.hidden} more steps not shown</li>
                        )}
                    </ol>
                    {actionLine && (
                        <div className="task-list-action" data-testid="task-list-action">
                            <Loader2 size={12} className="spin" aria-hidden="true" />
                            <span>{actionLine}</span>
                        </div>
                    )}
                </>
            )}
        </section>
    );
}
