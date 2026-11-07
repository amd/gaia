// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A render error stays in the screen it happened in instead of blanking the window (#6956). */

import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ErrorBoundary } from '../ErrorBoundary';
import { SettingsDialog } from '../settings/SettingsDialog';
import { useChatStore } from '../../stores/chatStore';

vi.mock('../../services/api');
vi.mock('../settings/GeneralSettings', () => ({
    GeneralSettings: () => { throw new Error('general pane broke'); },
}));
vi.mock('../settings/ModelSettings', () => ({ ModelSettings: () => <p>model pane</p> }));

let fail = true;
function Flaky() {
    if (fail) throw new Error('flaky');
    return <p>recovered</p>;
}

let consoleError: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
    fail = true;
    consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    useChatStore.setState({ settingsSection: 'general', locale: 'en' });
});
afterEach(() => consoleError.mockRestore());

describe('ErrorBoundary', () => {
    it('shows a fallback, keeps its siblings, and logs the area', () => {
        render(
            <>
                <p>sidebar</p>
                <ErrorBoundary area="main"><Flaky /></ErrorBoundary>
            </>,
        );
        expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong');
        expect(screen.getByText('sidebar')).toBeInTheDocument();
        expect(consoleError).toHaveBeenCalledWith(
            '[ErrorBoundary] main failed to render:', expect.any(Error), expect.any(String),
        );
    });

    it('renders the children again on Try again', () => {
        render(<ErrorBoundary area="main"><Flaky /></ErrorBoundary>);
        fail = false;
        fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
        expect(screen.getByText('recovered')).toBeInTheDocument();
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    it('says the whole app needs a reload when it replaces the window', () => {
        render(<ErrorBoundary area="app" variant="window"><Flaky /></ErrorBoundary>);
        const alert = screen.getByRole('alert');
        expect(alert).toHaveClass('error-boundary-window');
        expect(alert).toHaveTextContent('GAIA hit an error. Reload to continue.');
        expect(alert).not.toHaveTextContent('The rest of the app still works');
    });

    it('says the whole app needs a reload in Arabic too', () => {
        useChatStore.setState({ locale: 'ar' });
        render(<ErrorBoundary area="app" variant="window"><Flaky /></ErrorBoundary>);
        expect(screen.getByRole('alert')).toHaveTextContent('واجه GAIA خطأً. أعد التحميل للمتابعة.');
    });

    it('stays compact in the sidebar and keeps the rest-of-app wording', () => {
        render(<ErrorBoundary area="sidebar" variant="compact"><Flaky /></ErrorBoundary>);
        const alert = screen.getByRole('alert');
        expect(alert).toHaveClass('error-boundary-compact');
        expect(alert).toHaveTextContent('The rest of the app still works');
    });

    it('shows the fallback in Arabic', () => {
        useChatStore.setState({ locale: 'ar' });
        render(<ErrorBoundary area="main"><Flaky /></ErrorBoundary>);
        expect(screen.getByRole('alert')).toHaveTextContent('حدث خطأ ما');
    });
});

describe('SettingsDialog with a broken section', () => {
    it('keeps the section list working and clears when another section is picked', () => {
        render(
            <>
                <p>open chat</p>
                <SettingsDialog onUseSkill={vi.fn()} />
            </>,
        );
        expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong');
        expect(screen.getByText('open chat')).toBeInTheDocument();
        expect(consoleError).toHaveBeenCalledWith(
            '[ErrorBoundary] settings.general failed to render:', expect.any(Error), expect.any(String),
        );

        fireEvent.click(screen.getByRole('button', { name: 'Model' }));
        expect(screen.getByText('model pane')).toBeInTheDocument();
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });
});
