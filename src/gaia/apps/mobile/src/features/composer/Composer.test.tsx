// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, type ReactNode } from 'react';
import { createRoot } from 'test-renderer';

import { MAX_MESSAGE_CHARS } from '@webui/utils/messageLimits';

import { Composer } from './Composer';


jest.mock('@/i18n/useT', () => ({ useT: () => Object.assign((key: string) => key, { bare: (key: string) => key }) }));
jest.mock('@/ui/icons', () => ({ ArrowUp: 'ArrowUp', Square: 'Square' }));
jest.mock('@/ui', () => {
    const { createElement: mockElement } = jest.requireActual('react');
    const host = (type: string) => (props: Record<string, unknown>) => mockElement(type, props, props.children as ReactNode);
    return {
        Button: host('Button'),
        IconButton: host('IconButton'),
        Notice: host('Notice'),
        TextField: host('TextField'),
        Row: host('Row'),
        Stack: host('Stack'),
        Surface: host('Surface'),
    };
});

type Root = ReturnType<typeof createRoot>;
const find = (r: Root, testID: string) => r.container.queryAll((i) => i.props.testID === testID)[0];

async function mount(props: Partial<Parameters<typeof Composer>[0]> = {}) {
    const onSend = jest.fn();
    const r = createRoot();
    await act(async () => r.render(<Composer onSend={onSend} {...props} />));
    return { r, onSend };
}

async function type(r: Root, text: string) {
    await act(async () => find(r, 'composer-input').props.onChangeText(text));
}

describe('Composer', () => {
    it('sends the trimmed text once, clears the box, and refuses an empty box', async () => {
        const { r, onSend } = await mount();
        expect(find(r, 'composer-send').props.disabled).toBe(true);
        await type(r, '  hello  ');
        expect(find(r, 'composer-send').props.disabled).toBe(false);
        await act(async () => find(r, 'composer-send').props.onPress());
        expect(onSend).toHaveBeenCalledWith('hello');
        expect(find(r, 'composer-input').props.value).toBe('');
    });

    it('shows an alert and blocks Send when the text is over the limit', async () => {
        const { r, onSend } = await mount();
        await type(r, 'x'.repeat(MAX_MESSAGE_CHARS + 1));
        expect(find(r, 'composer-too-long')).toBeDefined();
        expect(find(r, 'composer-send').props.disabled).toBe(true);
        await act(async () => find(r, 'composer-send').props.onPress());
        expect(onSend).not.toHaveBeenCalled();
    });

    it('offers Stop while a reply runs, then reads Stopping until it ends', async () => {
        const onStop = jest.fn();
        const { r } = await mount({ running: true, onStop });
        expect(find(r, 'composer-stop').props.label).toBe('composer.stop');
        await act(async () => find(r, 'composer-stop').props.onPress());
        expect(onStop).toHaveBeenCalledTimes(1);
        expect(find(r, 'composer-stop').props.label).toBe('composer.stopping');
        await act(async () => r.render(<Composer onSend={jest.fn()} running={false} />));
        expect(find(r, 'composer-stop')).toBeUndefined();
    });

    it('hides the send arrow while a reply runs until the draft has text', async () => {
        const { r } = await mount({ running: true });
        expect(find(r, 'composer-send')).toBeUndefined();
        await type(r, 'next');
        expect(find(r, 'composer-send').props.label).toBe('composer.queue');
    });

    it('blocks Send with a reason or while the chat is starting, yet keeps the text editable', async () => {
        const { r } = await mount({ starting: true });
        await type(r, 'hi');
        expect(find(r, 'composer-input').props.disabled).toBe(false);
        expect(find(r, 'composer-send').props.disabled).toBe(true);
        await act(async () => r.render(<Composer onSend={jest.fn()} disabledReason="GAIA is starting…" />));
        expect(find(r, 'composer-input').props.disabled).toBe(true);
        expect(find(r, 'composer-send').props.disabled).toBe(true);
    });
});
