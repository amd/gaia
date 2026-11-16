// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// In Arabic the code a prompt asks about still reads left to right, exactly as it runs.

import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { PermissionPrompt } from '../PermissionPrompt';
import { useNotificationStore, PATH_ACCESS_TOOL } from '../../stores/notificationStore';
import { useChatStore } from '../../stores/chatStore';
import type { GaiaNotification } from '../../types/agent';
import { stripIsolates } from '../../i18n/registry';

const CODE = 'p = r"C:\\Users\\you\\notes.txt"\nt = open(p, encoding="utf-8").read()';

function prompt(over: Partial<GaiaNotification>): void {
    useNotificationStore.getState().addNotification({
        id: 'p1', type: 'permission_request', agentId: 's', sessionId: 's', agentName: 'GAIA',
        title: '', message: '', timestamp: 1, read: false, dismissed: false, priority: 'high',
        ...over,
    } as GaiaNotification);
    render(<PermissionPrompt sessionId="s" />);
}

beforeEach(() => {
    useChatStore.setState({ locale: 'ar' });
    document.documentElement.dir = 'rtl';
    useNotificationStore.setState({ notifications: [] });
});
afterEach(() => {
    useChatStore.setState({ locale: 'en' });
    document.documentElement.dir = 'ltr';
    useNotificationStore.setState({ notifications: [] });
});

describe('permission prompt in Arabic', () => {
    it('keeps the code to run left to right, and the details too', () => {
        prompt({ tool: 'run_python', toolLabel: 'running python', toolArgs: { code: CODE } });
        expect(screen.getByTestId('perm-code')).toHaveAttribute('dir', 'ltr');
        fireEvent.click(screen.getByRole('button', { name: /Details/ }));
        for (const pre of document.querySelectorAll('pre')) expect(pre, pre.outerHTML).toHaveAttribute('dir', 'ltr');
    });

    it('asks in Arabic, the tool kept left to right', () => {
        prompt({ tool: 'some_new_tool', toolLabel: 'editing file', toolArgs: { file_path: 'a.txt', old_content: 'a', new_content: 'b' } });
        const title = screen.getByRole('heading');
        expect(title.textContent).toMatch(/^⁧?هل تسمح بـ/);
        expect(title.querySelector('bdi[dir="ltr"]')).toHaveTextContent(/^editing file$/);
        expect(stripIsolates(title.querySelector('.i18n-gloss')!.textContent!)).toBe('(Allow editing file?)');
    });

    it('keeps a shell command, its folder and its always-allow scope left to right', () => {
        prompt({
            tool: 'run_shell_command', toolLabel: 'running a command', alwaysScope: 'git status',
            toolArgs: { command: 'git status --short', working_directory: 'C:\\repo', _risk_note: 'Runs outside the sandbox.' },
        });
        const card = screen.getByRole('alertdialog');
        expect(card.querySelector('.perm-card-exact')).toHaveAttribute('dir', 'ltr');
        const codes = [...card.querySelectorAll('code')];
        expect(codes.map((c) => c.textContent)).toContain('C:\\repo');
        for (const c of codes) expect(c, c.outerHTML).toHaveAttribute('dir', 'ltr');
        // The scope sits in the translated label as its own first-strong isolate, so it keeps its order.
        expect(screen.getByRole('button', { name: /Always allow/ }).textContent).toContain('\u2068git status\u2069');
    });

    it('labels its buttons in Arabic, with the English still there to find them by', () => {
        useNotificationStore.getState().addNotification({
            id: 'earlier', type: 'permission_request', agentId: 's', sessionId: 's', agentName: 'GAIA',
            title: '', message: '', timestamp: 0, read: false, dismissed: false, priority: 'high',
            tool: 'run_shell_command', alwaysScope: 'git status', toolArgs: { command: 'git status' }, response: 'allow',
        } as GaiaNotification);
        prompt({
            tool: 'run_shell_command', toolLabel: 'running a command', alwaysScope: 'git status',
            toolArgs: { command: 'git status' }, timeoutSeconds: 60,
        });
        const arabic = /\p{Script=Arabic}/u;
        for (const [ar, en] of [['السماح مرة واحدة', 'Allow once'], ['رفض', 'Deny'], ['التفاصيل', 'Details'], ['السماح دائمًا', 'Always allow']]) {
            const button = screen.getByRole('button', { name: new RegExp(en) });
            expect(button).toHaveTextContent(ar);
            expect(button.querySelector('.i18n-gloss')).toHaveTextContent(en);
        }
        expect(screen.getByRole('button', { name: /Always allow/ })).toHaveTextContent('git status');
        expect(document.querySelector('.perm-card-hint')?.textContent).toMatch(arabic);
        expect(document.querySelector('.perm-card-timer')?.getAttribute('title')).toMatch(arabic);
    });

    it('labels a path prompt\'s allow button in Arabic', () => {
        prompt({ tool: PATH_ACCESS_TOOL, title: 'Let GAIA use this folder?', toolArgs: { path: 'C:\\data' } });
        expect(screen.getByRole('button', { name: /Allow for this chat/ })).toHaveTextContent('السماح في هذه المحادثة');
    });

    it('labels a long code block\'s toggle in Arabic', () => {
        const long = Array.from({ length: 20 }, (_, i) => `print(${i})`).join('\n');
        prompt({ tool: 'run_python', toolLabel: 'running python', toolArgs: { code: long } });
        fireEvent.click(screen.getByRole('button', { name: /Show all 20 lines/ }));
        expect(screen.getByRole('button', { name: /Show less/ })).toHaveTextContent('عرض أقل');
    });

    it('keeps a relayed command left to right', () => {
        prompt({ tool: 'run_shell_command', summary: 'dir /b "C:\\data"\nin C:\\data\nLists files.' });
        expect(screen.getByRole('alertdialog').querySelector('.perm-card-exact')).toHaveAttribute('dir', 'ltr');
    });

    it('asks about a path in Arabic, the path kept left to right', () => {
        prompt({ tool: PATH_ACCESS_TOOL, toolArgs: { path: 'C:\\data\\reports', kind: 'folder' } });
        const card = screen.getByRole('alertdialog');
        expect(card.querySelector('.perm-card-detail code')).toHaveAttribute('dir', 'ltr');
        expect(screen.getByRole('heading')).toHaveTextContent('هل تسمح لـ GAIA باستخدام هذا المجلد؟');
        const question = card.querySelector('.perm-card-question')!;
        expect(question).toHaveTextContent('يريد GAIA استخدام المجلد');
        expect(question.querySelector('bdi[dir="ltr"]')).toHaveTextContent(/^C:\\data\\reports$/);
    });
});
