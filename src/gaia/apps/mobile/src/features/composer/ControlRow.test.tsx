// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, type ReactNode } from 'react';
import { createRoot } from 'test-renderer';

import { translator } from '@webui/i18n/translator';

import { Composer } from './Composer';
import { PrivateChip } from './PrivateChip';

let mockLocale: 'ar' | 'en' = 'ar';
jest.mock('@/i18n/useT', () => ({ useT: () => jest.requireActual('@webui/i18n/translator').translator(mockLocale) }));
jest.mock('react-native', () => ({ View: 'View', Platform: { OS: 'ios' } }));
jest.mock('@/ui/icons', () => ({ ArrowUp: 'ArrowUp', Square: 'Square', ChevronDown: 'ChevronDown', Eye: 'Eye', EyeOff: 'EyeOff' }));
jest.mock('./AttachButton', () => ({ AttachButton: 'AttachButton' }));
jest.mock('./AttachmentChips', () => ({ AttachmentChips: 'AttachmentChips' }));
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
        Icon: host('Icon'),
        Pressable: host('Pressable'),
        Text: host('Text'),
    };
});

type Root = ReturnType<typeof createRoot>;
const find = (r: Root, testID: string) => r.container.queryAll((i) => i.props.testID === testID)[0];
const style = (n: { props: { style?: unknown } }) => n.props.style as Record<string, unknown>;

describe('composer control row in Arabic', () => {
    it('shows the chip label in Arabic only and keeps the bilingual text for screen readers', async () => {
        const r = createRoot();
        await act(async () => r.render(<PrivateChip on={false} onChange={() => undefined} />));
        const label = r.container.queryAll((i) => i.type === 'Text')[0];
        expect(label.props.children).toBe(translator('ar').bare('composer.memoryOn'));
        expect(String(label.props.children)).not.toMatch(/[A-Za-z]/);
        expect(String(find(r, 'private-chip').props.accessibilityLabel)).toMatch(/[A-Za-z]/);
    });

    // react-native-web renders View as a DOM node the style assertions can't read.
    (typeof document === 'undefined' ? it : it.skip)('lets the chips shrink while Attach and Send keep their size', async () => {
        const r = createRoot();
        await act(async () =>
            r.render(<Composer onSend={() => undefined} leftControls={<PrivateChip on={false} onChange={() => undefined} />} />),
        );
        expect(style(find(r, 'composer-controls'))).toMatchObject({ flexShrink: 1, minWidth: 0 });
        expect(style(find(r, 'composer-actions'))).toMatchObject({ flexShrink: 0 });
        expect(JSON.stringify(style(find(r, 'composer-controls')))).not.toMatch(/left|right/i);
    });

    // Same as above: the web project renders View as a DOM node.
    // Each chip keeps an off-screen full-width copy (one Text) beside the shown one.
    (typeof document === 'undefined' ? it.each : it.skip.each)(['en', 'ar'] as const)('shows the chip as its icon alone when its measured label does not fit, keeping the label for screen readers (%s)', async (locale) => {
        mockLocale = locale;
        const r = createRoot();
        await act(async () =>
            r.render(<Composer onSend={() => undefined} leftControls={<PrivateChip on={false} onChange={() => undefined} />} />),
        );
        const labels = () => r.container.queryAll((i) => i.type === 'Text');
        const layout = (width: number) => find(r, 'composer-controls').props.onLayout({ nativeEvent: { layout: { width } } });
        const measure = r.container.queryAll((i) => typeof i.props.onLayout === 'function' && i.props.testID !== 'composer-controls')[0];
        await act(async () => measure.props.onLayout({ nativeEvent: { layout: { width: 120 } } }));
        await act(async () => layout(340));
        expect(labels()).toHaveLength(2);
        await act(async () => layout(60));
        expect(labels()).toHaveLength(1);
        expect(String(find(r, 'private-chip').props.accessibilityLabel).length).toBeGreaterThan(3);
        mockLocale = 'ar';
    });
});
