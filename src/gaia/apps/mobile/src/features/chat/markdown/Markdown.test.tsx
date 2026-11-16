// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react-native';
import { Alert, Linking } from 'react-native';

import { CODE_COLLAPSE_LINES } from '@webui/utils/longContent';
import { translator } from '@/i18n/useT';
import { useConnectionStore } from '@/state/connection';
import { ThemeProvider } from '@/ui';

import { FileScopeContext } from '@/features/files/scopeContext';
import { usePreviewStore } from '@/features/files/previewStore';

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

    it('opens a cited library file in the in-app preview at its page, never in the browser, and shows library notices as notes', async () => {
        useConnectionStore.getState().setConnection({ baseUrl: 'http://pc.local:4200/', token: null });
        const open = jest.spyOn(Linking, 'openURL').mockResolvedValue(undefined);
        open.mockClear();
        await render(
            <ThemeProvider>
                <FileScopeContext.Provider value="s1">
                    <Markdown
                        text="See `quarterly-report.pdf` on page 12."
                        documents={[{ id: 'doc 1', filename: 'quarterly-report.pdf' }]}
                        notes={['Added to your library as a copy.']}
                    />
                </FileScopeContext.Provider>
            </ThemeProvider>,
        );
        screen.getByText(/quarterly-report\.pdf/).props.onPress();
        expect(usePreviewStore.getState().target).toEqual({ kind: 'document', docId: 'doc 1', name: 'quarterly-report.pdf', sessionId: 's1', page: 12 });
        expect(open).not.toHaveBeenCalled();
        expect(screen.getByRole('note')).toBeTruthy();
    });

    it('links a Windows path to the preview only when a chat is in scope', async () => {
        await render(
            <ThemeProvider>
                <FileScopeContext.Provider value="s1">
                    <Markdown text={'Saved to `C:\\out\\a.txt`.'} />
                </FileScopeContext.Provider>
            </ThemeProvider>,
        );
        screen.getByText(/a\.txt/).props.onPress();
        expect(usePreviewStore.getState().target).toEqual({ kind: 'path', path: 'C:\\out\\a.txt', sessionId: 's1' });
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
