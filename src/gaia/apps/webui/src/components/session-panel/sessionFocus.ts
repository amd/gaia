// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * What a chat is about, read from what it has actually done rather than
 * guessed from its words: files it changed, documents it holds, a schedule
 * that runs it, a plan it is working through, websites it contacted. The
 * panel shows each kind of work its own rows, and a plain conversation none.
 */

import { useMemo } from 'react';
import { consolidateSteps, type PlanItemStatus } from '../AgentActivity';
import { railState, turnHasDetails } from '../SideRail';
import { useChatStore } from '../../stores/chatStore';
import { useScheduleStore } from '../../stores/scheduleStore';
import { useSessionPanelStore, type PanelSectionId } from '../../stores/sessionPanelStore';
import { useSessionEnvironment } from '../../hooks/useSessionEnvironment';
import type { AgentStep, CommandOutput, Document, Message, Schedule } from '../../types';
import type { ScopeEntry } from '../../types/sessionPanel';

export type FocusKind = 'chat' | 'coding' | 'commands' | 'documents' | 'scheduled' | 'plan' | 'web' | 'email';

export interface FocusFacet {
    kind: FocusKind;
    /** A count worth showing on the chip: documents, schedules, sites. */
    count?: number;
    /** The section the chip leads to. */
    section?: PanelSectionId;
}

/** A document in the chat, with its place in the library when the library knows it. */
export interface ChatDocument {
    entry: ScopeEntry;
    doc: Document | null;
}

export interface LivePlan {
    steps: string[];
    /** How each step stands, by the same rule the transcript's checklist uses, so the two never disagree. */
    statuses: PlanItemStatus[];
    /** The turn has ended, so nothing is in progress. */
    finished?: boolean;
}

export interface SessionFocus {
    facets: FocusFacet[];
    documents: ChatDocument[];
    schedules: Schedule[];
    plan: LivePlan | null;
    /** Shell commands the chat ran, oldest first. */
    commands: RanCommand[];
    /** The chat is writing or building code: it changed code files, loaded a coding skill, or ran a developer tool. */
    coding: boolean;
    /** The latest answer has sources, files or data movements to report, and no turn is running. */
    showAnswer: boolean;
}

const CODING_SKILLS = /^(coding|code|git|github)/i;

export function sameFile(a: string, b: string): boolean {
    const norm = (p: string) => p.replace(/\\/g, '/').toLowerCase();
    return norm(a) === norm(b);
}

/** The latest plan among *steps* and how far it got; *running* says whether its turn is still going. */
function planOf(steps: AgentStep[], running: boolean): LivePlan | null {
    const rows = consolidateSteps(steps, running);
    for (let i = rows.length - 1; i >= 0; i--) {
        const row = rows[i];
        if (row.type !== 'plan' || !row.planSteps || row.planSteps.length === 0) continue;
        const lines = row.planNarrations?.length === row.planSteps.length ? row.planNarrations : row.planSteps;
        // A plan with no step under way yet has started nothing.
        const statuses = row.planStatuses ?? row.planSteps.map((): PlanItemStatus => 'pending');
        return { steps: lines, statuses };
    }
    return null;
}

/** The plan the running turn is working through, if it made one. */
export function livePlan(steps: AgentStep[]): LivePlan | null {
    return planOf(steps, true);
}

/**
 * The plan the latest answer worked through, kept until the next message so a
 * long task ends on what it finished and where it failed or was stopped.
 * Nothing once the person has moved on, or for an answer that made no plan.
 */
export function finishedPlan(messages: Message[]): LivePlan | null {
    const last = messages[messages.length - 1];
    if (!last || last.role !== 'assistant') return null;
    const plan = planOf(last.agentSteps ?? [], false);
    return plan ? { ...plan, finished: true } : null;
}

/** Distinct websites the chat's answers contacted. */
export function sitesContacted(messages: Message[]): number {
    const sites = new Set<string>();
    for (const m of messages) for (const site of m.turnSummary?.sites ?? []) sites.add(site);
    return sites.size;
}

export function useSessionFocus(sessionId: string, messages: Message[], busy: boolean): SessionFocus {
    const session = useChatStore((s) => s.sessions.find((x) => x.id === sessionId));
    const library = useChatStore((s) => s.documents);
    const steps = useChatStore((s) => s.agentSteps);
    const allSchedules = useScheduleStore((s) => s.schedules);
    const signals = useSessionPanelStore((s) => s.signals[sessionId]);
    const { env } = useSessionEnvironment(sessionId);

    return useMemo(() => {
        const documents: ChatDocument[] = (env?.folders ?? [])
            .filter((f) => f.kind === 'file' && f.source === 'document')
            .map((entry) => ({ entry, doc: library.find((d) => sameFile(d.filepath, entry.path)) ?? null }));
        const schedules = allSchedules.filter((s) => s.session_id === sessionId && s.status !== 'cancelled');
        // Busy also covers comparing two answers, when no turn is streaming: the last plan stays.
        const plan = (busy ? livePlan(steps) : null) ?? finishedPlan(messages);
        const commands = commandsRun(messages, busy ? steps : []);
        const answer = railState(messages, busy);
        // A turn that used nothing but the local model has nothing to report.
        const showAnswer = answer?.kind === 'turn' && !!answer.turn.turnSummary && turnHasDetails(answer.turn.turnSummary);
        const changed = signals?.changedFiles ?? 0;
        const coding = (signals?.changedCode ?? 0) > 0
            || (signals?.skills ?? []).some((name) => CODING_SKILLS.test(name))
            || commands.some((c) => isDevCommand(c.command));
        const sites = sitesContacted(messages);

        const facets: FocusFacet[] = [];
        if (session?.agent_type === 'email') facets.push({ kind: 'email', section: 'environment' });
        if (schedules.length > 0 || session?.kind === 'scheduled') {
            facets.push({ kind: 'scheduled', section: schedules.length > 0 ? 'schedule' : undefined });
        }
        // A chip leads somewhere only when that section is on screen; otherwise it is a label.
        if (coding) {
            facets.push({
                kind: 'coding',
                count: changed || undefined,
                section: changed > 0 ? 'changes' : commands.length > 0 ? 'commands' : undefined,
            });
        }
        if (commands.length > 0) facets.push({ kind: 'commands', count: commands.length, section: 'commands' });
        if (documents.length > 0) facets.push({ kind: 'documents', count: documents.length, section: 'documents' });
        if (sites > 0) facets.push({ kind: 'web', count: sites, section: showAnswer ? 'answer' : undefined });
        if (plan && !plan.finished) facets.push({ kind: 'plan', section: 'plan' });
        // A conversation that has done none of that is just a chat.
        if (facets.length === 0) facets.push({ kind: 'chat' });
        return { facets, documents, schedules, plan, commands, coding, showAnswer };
    }, [allSchedules, busy, env, library, messages, session?.agent_type, session?.kind, sessionId, signals, steps]);
}

export type CommandState = 'ok' | 'failed' | 'declined' | 'stopped' | 'running';

/** A shell command the chat ran, or tried to. */
export interface RanCommand {
    key: string;
    command: string;
    state: CommandState;
    /** The answer whose turn ran it; null while that turn is still running. */
    messageId: number | null;
}

const SHELL_TOOL = 'run_shell_command';

/**
 * The shell tool's arguments, as a step shows them: `key: value` joined by
 * ", " for one or two, by newlines for more, in whatever order the model sent.
 */
const COMMAND_ARG = /(?:^|,[ \t]*|\n)[ \t]*command:[ \t]?/;
/** Where the command's value ends: the next argument. A timeout is a number, so a script line about one is not it. */
const NEXT_ARG = /(?:,[ \t]*|\n)[ \t]*(?:working_directory:[ \t]|timeout:[ \t]?\d+[ \t]*(?:,|\n|$))/;

/** The command line of a shell step: its output's own record, else the argument the step was shown with. */
function commandLine(step: AgentStep): string | null {
    if (step.commandOutput?.command) return step.commandOutput.command;
    const detail = step.detail ?? '';
    const start = COMMAND_ARG.exec(detail);
    if (!start) return null;
    const value = detail.slice(start.index + start[0].length);
    const end = NEXT_ARG.exec(value);
    return (end ? value.slice(0, end.index) : value).trim() || null;
}

function commandState(step: AgentStep): CommandState {
    if (step.declined || step.timedOut) return 'declined';
    if (step.active) return 'running';
    // The stream sends the output camelCase; a chat read back from the server has it as saved, snake_case.
    const out = step.commandOutput as (Partial<CommandOutput> & { return_code?: number }) | null | undefined;
    if (typeof out?.returnCode === 'number') {
        // A non-zero exit can still be an answer (grep finding nothing); the output says when it is.
        return out.returnCode === 0 || out.expectedExit ? 'ok' : 'failed';
    }
    if (typeof out?.return_code === 'number') {
        // The saved output does not keep that note, so the tool's own verdict stands in for it.
        return out.return_code === 0 || step.success === true ? 'ok' : 'failed';
    }
    if (step.success === false) return 'failed';
    // No verdict and no output: the turn was stopped while it ran, so it is not known to have worked.
    return step.success === true || step.result ? 'ok' : 'stopped';
}

/** Every shell command the chat ran, oldest first, the running turn's last. */
export function commandsRun(messages: Message[], live: AgentStep[]): RanCommand[] {
    const ran: RanCommand[] = [];
    const add = (steps: AgentStep[] | undefined, messageId: number | null) => {
        for (const step of steps ?? []) {
            if (step.type !== 'tool' || step.tool !== SHELL_TOOL) continue;
            const command = commandLine(step);
            if (command) ran.push({ key: `${messageId ?? 'live'}-${step.id}`, command, state: commandState(step), messageId });
        }
    };
    for (const m of messages) if (m.role === 'assistant') add(m.agentSteps, m.id);
    add(live, null);
    return ran;
}

const CODE_EXTENSIONS = /\.(py|pyi|ipynb|ts|tsx|js|jsx|mjs|cjs|go|rs|c|cc|cpp|cxx|h|hpp|cs|java|kt|kts|swift|rb|php|lua|r|scala|sh|bash|zsh|ps1|psm1|bat|cmd|sql|html|htm|css|scss|vue|svelte|json|jsonc|yaml|yml|toml|ini|cfg|xml|gradle|cmake|mk|lock)$/i;
const CODE_NAMES = /^(makefile|dockerfile|cmakelists\.txt|requirements[\w.-]*\.txt|\.gitignore|\.env\.example)$/i;

/** Whether *path* is source code or a project file: changing a note or a spreadsheet is not coding. */
export function isCodeFile(path: string): boolean {
    const name = path.split(/[\\/]/).pop() ?? '';
    return CODE_EXTENSIONS.test(name) || CODE_NAMES.test(name);
}

const DEV_TOOLS = /^(git|gh|npm|npx|pnpm|yarn|node|python3?|py|pip3?|uv|pytest|cargo|go|make|cmake|dotnet|mvn|gradle|tsc|docker)$/i;

/** Whether *command* starts a developer tool: `git clone …`, `"C:\…\git.exe" clone …`, `npm test`. */
export function isDevCommand(command: string): boolean {
    const first = /^\s*(?:"([^"]+)"|'([^']+)'|(\S+))/.exec(command);
    const program = (first?.[1] ?? first?.[2] ?? first?.[3] ?? '').split(/[\\/]/).pop() ?? '';
    return DEV_TOOLS.test(program.replace(/\.(exe|cmd|bat)$/i, ''));
}
