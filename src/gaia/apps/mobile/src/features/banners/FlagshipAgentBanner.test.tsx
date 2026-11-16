// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { FLAGSHIP_AGENT_ID } from '@webui/utils/newTask';

import type { AgentInstallStatus } from '@/api/client.types';
import { createFakeClient, type FakeClientData } from '@/api/fake';
import { GaiaClientProvider } from '@/data';
import { translator } from '@/i18n/useT';
import { ThemeProvider } from '@/ui';

import { FlagshipAgentBanner } from './FlagshipAgentBanner';

const t = translator('en');

function status(state: Partial<AgentInstallStatus>): AgentInstallStatus {
    return { id: FLAGSHIP_AGENT_ID, status: 'installing', phase: 'download', percent: 0, version: null, error: null, ...state };
}

async function mount(data: FakeClientData) {
    const client = createFakeClient({ agents: [], ...data });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrap = ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={queryClient}>
            <GaiaClientProvider client={client}>
                <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 800 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}>
                    <ThemeProvider>{children}</ThemeProvider>
                </SafeAreaProvider>
            </GaiaClientProvider>
        </QueryClientProvider>
    );
    await render(<FlagshipAgentBanner />, { wrapper: wrap });
    return client;
}

describe('FlagshipAgentBanner', () => {
    it('follows the install from the PC: percent, then the agent appears', async () => {
        const client = await mount({ installStatuses: [status({ percent: 40 }), status({ status: 'completed', percent: 100 })] });
        fireEvent.press(await screen.findByText(t('agentNotice.install')));
        expect(await screen.findByText(t('agentNotice.installing', { percent: 40 }))).toBeTruthy();
        expect(client.installs).toEqual([FLAGSHIP_AGENT_ID]);
    });

    it('shows the PC reason when the install fails, and offers Try again', async () => {
        await mount({ installStatuses: [status({ status: 'failed', error: 'disk full' })] });
        fireEvent.press(await screen.findByText(t('agentNotice.install')));
        expect(await screen.findByText(t('agentNotice.failed', { error: 'disk full' }))).toBeTruthy();
        expect(screen.getByText(t('agentNotice.retry'))).toBeTruthy();
    });

    it('says there is no build for this PC and offers no install', async () => {
        await mount({ setupChecks: [{ ready: false, no_agent_build: true, steps: [] }] });
        await waitFor(() => expect(screen.getByText(t('agentNotice.noBuild'))).toBeTruthy());
        expect(screen.queryByText(t('agentNotice.install'))).toBeNull();
    });

    it('renders nothing while the agent is installed', async () => {
        await mount({ agents: undefined });
        await waitFor(() => expect(screen.queryByTestId('flagship-notice')).toBeNull());
    });
});
