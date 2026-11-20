// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react-native';
import { Alert, Linking } from 'react-native';

import { CODE_COLLAPSE_LINES } from '@webui/utils/longContent';
import { translator } from '@/i18n/useT';
import { ThemeProvider } from '@/ui';

import { Markdown } from './Markdown';

jest.mock('expo-clipboard', () => ({ setStringAsync: jest.fn(() => Promise.resolve(true)) }));

const code = Array.from({ length: CODE_COLLAPSE_LINES + 5 }, (_, i) => `line ${i}`).join('\n');
const answer = [
    '# Plan',
    '```ts\n' + code + '\n```',
    '| Name | Count |\n| --- | --- |\n| apples | 3 |',
    'Click [bad](javascript:alert(1)) or [good](https://example.com).',
    'Logs: C:\\Users\\me\\.gaia\\app.log',
].join('\n\n');

describe('Markdown', () => {
    it('renders headings, a collapsed code block, a table, safe links and Windows paths as text', async () => {
        const t = translator('en');
        const open = jest.spyOn(Linking, 'openURL').mockResolvedValue(undefined);
        await render(
            <ThemeProvider>
                <Markdown text={answer} />
            </ThemeProvider>,
        );

        expect(screen.getByText('Plan')).toBeTruthy();
        expect(screen.getByText(t('code.expand', { size: `${CODE_COLLAPSE_LINES + 5} lines` }))).toBeTruthy();
        expect(screen.queryByText(/line 24/)).toBeNull();
        expect(screen.getByText('apples')).toBeTruthy();

        // javascript: opens nothing and tells the person why; https: opens.
        const alert = jest.spyOn(Alert, 'alert').mockImplementation(() => undefined);
        jest.spyOn(console, 'error').mockImplementation(() => undefined);
        screen.getAllByText(/bad/).forEach((el) => el.props.onPress?.());
        expect(alert).toHaveBeenCalledWith(t('mobileApp.link.blockedTitle'), expect.stringContaining('javascript'));
        expect(open).not.toHaveBeenCalled();
        screen.getByText('good').props.onPress();
        expect(open).toHaveBeenCalledWith('https://example.com');
        expect(open).toHaveBeenCalledTimes(1);

        const paths = screen.getAllByText(/C:\\Users\\me\\\.gaia\\app\.log/);
        expect(paths.length).toBeGreaterThan(0);
        expect(paths.every((el) => el.props.onPress === undefined)).toBe(true);
    });

    it('formatting variant renders a link, an image, a heading and HTML as plain text', async () => {
        const text = '# Urgent\n\nOpen [the form](https://example.com/login) ![pic](https://example.com/p.png) <b>raw</b>';
        await render(
            <ThemeProvider>
                <Markdown text={text} variant="formatting" />
            </ThemeProvider>,
        );
        const form = screen.getByText(/the form/);
        expect(form.props.onPress).toBeUndefined();
        expect(screen.queryByRole('link')).toBeNull();
        expect(screen.queryByText(/pic|p\.png/)).toBeNull();
        expect(screen.getByText('Urgent').props.accessibilityRole).not.toBe('header');
        expect(screen.getByText(/<b>raw<\/b>/)).toBeTruthy();
    });
});
