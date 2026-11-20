// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The session panel shapes itself to the chat: chips, plan, schedule and documents show when their work does. */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SessionPanel } from '../SessionPanel';
import { COMMANDS_SHOWN, CommandsSection, DocumentsSection, FocusBar, PlanSection, ScheduleSection } from '../FocusSections';
import { commandsRun, finishedPlan, isDevCommand, livePlan, sitesContacted } from '../sessionFocus';
import { PanelSettled, SETTLE_MS } from '../appear';
import { useChatStore } from '../../../stores/chatStore';
import { useScheduleStore } from '../../../stores/scheduleStore';
import { useSessionPanelStore } from '../../../stores/sessionPanelStore';
import type { AgentStep, Message, Schedule } from '../../../types';
import { ENV, resetPanelState, SID, stubPanelApi, stubWindowWidth } from './testUtils';

vi.mock('../../../services/api');

let mocked: ReturnType<typeof stubPanelApi>;

const QUESTION: Message = { id: 1, session_id: SID, role: 'user', content: 'q', created_at: '', rag_sources: null };

const SCHEDULE: Schedule = {
    id: 'sch-1', name: 'morning-brief', interval_seconds: 86400, prompt: 'Summarise my inbox.', status: 'active',
    created_at: null, last_run_at: null, next_run_at: null, last_result: null, run_count: 4, error_count: 0,
    session_id: SID, schedule_config: null, description: 'Every day at 8:00 AM',
    description_parts: { kind: 'daily', count: null, unit: null, days: null, time: '08:00', start_hour: null, end_hour: null },
    last_error: null,
};

const PLAN: AgentStep = {
    id: 1, type: 'plan', label: 'Created plan', timestamp: 0,
    planSteps: ['read_file', 'edit_file', 'run_tests'],
    planNarrations: ['Read the config', 'Change the port', 'Run the tests'],
    currentStep: 1,
};

function setSession(extra: Record<string, unknown> = {}) {
    useChatStore.setState({
        sessions: [{
            id: SID, title: 't', created_at: '', updated_at: '', model: 'm', system_prompt: null, message_count: 0,
            document_ids: [], inference_remote: false, ...extra,
        } as never],
        currentSessionId: SID,
        documents: [],
        agentSteps: [],
    });
}

function chips(): string[] {
    const bar = screen.getByRole('list', { name: 'What this chat is doing' });
    return within(bar).getAllByRole('listitem').map((li) => li.textContent ?? '');
}

function Panel({ busy = false, messages = [QUESTION] }: { busy?: boolean; messages?: Message[] }) {
    return <SessionPanel sessionId={SID} messages={messages} busy={busy} onJumpToMessage={() => true} />;
}

beforeEach(() => {
    vi.clearAllMocks();
    mocked = stubPanelApi();
    resetPanelState();
    stubWindowWidth(false);
    setSession();
    useScheduleStore.setState({ schedules: [], actionError: null });
    mocked.getEnvironment.mockResolvedValue({ ...ENV, folders: ENV.folders.filter((f) => f.source !== 'document') });
});

afterEach(() => {
    vi.useRealTimers();
});

describe('what the chat is doing', () => {
    it('calls a conversation that has done nothing else a chat', async () => {
        render(<Panel />);
        // Every read that could bring a section has answered before anything is called absent.
        await waitFor(() => {
            for (const read of [mocked.getEnvironment, mocked.getOutputs, mocked.getCapabilities, mocked.getSessionChanges]) {
                expect(read).toHaveBeenCalled();
            }
        });
        await act(async () => {});
        expect(chips()).toEqual(['Chat']);
        for (const name of ['Plan', 'Schedule', 'Documents', 'Commands', 'Changes', 'Outputs', 'Skills']) {
            expect(screen.queryByRole('button', { name: new RegExp(`^${name}`) })).not.toBeInTheDocument();
        }
    });

    it('names documents once the chat holds some, and gives them a section', async () => {
        mocked.getEnvironment.mockResolvedValue(ENV);
        useChatStore.setState({
            documents: [{
                id: 'd1', filename: 'report.pdf', filepath: 'C:\\Users\\me\\report.pdf', file_size: 1, chunk_count: 42,
                indexed_at: '', last_accessed_at: null, sessions_using: 1, indexing_status: 'complete',
            }],
        });
        render(<Panel />);
        const list = await screen.findByRole('list', { name: 'Documents in this chat' });
        expect(within(list).getByText('report.pdf')).toBeInTheDocument();
        expect(within(list).getByText('42 passages')).toBeInTheDocument();
        expect(chips()).toEqual(['Documents1']);
        // Listed once: the document is not also a row under the folders.
        expect(screen.getAllByText('report.pdf')).toHaveLength(1);
    });

    it('names coding once the chat has changed files, and leads with the changes', async () => {
        mocked.getSessionChanges.mockResolvedValue({
            files: [{
                path: 'C:\\Users\\me\\project\\a.ts', change: 'edited', unified: '', binary: false, too_large: false, truncated: false,
                additions: 3, deletions: 1, before_size: 1, after_size: 2, message_ids: [2], changed_since: null,
            }],
            totals: { files: 1, additions: 3, deletions: 1, uncounted: 0 }, turns: 1, pending: false, untracked: [], incomplete: false,
        });
        render(<Panel />);
        await waitFor(() => expect(chips()).toEqual(['Coding1']));
        const heads = [...document.querySelectorAll('.sp-section-title')].map((el) => el.textContent);
        expect(heads.indexOf('Changes')).toBe(heads.indexOf('Environment') + 1);
    });

    it('keeps what Undo all said after the chat stops being a coding chat', async () => {
        const changed = {
            files: [{
                path: 'C:\\Users\\me\\project\\a.ts', change: 'edited' as const, unified: '', binary: false, too_large: false, truncated: false,
                additions: 3, deletions: 1, before_size: 1, after_size: 2, message_ids: [2], changed_since: null,
            }],
            totals: { files: 1, additions: 3, deletions: 1, uncounted: 0 }, turns: 1, pending: false, untracked: [], incomplete: false,
        };
        mocked.getSessionChanges.mockResolvedValue(changed);
        mocked.undoAllChanges.mockImplementation(async () => {
            // Once undone, the chat has changed nothing: it is no longer "coding".
            mocked.getSessionChanges.mockResolvedValue({ ...changed, files: [], totals: { files: 0, additions: 0, deletions: 0, uncounted: 0 } });
            return { turn_ids: ['t1'], restored: ['a'], removed: [], kept_folders: [], untracked: [], incomplete: false };
        });
        mocked.listTurnChanges.mockResolvedValue({ turns: [] });
        render(<Panel />);
        await waitFor(() => expect(chips()).toEqual(['Coding1']));
        // Moving the section up under Environment must not read the changes a second time.
        expect(mocked.getSessionChanges).toHaveBeenCalledTimes(1);

        fireEvent.click(await screen.findByRole('button', { name: /Undo all/ }));
        fireEvent.click(within(screen.getByRole('group', { name: 'Confirm undo all' })).getByRole('button', { name: 'Undo all' }));
        await waitFor(() => expect(chips()).toEqual(['Chat']));
        expect(screen.getByRole('status')).toHaveTextContent('Undone: restored 1 file');
    });

    it('gives a chip a button only when its section is there to go to', async () => {
        // An earlier answer searched the web; the latest was a plain local one, so "This answer" is not shown.
        const summary = (sites: string[]) => ({
            pages: [], documents: [], files: [], sites, live_data: { attempted: 0, failed: 0 }, cost: { kind: 'local' },
            model: { remote: false, provider: 'lemonade', provider_name: 'Lemonade', name: 'Gemma' },
        });
        const messages: Message[] = [
            QUESTION,
            { id: 2, session_id: SID, role: 'assistant', content: 'a', created_at: '', rag_sources: null, turnSummary: summary(['a.com']) as never },
            { ...QUESTION, id: 3 },
            { id: 4, session_id: SID, role: 'assistant', content: 'b', created_at: '', rag_sources: null, turnSummary: summary([]) as never },
        ];
        // A coding skill is loaded, but nothing has been changed or run yet.
        mocked.getCapabilities.mockResolvedValue({ session_id: SID, agent_type: 'gaia', tools: [], skills: [{ name: 'coding', how: 'model' }] });
        render(<Panel messages={messages} />);
        await waitFor(() => expect(chips()).toEqual(['Coding', 'Web1']));
        const bar = screen.getByRole('list', { name: 'What this chat is doing' });
        expect(within(bar).queryByRole('button')).not.toBeInTheDocument();
    });

    it('shows a failed schedule action once, and only one this panel started', async () => {
        const second = { ...SCHEDULE, id: 'sch-2', name: 'evening-brief' };
        useScheduleStore.setState({ schedules: [SCHEDULE, second], actionError: 'Failed to delete "elsewhere"' });
        const updateScheduleStatus = vi.fn(async () => { useScheduleStore.setState({ actionError: 'The scheduler is not running.' }); });
        useScheduleStore.setState({ updateScheduleStatus });
        render(<Panel />);
        const list = await screen.findByRole('list', { name: 'Schedules that run in this chat' });
        // An error from the Scheduled tasks dialog is not this panel's to show.
        expect(screen.queryByText(/Failed to delete/)).not.toBeInTheDocument();

        fireEvent.click(within(list).getAllByRole('button', { name: 'Pause' })[0]);
        expect(await screen.findAllByText('The scheduler is not running.')).toHaveLength(1);

        // Later, something fails in the Scheduled tasks dialog: still not this panel's to show.
        act(() => useScheduleStore.setState({ actionError: 'Failed to delete "elsewhere"' }));
        expect(screen.queryByText(/Failed to delete/)).not.toBeInTheDocument();
        expect(screen.getByText('The scheduler is not running.')).toBeInTheDocument();

        // The next action from here works, so the old failure goes.
        act(() => useScheduleStore.setState({ updateScheduleStatus: vi.fn(async () => { useScheduleStore.setState({ actionError: null }); }) }));
        fireEvent.click(within(list).getAllByRole('button', { name: 'Pause' })[0]);
        await waitFor(() => expect(screen.queryByText('The scheduler is not running.')).not.toBeInTheDocument());
    });

    it('runs one schedule action at a time, so a later success cannot hide an earlier failure', async () => {
        const second = { ...SCHEDULE, id: 'sch-2', name: 'evening-brief' };
        let finish: () => void = () => {};
        const updateScheduleStatus = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
        useScheduleStore.setState({ schedules: [SCHEDULE, second], updateScheduleStatus });
        render(<Panel />);
        const list = await screen.findByRole('list', { name: 'Schedules that run in this chat' });
        const buttons = () => within(list).getAllByRole('button');

        fireEvent.click(within(list).getAllByRole('button', { name: 'Pause' })[0]);
        // While the first is out, neither row takes another.
        for (const button of buttons()) expect(button).toBeDisabled();

        await act(async () => { finish(); });
        for (const button of buttons()) expect(button).toBeEnabled();
    });

    it('does not jump to a section because of a chip clicked a while ago', async () => {
        // The test setup stubs scrollIntoView with a spy; count its calls from here.
        const scrolled = vi.mocked(HTMLElement.prototype.scrollIntoView);
        scrolled.mockClear();
        try {
            vi.useFakeTimers({ shouldAdvanceTime: true });
            useChatStore.setState({ agentSteps: [PLAN] });
            const { rerender } = render(<Panel busy />);
            await screen.findByRole('progressbar', { name: 'Plan' });
            fireEvent.click(screen.getByRole('button', { name: /Working a plan/ }));
            expect(scrolled).toHaveBeenCalledTimes(1);

            // The reply ends and, much later, another turn makes a new plan.
            useChatStore.setState({ agentSteps: [] });
            rerender(<Panel busy={false} />);
            await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
            useChatStore.setState({ agentSteps: [PLAN] });
            rerender(<Panel busy />);
            await screen.findByRole('progressbar', { name: 'Plan' });
            expect(scrolled).toHaveBeenCalledTimes(1);
        } finally {
            vi.useRealTimers();
        }
    });

    it('keeps the finished plan in view while two answers are being compared', async () => {
        const answered: Message[] = [QUESTION, {
            id: 2, session_id: SID, role: 'assistant', content: 'Done.', created_at: '', rag_sources: null,
            agentSteps: [{ ...PLAN, currentStep: 2 }],
        }];
        // Comparing answers counts as busy, but no turn of this chat is streaming steps.
        render(<Panel messages={answered} busy />);
        expect(await screen.findByRole('progressbar', { name: 'Plan' })).toBeInTheDocument();
        expect(chips()).not.toContain('Working a plan');
    });

    it('names a schedule that runs in the chat, with when it runs and how to stop it', async () => {
        useScheduleStore.setState({ schedules: [SCHEDULE, { ...SCHEDULE, id: 'other', name: 'elsewhere', session_id: 'another' }] });
        const updateScheduleStatus = vi.fn(async () => {});
        useScheduleStore.setState({ updateScheduleStatus });
        render(<Panel />);
        await waitFor(() => expect(chips()).toEqual(['Scheduled']));
        const list = screen.getByRole('list', { name: 'Schedules that run in this chat' });
        expect(within(list).getByText('morning-brief')).toBeInTheDocument();
        expect(within(list).queryByText('elsewhere')).not.toBeInTheDocument();
        expect(within(list).getByText(/Runs: 4/)).toBeInTheDocument();
        fireEvent.click(within(list).getByRole('button', { name: 'Pause' }));
        expect(updateScheduleStatus).toHaveBeenCalledWith('morning-brief', 'paused');
    });

    it('shows the plan a running turn is working through, and drops it when the turn ends', async () => {
        useChatStore.setState({ agentSteps: [PLAN] });
        const { rerender } = render(<Panel busy />);
        const list = await screen.findByRole('list', { name: 'Steps of the plan GAIA is working through' });
        const steps = within(list).getAllByRole('listitem');
        expect(steps.map((li) => li.textContent)).toEqual(['Read the config', 'Change the port', 'Run the tests']);
        expect(steps[1]).toHaveAttribute('aria-current', 'step');
        expect(screen.getByRole('progressbar', { name: 'Plan' })).toHaveAttribute('aria-valuenow', '1');
        expect(chips()).toContain('Working a plan');

        rerender(<Panel busy={false} />);
        expect(screen.queryByRole('list', { name: 'Steps of the plan GAIA is working through' })).not.toBeInTheDocument();
        expect(chips()).toEqual(['Chat']);
    });

    it('keeps the finished plan as the turn left it until the next message', async () => {
        const ran: AgentStep = { id: 2, type: 'tool', tool: 'run_shell_command', label: 'Ran', timestamp: 1, success: true, active: false };
        const answered: Message[] = [QUESTION, {
            id: 2, session_id: SID, role: 'assistant', content: 'Done.', created_at: '', rag_sources: null,
            agentSteps: [{ ...PLAN, currentStep: 2 }, ran],
        }];
        const { rerender } = render(<Panel messages={answered} />);
        const list = await screen.findByRole('list', { name: 'Steps of the plan GAIA is working through' });
        expect(within(list).getAllByLabelText('Done')).toHaveLength(3);
        expect(within(list).queryByLabelText('Running')).not.toBeInTheDocument();
        expect(screen.getByRole('progressbar', { name: 'Plan' })).toHaveAttribute('aria-valuenow', '3');
        // The work is over, so the chat is no longer "working a plan".
        expect(chips()).toEqual(['Chat']);

        rerender(<Panel messages={[...answered, { ...QUESTION, id: 3 }]} />);
        expect(screen.queryByRole('progressbar', { name: 'Plan' })).not.toBeInTheDocument();
    });

    it('says where a turn failed or was cut short, and never claims a step it did not finish', () => {
        const answer = (last: Partial<AgentStep>): Message[] => [QUESTION, {
            id: 2, session_id: SID, role: 'assistant', content: '', created_at: '', rag_sources: null,
            agentSteps: [PLAN, { id: 2, type: 'tool', tool: 'edit_file', label: 'Editing', timestamp: 1, active: false, ...last }],
        }];
        // The same verdicts the transcript's checklist gives, so the two never disagree.
        expect(finishedPlan(answer({ success: false }))?.statuses).toEqual(['done', 'failed', 'pending']);
        expect(finishedPlan(answer({}))?.statuses).toEqual(['done', 'stopped', 'pending']);
        // No call was made for the last step: it was not reached, so it is not ticked.
        expect(finishedPlan([QUESTION, { ...answer({})[1], agentSteps: [{ ...PLAN, currentStep: 2 }] }])?.statuses)
            .toEqual(['done', 'done', 'pending']);

        render(<PlanSection plan={finishedPlan(answer({ success: false }))} />);
        expect(screen.getAllByLabelText('Done')).toHaveLength(1);
        expect(screen.getByLabelText('Failed')).toBeInTheDocument();
        expect(screen.getByLabelText('Not started')).toBeInTheDocument();
        expect(screen.getByRole('progressbar', { name: 'Plan' })).toHaveAttribute('aria-valuenow', '1');
    });

    it('counts the websites the chat contacted', () => {
        const answer = (id: number, sites: string[]): Message => ({
            id, session_id: SID, role: 'assistant', content: 'a', created_at: '', rag_sources: null,
            turnSummary: { pages: [], documents: [], files: [], sites, live_data: { attempted: 0, failed: 0 }, cost: { kind: 'local' }, model: null } as never,
        });
        expect(sitesContacted([QUESTION, answer(2, ['a.com', 'b.com']), answer(3, ['a.com'])])).toBe(2);
    });
});

describe('commands', () => {
    const shell = (id: number, detail: string, extra: Partial<AgentStep> = {}): AgentStep => ({
        id, type: 'tool', tool: 'run_shell_command', label: 'Using run_shell_command', detail, timestamp: 0, success: true, ...extra,
    });
    // A chat that only ran commands: it changed no file GAIA tracks and loaded no skill.
    const CLONE: Message[] = [QUESTION, {
        id: 2, session_id: SID, role: 'assistant', content: 'Run it yourself.', created_at: '', rag_sources: null,
        agentSteps: [
            { id: 1, type: 'thinking', label: 'Thinking', timestamp: 0 },
            shell(2, 'command: git clone https://github.com/amd/gaia.git C:\\Users\\me\\Work\\gaia, timeout: 120', { success: false }),
            shell(3, 'command: winget install Git.Git --source winget --silent'),
            { id: 4, type: 'tool', tool: 'reset_shell_session', label: 'Using reset_shell_session', timestamp: 0, success: true },
            shell(5, 'command: powershell -Command "git clone https://github.com/amd/gaia.git"', { success: false, declined: true }),
        ],
    }];

    it('reads each command and how it ended from the saved steps', () => {
        expect(commandsRun(CLONE, []).map((c) => [c.command, c.state, c.messageId])).toEqual([
            ['git clone https://github.com/amd/gaia.git C:\\Users\\me\\Work\\gaia', 'failed', 2],
            ['winget install Git.Git --source winget --silent', 'ok', 2],
            ['powershell -Command "git clone https://github.com/amd/gaia.git"', 'declined', 2],
        ]);
    });

    it('trusts the exit code over the step when the output was kept, and marks the running turn', () => {
        const out = (returnCode: number, expectedExit: string | null = null) =>
            ({ command: 'grep -r todo .', stdout: '', stderr: '', returnCode, expectedExit });
        const live = [shell(9, 'command: npm test', { active: true })];
        const messages: Message[] = [{
            ...CLONE[1],
            agentSteps: [shell(1, '', { commandOutput: out(1, 'no match'), success: false }), shell(2, '', { commandOutput: out(2), success: true })],
        }];
        expect(commandsRun(messages, live).map((c) => [c.command, c.state, c.messageId])).toEqual([
            ['grep -r todo .', 'ok', 2],
            ['grep -r todo .', 'failed', 2],
            ['npm test', 'running', null],
        ]);
    });

    it('takes the command alone, however its other arguments were written out', () => {
        const only = (detail: string) => commandsRun([{ ...CLONE[1], agentSteps: [shell(1, detail)] }], [])[0]?.command;
        // Three or more arguments, or a value with ", " in it, are joined by newlines.
        expect(only('command: git clone x\nworking_directory: C:\\Users\\me\\Work\ntimeout: 60')).toBe('git clone x');
        expect(only('command: echo a, b\ntimeout: 60')).toBe('echo a, b');
        expect(only('command: npm test, timeout: 120')).toBe('npm test');
        // A command that merely mentions an argument name keeps its text.
        expect(only('command: echo "timeout: 5 seconds"')).toBe('echo "timeout: 5 seconds"');
    });

    it('finds the command wherever the model put it among its arguments', () => {
        const only = (detail: string, extra: Partial<AgentStep> = {}) =>
            commandsRun([{ ...CLONE[1], agentSteps: [shell(1, detail, extra)] }], [])[0]?.command;
        expect(only('working_directory: C:\\Users\\me, command: dir')).toBe('dir');
        expect(only('timeout: 60\nworking_directory: C:\\Users\\me\ncommand: git status')).toBe('git status');
        // A refused call never gets output, so its arguments are all there is to read.
        expect(only('working_directory: C:\\x, command: del /q *', { declined: true, success: false })).toBe('del /q *');
        // A script that merely has a line about a timeout is not cut there.
        expect(only('command: echo start\ntimeout: wait for it\necho done')).toBe('echo start\ntimeout: wait for it\necho done');
    });

    it('reads a saved command the way the server stores it, not only the way the stream sends it', () => {
        // Reloaded from the server, the output's fields are snake_case.
        const saved = (return_code: number, success: boolean) => shell(1, '', {
            success,
            commandOutput: { command: 'npm test', stdout: '', stderr: '', return_code } as never,
        });
        const state = (step: AgentStep) => commandsRun([{ ...CLONE[1], agentSteps: [step] }], [])[0].state;
        expect(state(saved(0, true))).toBe('ok');
        expect(state(saved(1, false))).toBe('failed');
        // grep's "no match" exits 1 and is still an answer: the tool said so.
        expect(state(saved(1, true))).toBe('ok');
    });

    it('never calls a command that was cut off a success', () => {
        // Stopping a turn marks every step inactive; this one has no output and no verdict.
        const cut = shell(1, 'command: npm install', { success: undefined, active: false });
        expect(commandsRun([{ ...CLONE[1], agentSteps: [cut] }], [])[0].state).toBe('stopped');
        render(<CommandsSection commands={commandsRun([{ ...CLONE[1], agentSteps: [cut] }], [])} onJumpToMessage={() => true} />);
        expect(screen.getByLabelText('Stopped')).toBeInTheDocument();
        expect(screen.queryByLabelText('Ran')).not.toBeInTheDocument();
    });

    it('knows a developer tool by its program, however it is spelled', () => {
        for (const command of ['git clone x', '"C:\\Program Files\\Git\\cmd\\git.exe" clone x', '/usr/bin/python3 -m pytest', 'npm.cmd test']) {
            expect(isDevCommand(command), command).toBe(true);
        }
        for (const command of ['winget install Git.Git', 'dir', 'echo git']) expect(isDevCommand(command), command).toBe(false);
    });

    it('calls a chat that ran git coding, and lists what it ran with failures counted', async () => {
        const onJump = vi.fn(() => true);
        render(<SessionPanel sessionId={SID} messages={CLONE} busy={false} onJumpToMessage={onJump} />);
        await waitFor(() => expect(chips()).toEqual(['Coding', 'Commands3']));
        expect(document.querySelector('[data-section="commands"] .sp-section-head')).toHaveTextContent('1 failed');
        const rows = within(screen.getByRole('list', { name: 'Commands this chat ran, newest first' })).getAllByRole('listitem');
        expect(rows.map((li) => li.querySelector('code')?.textContent)).toEqual([
            'powershell -Command "git clone https://github.com/amd/gaia.git"',
            'winget install Git.Git --source winget --silent',
            'git clone https://github.com/amd/gaia.git C:\\Users\\me\\Work\\gaia',
        ]);
        expect(within(rows[0]).getByLabelText('Not run')).toBeInTheDocument();
        expect(within(rows[2]).getByLabelText('Failed')).toBeInTheDocument();
        fireEvent.click(within(rows[2]).getByRole('button'));
        expect(onJump).toHaveBeenCalledWith(2);
    });

    it('keeps the latest five in view and the rest behind Show more', () => {
        const many = Array.from({ length: 7 }, (_, i) => ({ key: `k${i}`, command: `echo ${i}`, state: 'ok' as const, messageId: 2 }));
        render(<CommandsSection commands={many} onJumpToMessage={() => true} />);
        expect(screen.getAllByRole('listitem')).toHaveLength(COMMANDS_SHOWN);
        expect(screen.getByText('echo 6')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Show 2 more' }));
        expect(screen.getAllByRole('listitem')).toHaveLength(7);
    });
});

describe('livePlan', () => {
    it('takes the latest plan and its plain-language lines', () => {
        const earlier: AgentStep = { ...PLAN, id: 0, planSteps: ['x'], planNarrations: ['Old'], currentStep: 0 };
        expect(livePlan([earlier, PLAN])).toEqual({
            steps: ['Read the config', 'Change the port', 'Run the tests'], statuses: ['done', 'running', 'pending'],
        });
    });

    it('is nothing for a turn that made no plan', () => {
        expect(livePlan([{ id: 1, type: 'tool', label: 'x', timestamp: 0 }])).toBeNull();
    });
});

describe('chips', () => {
    it('take you to the section they name', () => {
        render(<FocusBar facets={[{ kind: 'documents', count: 3, section: 'documents' }, { kind: 'chat' }]} />);
        fireEvent.click(screen.getByRole('button', { name: /Documents/ }));
        expect(useSessionPanelStore.getState().focus?.section).toBe('documents');
        // A chip with nowhere to go is a label, not a button.
        expect(screen.queryByRole('button', { name: /Chat/ })).not.toBeInTheDocument();
    });
});

describe('documents', () => {
    const entry = { path: 'C:\\docs\\plan.pdf', kind: 'file' as const, source: 'document' as const, removable: false };

    it('says which document the latest answer drew on', () => {
        const messages: Message[] = [QUESTION, {
            id: 2, session_id: SID, role: 'assistant', content: 'a', created_at: '', rag_sources: null,
            turnSummary: {
                pages: [], files: [], sites: [], live_data: { attempted: 0, failed: 0 }, cost: { kind: 'local' }, model: null,
                documents: [{ name: 'plan.pdf', path: entry.path, pages: [1], passages: 6 }],
            } as never,
        }];
        render(<DocumentsSection documents={[{ entry, doc: null }]} messages={messages} />);
        expect(screen.getByText('6 used')).toBeInTheDocument();
    });

    it('credits the document the answer used, not another with the same name', () => {
        const other = { ...entry, path: 'C:\\archive\\plan.pdf' };
        const messages: Message[] = [QUESTION, {
            id: 2, session_id: SID, role: 'assistant', content: 'a', created_at: '', rag_sources: null,
            turnSummary: {
                pages: [], files: [], sites: [], live_data: { attempted: 0, failed: 0 }, cost: { kind: 'local' }, model: null,
                documents: [{ name: 'plan.pdf', path: 'c:/docs/plan.pdf', pages: [1], passages: 6 }],
            } as never,
        }];
        render(<DocumentsSection documents={[{ entry, doc: null }, { entry: other, doc: null }]} messages={messages} />);
        expect(screen.getAllByText('6 used')).toHaveLength(1);
        expect(within(screen.getAllByRole('listitem')[0]).getByText('6 used')).toBeInTheDocument();
    });

    it('says when a document is still being indexed or failed', () => {
        const doc = { id: 'd', filename: 'plan.pdf', filepath: entry.path, file_size: 1, chunk_count: 0, indexed_at: '', last_accessed_at: null, sessions_using: 1 };
        const { rerender } = render(<DocumentsSection documents={[{ entry, doc: { ...doc, indexing_status: 'indexing' } }]} messages={[]} />);
        expect(screen.getByText('Indexing')).toBeInTheDocument();
        rerender(<DocumentsSection documents={[{ entry, doc: { ...doc, indexing_status: 'failed', last_error: 'The file is encrypted.' } }]} messages={[]} />);
        expect(screen.getByText('Failed')).toBeInTheDocument();
        expect(screen.getByText('The file is encrypted.')).toBeInTheDocument();
    });
});

describe('sections with nothing to show', () => {
    it('render nothing', () => {
        const { container } = render(
            <>
                <PlanSection plan={null} />
                <ScheduleSection schedules={[]} />
                <DocumentsSection documents={[]} messages={[]} />
                <CommandsSection commands={[]} onJumpToMessage={() => true} />
            </>,
        );
        expect(container).toBeEmptyDOMElement();
    });
});

describe('easing in', () => {
    it('animates only what turns up after the panel has settled', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
        const { rerender } = render(<Panel />);
        await waitFor(() => expect(screen.getByRole('button', { name: /^Environment/ })).toBeInTheDocument());
        // There when the panel opened: no animation.
        expect(document.querySelector('[data-section="environment"]')).not.toHaveClass('sp-appear');

        await act(async () => { await vi.advanceTimersByTimeAsync(SETTLE_MS + 50); });
        useChatStore.setState({ agentSteps: [PLAN] });
        rerender(<Panel busy />);
        expect(await screen.findByRole('progressbar', { name: 'Plan' })).toBeInTheDocument();
        expect(document.querySelector('[data-section="plan"]')).toHaveClass('sp-appear');
        expect(document.querySelector('[data-section="environment"]')).not.toHaveClass('sp-appear');
    });

    it('a section mounted into a settled panel eases in', () => {
        render(
            <PanelSettled.Provider value>
                <PlanSection plan={{ steps: ['One'], statuses: ['running'] }} />
            </PanelSettled.Provider>,
        );
        expect(document.querySelector('[data-section="plan"]')).toHaveClass('sp-appear');
    });
});
