// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { createFakeClient } from '@/api/fake';
import { GaiaClientProvider } from '@/data';
import { ThemeProvider } from '@/ui';

import { useSessionPanelStore } from './panelStore';
import { SkillsSection } from './SkillsSection';

describe('a collapsed panel section', () => {
    it('reads nothing until it is opened, then reads once', async () => {
        useSessionPanelStore.getState().setSection('skills', false);
        const client = createFakeClient({
            capabilities: {
                s1: {
                    session_id: 's1',
                    agent_type: 'gaia',
                    tools: [],
                    skills: [{ name: 'coding', how: 'model' }],
                },
            },
        });
        const queryClient = new QueryClient({
            defaultOptions: { queries: { retry: false } },
        });
        const wrap = ({ children }: { children: ReactNode }) => (
            <QueryClientProvider client={queryClient}>
                <GaiaClientProvider client={client}>
                    <SafeAreaProvider
                        initialMetrics={{
                            frame: { x: 0, y: 0, width: 390, height: 800 },
                            insets: { top: 0, left: 0, right: 0, bottom: 0 },
                        }}
                    >
                        <ThemeProvider>{children}</ThemeProvider>
                    </SafeAreaProvider>
                </GaiaClientProvider>
            </QueryClientProvider>
        );
        await render(<SkillsSection sessionId="s1" live={false} />, {
            wrapper: wrap,
        });
        expect(client.capabilityReads).toEqual([]);

        await fireEvent.press(screen.getByLabelText('Skills'));
        expect(await screen.findByText('Loaded by the model')).toBeTruthy();
        expect(client.capabilityReads).toEqual(['s1']);
    });
});
