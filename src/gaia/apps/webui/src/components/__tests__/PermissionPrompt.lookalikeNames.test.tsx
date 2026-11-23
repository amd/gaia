// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PermissionPrompt } from '../PermissionPrompt';
import { visibleCommand, visibleStrict, visibleText } from '../CalendarApproval';
import { useNotificationStore } from '../../stores/notificationStore';
import type { GaiaNotification } from '../../types/agent';
import parity from './fixtures/approval-name-parity.json';

const originalRespond = useNotificationStore.getState().respondToPermission;

function request(over: Partial<GaiaNotification> = {}): GaiaNotification {
    return {
        id: 'perm-1', type: 'permission_request', agentId: 'chat-1', sessionId: 'chat-1',
        agentName: 'GAIA', title: 'Allow run_shell_command?', message: '', timestamp: 1,
        read: false, dismissed: false, priority: 'high', tool: 'run_shell_command',
        toolArgs: { command: 'git status' }, ...over,
    };
}

beforeEach(() => {
    useNotificationStore.setState({ notifications: [], respondToPermission: vi.fn().mockResolvedValue(undefined) });
});
afterEach(() => {
    useNotificationStore.setState({ notifications: [], respondToPermission: originalRespond });
});

describe('a name on an approval card shows every character that could pass it off as another', () => {
    it.each([
        ['ZWJ', 'inv\u200doice.txt', 'inv[U+200D]oice.txt'],
        ['ZWNJ', 'inv\u200coice.txt', 'inv[U+200C]oice.txt'],
        ['LRM', 'report\u200e.txt', 'report[U+200E].txt'],
        ['RLM', 'report\u200f.txt', 'report[U+200F].txt'],
        ['emoji and VS16', '\u{1F4C1}\ufe0fnotes.txt', '\u{1F4C1}[U+FE0F]notes.txt'],
        ['CJK and VS17', '\u845b\u{E0100}.txt', '\u845b[U+E0100].txt'],
        ['CRLF', 'a\r\nb.txt', 'a[U+000D][U+000A]b.txt'],
        ['tab', 'a\tb.txt', 'a[U+0009]b.txt'],
    ])('in a file path (%s)', (_name, name, shown) => {
        useNotificationStore.getState().addNotification(request({
            tool: 'write_file', toolLabel: 'writing file',
            toolArgs: { file_path: `/home/u/${name}`, content: 'hi' },
        }));
        render(<PermissionPrompt sessionId="chat-1" />);
        expect(document.querySelector('.perm-card-detail code')?.textContent).toBe(`/home/u/${shown}`);
        expect(screen.getByTestId('perm-hidden-note')).toBeInTheDocument();
    });

    it('in a program name, on the card and on its always button', () => {
        useNotificationStore.getState().addNotification(request({
            toolArgs: { command: 'gi\u200dt status' }, alwaysScope: 'gi\u200dt status',
        }));
        render(<PermissionPrompt sessionId="chat-1" />);
        expect(document.querySelector('.perm-card-detail code')?.textContent).toBe('gi[U+200D]t status');
        expect(screen.getByRole('button', { name: 'Always allow gi[U+200D]t status in this chat' })).toBeInTheDocument();
        expect(screen.getByTestId('perm-hidden-note')).toBeInTheDocument();
    });

    it('in a command, while its line breaks and indents stay layout', () => {
        useNotificationStore.getState().addNotification(request({
            toolArgs: { command: 'echo a\n\tgi\u200ct log' },
        }));
        render(<PermissionPrompt sessionId="chat-1" />);
        expect(document.querySelector('.perm-card-detail code')?.textContent).toBe('echo a\n\tgi[U+200C]t log');
    });

    it('but code keeps emoji joiner sequences as written', () => {
        const family = '\u{1F468}\u200d\u{1F469}\u200d\u{1F467}';
        useNotificationStore.getState().addNotification(request({
            tool: 'run_python', toolArgs: { code: `print("${family}")\nprint("\u2764\ufe0f")` },
        }));
        render(<PermissionPrompt sessionId="chat-1" />);
        expect(screen.getByTestId('perm-code').textContent).toBe(`print("${family}")\nprint("\u2764\ufe0f")`);
        expect(screen.queryByTestId('perm-hidden-note')).not.toBeInTheDocument();
    });

    it('a plain name and command are unchanged', () => {
        expect(visibleStrict('/home/u/notes.txt')).toBe('/home/u/notes.txt');
        expect(visibleCommand('git status\n\tgit log')).toBe('git status\n\tgit log');
        // Prose is unchanged by this rule: the joiners Persian needs stay as written.
        expect(visibleText('\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645', false)).toBe('\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645');
    });
});

describe('the name rule matches the Python agent', () => {
    it.each(parity.map((c) => [JSON.stringify(c.input), c] as const))('shows %s as visible_strict does', (_label, { input, shown }) => {
        expect(visibleStrict(input)).toBe(shown);
    });
});

describe('the expanded arguments view marks name-like args like the main field', () => {
    it('codes a joiner in a file path, while prose stays as written', () => {
        const prose = '\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645';
        useNotificationStore.getState().addNotification(request({
            tool: 'write_file',
            toolArgs: { file_path: '/home/u/inv\u200doice.txt', content: prose },
        }));
        render(<PermissionPrompt sessionId="chat-1" />);
        const main = document.querySelector('.perm-card-detail code')?.textContent;
        expect(main).toBe('/home/u/inv[U+200D]oice.txt');
        fireEvent.click(screen.getByRole('button', { name: 'Technical details' }));
        const shown = document.querySelector('.perm-card-args pre')?.textContent ?? '';
        expect(shown).toContain('/home/u/inv[U+200D]oice.txt');
        expect(shown).toContain(prose);
    });

    it('codes a joiner in a command, while its line breaks and tabs keep their layout', () => {
        useNotificationStore.getState().addNotification(request({
            toolArgs: { command: 'echo a\n\tgi\u200ct log' },
        }));
        render(<PermissionPrompt sessionId="chat-1" />);
        fireEvent.click(screen.getByRole('button', { name: 'Details' }));
        const shown = document.querySelector('.perm-card-args pre')?.textContent ?? '';
        expect(shown).toContain('echo a\\n\\tgi[U+200C]t log');
    });
});

describe('the expanded view finds name-like args by their key shape', () => {
    const prose = '\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645';
    const family = '\u{1F468}\u200d\u{1F469}\u200d\u{1F467}';

    function expanded(toolArgs: Record<string, unknown>): string {
        useNotificationStore.getState().addNotification(request({ tool: 'mcp_tool', toolArgs }));
        render(<PermissionPrompt sessionId="chat-1" />);
        fireEvent.click(screen.getByRole('button', { name: 'Details' }));
        return document.querySelector('.perm-card-args pre')?.textContent ?? '';
    }

    it.each([
        ['output_path'], ['target_dir'], ['output_dir'], ['directory'], ['folder'], ['cwd'],
        ['src_path'], ['dest_path'], ['image_path'], ['filename'], ['filePath'], ['targetDir'],
        ['session_id'], ['userId'], ['tool_name'], ['repo_url'],
    ])('codes a joiner in %s', (key) => {
        expect(expanded({ [key]: 'inv\u200doice.txt' })).toContain('inv[U+200D]oice.txt');
    });

    it.each([
        ['source'], ['destination'], ['target'], ['dest'], ['src'], ['to'], ['from'], ['href'], ['location'],
        ['branch'], ['repo'], ['guid'], ['uuid'], ['path2'], ['baseURI'], ['HTTPUrl'], ['filename_hint'],
    ])('codes a joiner in %s too', (key) => {
        expect(expanded({ [key]: 'inv\u200doice.txt' })).toContain('inv[U+200D]oice.txt');
    });

    it.each([['cmd'], ['argv']])('reads %s with the command rule, keeping its layout', (key) => {
        const value = key === 'argv' ? ['echo', 'a\n\tgi\u200ct'] : 'echo a\n\tgi\u200ct log';
        const shown = expanded({ [key]: value });
        expect(shown).toContain('a\\n\\tgi[U+200C]t');
        expect(shown).not.toContain('[U+000A]');
    });

    it('codes each entry of a list of paths', () => {
        const shown = expanded({ file_paths: ['a\u200d.txt', 'b\u200e.txt'], watch_directories: ['c\u200c'] });
        expect(shown).toContain('a[U+200D].txt');
        expect(shown).toContain('b[U+200E].txt');
        expect(shown).toContain('c[U+200C]');
    });

    it('codes a path nested in a list of edits, but not the text beside it', () => {
        const shown = expanded({ edits: [{ path: 'x\u200d.py', new_text: prose }] });
        expect(shown).toContain('x[U+200D].py');
        expect(shown).toContain(prose);
    });

    it.each([
        ['content'], ['query'], ['text'], ['message'], ['new_text'], ['_risk_note'], ['valid'], ['paid'], ['profile'],
    ])('keeps prose joiners in %s as written', (key) => {
        expect(expanded({ [key]: `${prose} ${family}` })).toContain(`${prose} ${family}`);
    });

    it('leaves numbers, booleans and null alone', () => {
        const shown = expanded({ id: 7, file_path: null, recursive: true });
        expect(shown).toContain('"id": 7');
        expect(shown).toContain('"file_path": null');
        expect(shown).toContain('"recursive": true');
    });
});
