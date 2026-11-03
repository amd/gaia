// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PermissionPrompt } from '../PermissionPrompt';
import { useChatStore } from '../../stores/chatStore';
import { useNotificationStore } from '../../stores/notificationStore';
import type { GaiaNotification } from '../../types/agent';
import parity from './fixtures/approval-text-parity.json';

const originalRespond = useNotificationStore.getState().respondToPermission;
const respond = vi.fn();

function request(over: Partial<GaiaNotification> = {}): GaiaNotification {
    return {
        id: 'perm-1', type: 'permission_request', agentId: 'chat-1', sessionId: 'chat-1',
        agentName: 'GAIA', title: 'Allow run_shell_command?', message: '', timestamp: 1,
        read: false, dismissed: false, priority: 'high', tool: 'run_shell_command',
        toolArgs: { command: 'git status' }, ...over,
    };
}

beforeEach(() => {
    respond.mockReset().mockResolvedValue(undefined);
    useNotificationStore.setState({ notifications: [], respondToPermission: respond });
});
afterEach(() => {
    useNotificationStore.setState({ notifications: [], respondToPermission: originalRespond });
});

describe('PermissionPrompt', () => {
    it('shows short code whole, with no expand control', () => {
        useNotificationStore.getState().addNotification(request({
            tool: 'run_python', toolArgs: { code: 'print(1)\nprint(2)' },
            summary: 'print(1)\nprint(2)',
        }));
        render(<PermissionPrompt sessionId="chat-1" />);
        expect(screen.getByTestId('perm-code').textContent).toBe('print(1)\nprint(2)');
        expect(screen.queryByRole('button', { name: /Show all/ })).not.toBeInTheDocument();
    });

    it('collapses long code and shows every line on "Show all"', () => {
        const code = Array.from({ length: 30 }, (_, i) => `line_${i + 1} = ${i + 1}`).join('\n');
        useNotificationStore.getState().addNotification(request({
            tool: 'run_python', toolArgs: { code },
            // What a relayed agent's summary looks like: the middle elided.
            summary: 'line_1 = 1\n…[18 more lines]…\nline_30 = 30',
        }));
        render(<PermissionPrompt sessionId="chat-1" />);
        const block = screen.getByTestId('perm-code');
        expect(block.textContent).toContain('line_12 = 12');
        expect(block.textContent).not.toContain('line_13 = 13');
        // The code block replaces the shortened summary rather than repeating it.
        expect(screen.getByRole('alertdialog')).not.toHaveTextContent('more lines');
        fireEvent.click(screen.getByRole('button', { name: 'Show all 30 lines' }));
        expect(screen.getByTestId('perm-code').textContent).toBe(code);
        expect(screen.getByRole('button', { name: 'Show less' })).toHaveAttribute('aria-expanded', 'true');
    });

    it('splits a relayed command summary with no args into the command and its risk note', () => {
        useNotificationStore.getState().addNotification(request({
            toolArgs: undefined,
            summary: 'powershell -Command "Get-ChildItem"\nCan run any code, with your permissions.',
        }));
        render(<PermissionPrompt sessionId="chat-1" />);
        expect(document.querySelector('.perm-card-exact')?.textContent).toBe('powershell -Command "Get-ChildItem"');
        expect(screen.getByRole('note')).toHaveTextContent('Can run any code, with your permissions.');
    });

    it("keeps a relayed command's folder, extras and note apart, as the server sends them", () => {
        useNotificationStore.getState().addNotification(request({
            toolArgs: undefined,
            summary: 'Get-ChildItem\nin C:\\Users\\me\n(timeout=30)\nCan run any code, with your permissions.',
        }));
        render(<PermissionPrompt sessionId="chat-1" />);
        expect(document.querySelector('.perm-card-exact')?.textContent).toBe('Get-ChildItem');
        expect(screen.getByRole('note').textContent).toBe('Can run any code, with your permissions.');
        expect(screen.getByText('C:\\Users\\me').tagName).toBe('CODE');
        expect(screen.getByText('(timeout=30)')).toBeInTheDocument();
    });

    it('keeps a multi-line relayed command whole in the code block', () => {
        useNotificationStore.getState().addNotification(request({
            toolArgs: undefined,
            summary: "cat <<'EOF' > notes.txt\nin the morning\nEOF\nin /home/me\nCan run any code, with your permissions.",
        }));
        render(<PermissionPrompt sessionId="chat-1" />);
        expect(document.querySelector('.perm-card-exact')?.textContent).toBe("cat <<'EOF' > notes.txt\nin the morning\nEOF");
        expect(screen.getByText('/home/me').tagName).toBe('CODE');
    });

    it('reads a relayed command with no risk note: the folder is last', () => {
        useNotificationStore.getState().addNotification(request({
            toolArgs: undefined,
            summary: 'git status\nin /repo',
        }));
        render(<PermissionPrompt sessionId="chat-1" />);
        expect(document.querySelector('.perm-card-exact')?.textContent).toBe('git status');
        expect(screen.getByText('/repo').tagName).toBe('CODE');
        expect(screen.queryByRole('note')).not.toBeInTheDocument();
    });

    it('keeps a no-args summary for other tools as plain text', () => {
        useNotificationStore.getState().addNotification(request({
            tool: 'send_email', toolArgs: undefined, summary: 'Send to a@b.c\nSubject: hi',
        }));
        render(<PermissionPrompt sessionId="chat-1" />);
        expect(document.querySelector('.perm-card-exact')).toBeNull();
        expect(document.querySelector('.perm-card-detail')?.textContent).toBe('Send to a@b.c\nSubject: hi');
    });

    it('renders nothing when this chat has no pending request', () => {
        useNotificationStore.getState().addNotification(request({ sessionId: 'other-chat' }));
        const { container } = render(<PermissionPrompt sessionId="chat-1" />);
        expect(container).toBeEmptyDOMElement();
    });

    it('offers allow once, always (with its scope) and deny', () => {
        useNotificationStore.getState().addNotification(request({ alwaysScope: 'git status' }));
        render(<PermissionPrompt sessionId="chat-1" />);
        expect(screen.getByRole('alertdialog')).toHaveTextContent('Allow GAIA to use run_shell_command?');
        expect(screen.getByRole('button', { name: 'Allow once' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Always allow git status in this chat' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Deny' })).toBeInTheDocument();
    });

    it('words the question and the always option without tool names', () => {
        useNotificationStore.getState().addNotification(request({
            tool: 'write_file',
            toolLabel: 'writing file',
            toolArgs: { file_path: '/home/u/prompt-test.txt', content: 'hi' },
            alwaysScope: 'write_file on prompt-test.txt',
            alwaysLabel: 'writing prompt-test.txt',
        }));
        render(<PermissionPrompt sessionId="chat-1" />);
        const dialog = screen.getByRole('alertdialog');
        expect(screen.getByRole('heading')).toHaveTextContent('Allow writing file?');
        expect(screen.getByRole('button', { name: 'Always allow writing prompt-test.txt in this chat' })).toBeInTheDocument();
        expect(dialog).not.toHaveTextContent('write_file on');
    });

    it("shows a relayed agent's summary when the call came without args", () => {
        const summary = `Run 'write_file' with file_path="notes.md"?`;
        useNotificationStore.getState().addNotification(request({ tool: 'write_file', toolArgs: undefined, summary }));
        render(<PermissionPrompt sessionId="chat-1" />);
        expect(screen.getByRole('alertdialog')).toHaveTextContent(summary);
    });

    it('prefers the args over the summary when both are present', () => {
        useNotificationStore.getState().addNotification(request({ summary: 'should not show' }));
        render(<PermissionPrompt sessionId="chat-1" />);
        expect(screen.getByRole('alertdialog')).toHaveTextContent('git status');
        expect(screen.getByRole('alertdialog')).not.toHaveTextContent('should not show');
    });

    it('shows a one-time call verbatim with its folder and risk note, and no always', () => {
        const command = 'git push   origin main';
        useNotificationStore.getState().addNotification(request({
            toolArgs: {
                command,
                working_directory: '/home/u/proj',
                _risk_note: 'Sends your commits to the remote repository.',
            },
        }));
        render(<PermissionPrompt sessionId="chat-1" />);
        const dialog = screen.getByRole('alertdialog');
        // Unwrapped and uncollapsed: the exact text the shell receives.
        expect(dialog.querySelector('pre.perm-card-exact')?.textContent).toBe(command);
        expect(dialog).toHaveTextContent('in /home/u/proj');
        expect(screen.getByRole('note')).toHaveTextContent('Sends your commits to the remote repository.');
        expect(screen.queryByRole('button', { name: /Always allow/ })).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Allow once' })).toBeInTheDocument();
    });

    it('offers no always option without a scope', () => {
        useNotificationStore.getState().addNotification(request());
        render(<PermissionPrompt sessionId="chat-1" />);
        expect(screen.queryByRole('button', { name: /Always allow/ })).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Allow once' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Deny' })).toBeInTheDocument();
    });

    it.each([
        ['Allow once', 'allow'],
        [/Always allow/, 'always'],
        ['Deny', 'deny'],
    ] as const)('clicking %s answers %s', async (name, decision) => {
        useNotificationStore.getState().addNotification(request({ alwaysScope: 'git status' }));
        render(<PermissionPrompt sessionId="chat-1" />);
        fireEvent.click(screen.getByRole('button', { name }));
        await waitFor(() => expect(respond).toHaveBeenCalledExactlyOnceWith('perm-1', decision));
    });

    it('shows why the answer failed and lets the user try again', async () => {
        respond.mockRejectedValueOnce(new Error('GAIA is not reachable'));
        useNotificationStore.getState().addNotification(request());
        render(<PermissionPrompt sessionId="chat-1" />);

        fireEvent.click(screen.getByRole('button', { name: 'Allow once' }));

        expect(await screen.findByRole('alert')).toHaveTextContent('GAIA is not reachable');
        const allow = screen.getByRole('button', { name: 'Allow once' });
        await waitFor(() => expect(allow).toBeEnabled());
        fireEvent.click(allow);
        await waitFor(() => expect(respond).toHaveBeenCalledTimes(2));
    });

    it('shows the newest pending request, the one the agent is waiting on', () => {
        const add = useNotificationStore.getState().addNotification;
        add(request({ id: 'first', tool: 'write_file' }));
        add(request({ id: 'second', tool: 'delete_file' }));
        render(<PermissionPrompt sessionId="chat-1" />);
        expect(screen.getByRole('alertdialog')).toHaveTextContent('delete_file');
    });

    describe('calendar writes show what is being approved without a click', () => {
        const ask = (tool: string, toolArgs: Record<string, unknown>) => {
            useNotificationStore.getState().addNotification(request({ tool, toolArgs }));
            render(<PermissionPrompt sessionId="chat-1" />);
            return screen.getByTestId('perm-calendar');
        };
        const local = (iso: string) => new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit' }).format(new Date(iso));

        it('create: title, readable time, account and everyone notified', () => {
            const facts = ask('create_calendar_event', {
                title: 'Budget review', start: '2026-11-03T09:00:00-05:00', end: '2026-11-03T10:00:00-05:00',
                notify_attendees: ['dana@x.com', 'sam@x.com'], calendar_account: 'me@x.com',
                location: 'Room 4', description: 'Bring the Q3 numbers',
            });
            expect(facts).toHaveTextContent('Budget review');
            expect(facts).toHaveTextContent('Nov 3, 2026');
            expect(facts.textContent).toContain(local('2026-11-03T14:00:00Z').replace(/\s?[AP]M/, ''));
            expect(facts).toHaveTextContent('me@x.com');
            expect(facts).toHaveTextContent('Notifies 2 people');
            expect(facts).toHaveTextContent('dana@x.com');
            expect(facts).toHaveTextContent('sam@x.com');
            expect(facts).toHaveTextContent('Room 4');
            expect(facts).toHaveTextContent('Bring the Q3 numbers');
        });

        it('dates follow the interface language', () => {
            act(() => useChatStore.getState().setLocale('ar'));
            try {
                const facts = ask('create_calendar_event', {
                    title: 'Budget review', start: '2026-11-03T09:00:00-05:00', end: '2026-11-03T10:00:00-05:00',
                    notify_attendees: [], calendar_account: 'me@x.com',
                });
                expect(facts).toHaveTextContent('نوفمبر');
                expect(facts).not.toHaveTextContent('Nov');
            } finally {
                act(() => useChatStore.getState().setLocale('en'));
            }
        });

        it('update: the event and what changes', () => {
            const facts = ask('update_calendar_event', {
                event_id: 'abc', expected_title: 'Budget review', expected_start: '2026-11-03T14:00:00Z',
                notify_attendees: ['dana@x.com'], new_title: 'Budget sync',
                new_start: '2026-11-04T14:00:00Z', new_end: '2026-11-04T15:00:00Z',
                new_location: 'Room 9', new_description: 'New agenda',
            });
            expect(facts).toHaveTextContent('Budget review');
            expect(facts).toHaveTextContent('New title');
            expect(facts).toHaveTextContent('Budget sync');
            expect(facts).toHaveTextContent('New time');
            expect(facts).toHaveTextContent('Nov 4, 2026');
            expect(facts).toHaveTextContent('Room 9');
            expect(facts).toHaveTextContent('New agenda');
            expect(facts).toHaveTextContent('Notifies 1 person');
        });

        it('cancel: says plainly when nobody is told', () => {
            const facts = ask('cancel_calendar_event', {
                event_id: 'abc', expected_title: 'Budget review', expected_start: '2026-11-03T14:00:00Z', notify_attendees: [],
            });
            expect(facts).toHaveTextContent('Budget review');
            expect(facts).toHaveTextContent('Nobody is notified');
            expect(facts).not.toHaveTextContent('abc');
        });

        it('an all-day date stays on its own day in any zone', () => {
            const facts = ask('create_calendar_event', {
                title: 'Day off', start: '2026-11-03', end: '2026-11-04', notify_attendees: [], calendar_account: 'me@x.com',
            });
            expect(facts).toHaveTextContent(/Nov 3.*Nov 4/);
            expect(facts).toHaveTextContent('all day');
        });

        it('hides the untrusted-text markers a model copied into the call', () => {
            const facts = ask('cancel_calendar_event', {
                event_id: 'abc', expected_title: '<<<UNTRUSTED_TEXT>>>Budget review<<<END_UNTRUSTED_TEXT>>>',
                expected_start: '2026-11-03T14:00:00Z', notify_attendees: [],
            });
            expect(facts.textContent).not.toContain('UNTRUSTED');
            expect(facts).toHaveTextContent('Budget review');
        });

        it('shows characters that hide or reorder text as visible codes', () => {
            const facts = ask('create_calendar_event', {
                title: 'Budget \u202eweiver', start: '2026-11-03T09:00:00-05:00', end: '2026-11-03T10:00:00-05:00',
                notify_attendees: ['dana@x.com'], calendar_account: 'me@x.com',
                location: 'Room\u200b 4', description: 'Agenda\u2066 here\u2069',
            });
            const shown = facts.textContent ?? '';
            expect(shown).toContain('Budget [U+202E]weiver');
            expect(shown).toContain('Room[U+200B] 4');
            expect(shown).toContain('Agenda[U+2066] here[U+2069]');
            expect(shown).not.toMatch(/[\u202e\u200b\u2066\u2069]/);
        });

        it('keeps the joiners right-to-left writing needs', () => {
            const facts = ask('create_calendar_event', {
                title: 'می\u200cخواهم', start: '2026-11-03T09:00:00-05:00', end: '2026-11-03T10:00:00-05:00',
                notify_attendees: [], calendar_account: 'me@x.com',
            });
            expect(facts.textContent).toContain('می\u200cخواهم');
        });

        it('shows every format character, and the blank-looking letters, as codes', () => {
            const kept = new Set([0x200c, 0x200d, 0x200e, 0x200f]);
            const chars: number[] = [];
            for (let cp = 0; cp <= 0x10ffff; cp++) {
                if (cp >= 0xd800 && cp <= 0xdfff) continue;
                if (!kept.has(cp) && /^\p{Cf}$/u.test(String.fromCodePoint(cp))) chars.push(cp);
            }
            chars.push(0x034f, 0x115f, 0x1160, 0x2800, 0x3164, 0xffa0, 0x0085, 0x2028);
            expect(chars).toEqual(expect.arrayContaining([0xe0041, 0x061c, 0x206a, 0xfff9, 0x0600]));
            const facts = ask('create_calendar_event', {
                title: `${chars.map((cp) => `x${String.fromCodePoint(cp)}`).join('')}x`,
                start: '2026-11-03T09:00:00-05:00', end: '2026-11-03T10:00:00-05:00',
                notify_attendees: [], calendar_account: 'me@x.com',
            });
            const shown = facts.textContent ?? '';
            for (const cp of chars) {
                expect(shown).toContain(`x[U+${cp.toString(16).toUpperCase().padStart(4, '0')}]`);
                expect(shown).not.toContain(String.fromCodePoint(cp));
            }
        });

        it('shows a line break in a one-line field', () => {
            const facts = ask('create_calendar_event', {
                title: 'Lunch', start: '2026-11-03T09:00:00-05:00', end: '2026-11-03T10:00:00-05:00',
                notify_attendees: [], calendar_account: 'me@x.com', location: 'A\nB',
            });
            expect(facts.textContent).toContain('A[U+000A]B');
        });

        it('shows the addresses GAIA checked, as given', () => {
            const facts = ask('cancel_calendar_event', {
                event_id: 'abc', expected_title: 'Sync', expected_start: '2026-11-03T14:00:00Z',
                notify_attendees: ['ceo@xn--pple-43d.com', 'dana@exa\u200bmple.com', 'sam@x.com'],
            });
            const shown = facts.textContent ?? '';
            expect(shown).toContain('ceo@xn--pple-43d.com');
            expect(shown).toContain('dana@exa[U+200B]mple.com');
            expect(shown).toContain('sam@x.com');
        });

        it("leaves GAIA's own note to say what it found", () => {
            const facts = ask('create_calendar_event', {
                title: 'a‮', start: '2026-11-03T09:00:00-05:00', end: '2026-11-03T10:00:00-05:00',
                notify_attendees: [], calendar_account: 'me@x.com', _risk_note: 'From GAIA.',
            });
            expect(facts).toBeTruthy();
            expect(screen.queryByTestId('perm-calendar-unseen')).toBeNull();
        });

        it('keeps a byte-order mark at either end visible', () => {
            const facts = ask('create_calendar_event', {
                title: '\ufeffBudget\ufeff', start: '2026-11-03T09:00:00-05:00', end: '2026-11-03T10:00:00-05:00',
                notify_attendees: [], calendar_account: 'me@x.com',
            });
            expect(facts.textContent).toContain('[U+FEFF]Budget[U+FEFF]');
        });

        it.each(parity.map((c) => [JSON.stringify(c.input), c] as const))('shows %s exactly as GAIA does', (_label, { input, shown, unseen }) => {
            const facts = ask('create_calendar_event', {
                title: input, start: '2026-11-03T09:00:00-05:00', end: '2026-11-03T10:00:00-05:00',
                notify_attendees: [], calendar_account: 'me@x.com',
            });
            const title = [...facts.querySelectorAll('dt')].find((dt) => dt.textContent === 'Title');
            expect(title?.nextElementSibling?.textContent).toBe(shown);
            expect(screen.queryByTestId('perm-calendar-unseen') !== null).toBe(unseen);
        });

        it('an unreadable time still shows, as written', () => {
            const facts = ask('cancel_calendar_event', {
                event_id: 'abc', expected_title: 'Sync', expected_start: 'tomorrow at nine', notify_attendees: [],
            });
            expect(facts).toHaveTextContent('tomorrow at nine');
        });

        it('a relayed prompt with no args keeps the lines of its summary', () => {
            useNotificationStore.getState().addNotification(request({
                tool: 'cancel_calendar_event', toolArgs: undefined,
                summary: 'Budget review\nWhen: 2026-11-03T14:00:00Z\nNotifies: dana@x.com',
            }));
            render(<PermissionPrompt sessionId="chat-1" />);
            expect(screen.getByTestId('perm-exact-summary').textContent).toContain('\nNotifies: dana@x.com');
        });
    });
});
