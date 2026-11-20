// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { AgentStep } from '@webui/types';
import {
    FILTER_MIN_TOOL_ROWS, consolidateSteps, foldThoughts, isTurnError, isWarning, matchesStepFilter, shownWhenDone, stepsSummary, thoughtDurations,
    thoughtLabel, toTimeline, type ActivityRow, type StepStatusFilter,
} from '@webui/utils/agentActivity.core';
import { useEffect, useMemo, useState } from 'react';

import { useT } from '@/i18n/useT';
import { Disclosure, Notice, Stack, Text } from '@/ui';

import { PlanChecklist } from './PlanChecklist';
import { PolicyAlert } from './PolicyAlert';
import { StepFilter } from './StepFilter';
import { ToolStepRow } from './ToolStepRow';

export interface ActivityTimelineProps {
    steps: readonly AgentStep[];
    /** The turn is running: its newest stretch stays open and tool calls time themselves. */
    running?: boolean;
    awaitingUser?: boolean;
    /** The answer so far, so a note it repeats isn't shown twice. */
    answer?: string;
}

/** Wall-clock milliseconds, ticking once a second while `ticking`. */
function useNow(ticking: boolean): number {
    const [now, setNow] = useState(() => Date.now());
    useEffect(() => {
        if (!ticking) return undefined;
        const timer = setInterval(() => setNow(Date.now()), 1000);
        return () => clearInterval(timer);
    }, [ticking]);
    return now;
}

function Thought({ step, live, durationMs, now }: { step: ActivityRow; live: boolean; durationMs?: number; now: number }) {
    const t = useT();
    const [open, setOpen] = useState(false);
    const text = (step.detail ?? '').trim();
    if (!text) return null;
    return (
        <Disclosure title={thoughtLabel(live, live ? now - step.timestamp : durationMs, t)} open={open} onToggle={() => setOpen((v) => !v)}>
            <Text role="label" dir="auto" tone="secondary">
                {text}
            </Text>
        </Disclosure>
    );
}

/** What the agent did during a reply: its notes and steps, the finished stretches folded to one line. */
export function ActivityTimeline({ steps, running = false, awaitingUser = false, answer }: ActivityTimelineProps) {
    const t = useT();
    const [openGroups, setOpenGroups] = useState<ReadonlyMap<number, boolean>>(new Map());
    const [nameFilter, setNameFilter] = useState('');
    const [statusFilter, setStatusFilter] = useState<StepStatusFilter>('all');
    const mutable = useMemo(() => [...steps], [steps]);
    const rows = useMemo(() => consolidateSteps(mutable, running), [mutable, running]);
    const timeline = useMemo(() => toTimeline(rows, answer), [rows, answer]);
    const durations = useMemo(() => thoughtDurations(mutable), [mutable]);
    const now = useNow(running);
    const livePlan = running ? [...rows].reverse().find((s) => s.type === 'plan' && s.planSteps?.length) : undefined;

    const renderRow = (row: ActivityRow) => {
        switch (row.type) {
            case 'thinking':
                return <Thought key={row.id} step={row} live={running && !!row.active} durationMs={durations.get(row.id)} now={now} />;
            case 'tool':
                return <ToolStepRow key={row.id} step={row} now={now} awaitingUser={running && awaitingUser} testID={`step-row-${rows.indexOf(row)}`} />;
            case 'plan':
                return row === livePlan ? null : <PlanChecklist key={row.id} step={row} />;
            case 'status':
                return <Notice key={row.id} tone="warning" message={row.label} />;
            case 'error':
                return <Notice key={row.id} tone="danger" message={row.detail || row.label || t('activity.errorOccurred')} />;
            case 'policy_alert':
                return <PolicyAlert key={row.id} step={row} />;
            default:
                return null;
        }
    };

    const lastIndex = timeline.length - 1;
    const visibleRows = (list: ActivityRow[]) =>
        foldThoughts(list.filter((r) => (shownWhenDone(r) || isWarning(r)) && !(r.type === 'plan' && r === livePlan)));
    const keep = (r: ActivityRow) => matchesStepFilter(r, nameFilter, statusFilter, t);
    // Calls in the runs that are open; folded runs and lone rows have nothing to sift.
    const openCalls = timeline.reduce((n, item, i) => {
        if (item.kind !== 'steps') return n;
        const live = running && i === lastIndex;
        const visible = visibleRows(item.rows);
        if (visible.length < 2 && !live) return n;
        const open = openGroups.get(item.id) ?? (live || visible.some(isTurnError));
        return open ? n + visible.filter((r) => r.type === 'tool').length : n;
    }, 0);
    if (rows.length === 0) return null;

    return (
        <Stack gap="xs" testID="activity-timeline">
            {timeline.map((item, i) => {
                if (item.kind === 'note') {
                    return (
                        <Text key={item.row.id} role="label" dir="auto" tone="secondary">
                            {item.row.detail}
                        </Text>
                    );
                }
                const live = running && i === lastIndex;
                const visible = visibleRows(item.rows);
                if (visible.length === 0) return null;
                // A lone row is already one line; folding it would only add a tap.
                if (visible.length === 1 && !live) return keep(visible[0]!) ? renderRow(visible[0]!) : null;
                const open = openGroups.get(item.id) ?? (live || visible.some(isTurnError));
                const summary = stepsSummary(visible, t);
                return (
                    <Stack key={item.id}>
                        {live ? null : (
                            <Disclosure
                                title={summary}
                                open={open}
                                onToggle={() => setOpenGroups((prev) => new Map(prev).set(item.id, !open))}
                                testID="steps-summary"
                            />
                        )}
                        {open ? <Stack paddingX="md">{visible.filter(keep).map(renderRow)}</Stack> : visible.filter(isWarning).map(renderRow)}
                    </Stack>
                );
            })}
            {livePlan ? <PlanChecklist step={livePlan} /> : null}
            {!running && openCalls >= FILTER_MIN_TOOL_ROWS ? (
                <StepFilter name={nameFilter} status={statusFilter} onName={setNameFilter} onStatus={setStatusFilter} />
            ) : null}
        </Stack>
    );
}
