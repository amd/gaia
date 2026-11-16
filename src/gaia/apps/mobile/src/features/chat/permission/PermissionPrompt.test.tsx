// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act } from 'react';
import { createRoot } from 'test-renderer';

import type { GaiaNotification } from '@webui/types/agent';

import { PermissionPrompt } from './PermissionPrompt';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const buttons: Record<string, { onPress: () => void }> = {};

jest.mock('react-native-reanimated', () => {
    const { View } = jest.requireActual('react-native');
    return {
        __esModule: true,
        default: { View },
        FadeIn: undefined,
        FadeInDown: undefined,
        FadeOut: undefined,
        FadeOutDown: undefined,
        useReducedMotion: () => true,
    };
});
jest.mock('@/ui', () => {
    const { Text: RNText } = jest.requireActual('react-native');
    const Pass = ({ children }: { children?: unknown }) => children ?? null;
    return {
        Button: (p: { label: string; onPress: () => void }) => {
            buttons[p.label] = p;
            return null;
        },
        Disclosure: ({ children }: { children?: unknown }) => children ?? null,
        HScroll: Pass,
        Icon: () => null,
        Notice: ({ message }: { message: string }) => <RNText>{message}</RNText>,
        Pressable: Pass,
        Row: Pass,
        ScrollBox: Pass,
        Stack: Pass,
        Surface: Pass,
        Text: RNText,
    };
});
jest.mock('@/ui/icons', () => ({ ShieldQuestion: () => null }));

function prompt(fields: Partial<GaiaNotification>): GaiaNotification {
    return {
        id: 'p1',
        type: 'permission_request',
        agentId: 'gaia',
        agentName: 'GAIA',
        title: '',
        message: '',
        timestamp: 0,
        read: false,
        dismissed: false,
        priority: 'high',
        tool: 'write_file',
        toolLabel: 'writing file',
        alwaysScope: 'write_file on notes.txt',
        timeoutSeconds: 30,
        ...fields,
    };
}

async function mount(p: GaiaNotification, onAnswer = jest.fn(), onExpired = jest.fn()) {
    for (const k of Object.keys(buttons)) delete buttons[k];
    const r = createRoot({ textComponentTypes: ['Text', 'RCTText'] });
    await act(async () => {
        r.render(<PermissionPrompt prompt={p} onAnswer={onAnswer} onExpired={onExpired} />);
    });
    return { onAnswer, onExpired, shown: () => JSON.stringify(r.container.toJSON()) };
}

beforeEach(() => jest.useFakeTimers());
afterEach(() => jest.useRealTimers());

describe('PermissionPrompt', () => {
    it('answers once or deny, and offers Always only when the call may be remembered', async () => {
        const { onAnswer } = await mount(prompt({}));
        act(() => buttons['Allow once'].onPress());
        act(() => buttons.Deny.onPress());
        expect(onAnswer.mock.calls).toEqual([['once'], ['deny']]);
        expect(Object.keys(buttons).some((label) => label.startsWith('Always allow'))).toBe(true);
        act(() => buttons[Object.keys(buttons).find((label) => label.startsWith('Always allow'))!].onPress());
        expect(onAnswer).toHaveBeenLastCalledWith('always');

        await mount(prompt({ tool: 'allow_path_access', toolArgs: { path: '/x', kind: 'folder' } }));
        expect(Object.keys(buttons).some((label) => label.startsWith('Always allow'))).toBe(false);
        expect(buttons['Allow for this chat']).toBeDefined();
    });

    it('hides the buttons at the deadline and reports it once', async () => {
        const { onExpired, shown } = await mount(prompt({ timeoutSeconds: 3 }));
        expect(shown()).toContain('3s');
        await act(async () => {
            jest.advanceTimersByTime(5000);
        });
        expect(shown()).toContain('Timed out with no answer');
        expect(onExpired).toHaveBeenCalledTimes(1);
        await act(async () => {
            jest.advanceTimersByTime(5000);
        });
        expect(onExpired).toHaveBeenCalledTimes(1);
    });

    it('picks the preview by tool: a saved file, a changed file, a calendar event', async () => {
        const write = await mount(prompt({ toolArgs: { file_path: 'a.txt', content: 'hello' } }));
        expect(write.shown()).toContain('What will be saved');
        expect(write.shown()).toContain('hello');

        const edit = await mount(prompt({ toolArgs: { file_path: 'a.txt', old_content: 'one\n', new_content: 'two\n' } }));
        expect(edit.shown()).toContain('What will change');
        expect(edit.shown()).toContain('-one');
        expect(edit.shown()).toContain('+two');

        const event = await mount(
            prompt({
                tool: 'calendar/create_calendar_event',
                toolArgs: { title: 'Lunch', start: '2026-11-20', end: '2026-11-20', notify_attendees: [] },
            }),
        );
        expect(event.shown()).toContain('Lunch');
        expect(event.shown()).toContain('Nobody is notified');
        expect(event.shown()).not.toContain('What will be saved');
    });
});
