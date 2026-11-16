// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { Platform } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import type { LinkPreview } from '@/api/client.types';
import { createFakeClient } from '@/api/fake';
import { GaiaClientProvider } from '@/data';
import { ThemeProvider } from '@/ui';
import { SheetHost } from '@/ui/overlays/SheetHost';

import { AddSkillsSheet } from './AddSkillsSheet';

const describeNative = Platform.OS === 'web' ? describe.skip : describe;

const PREVIEW: LinkPreview = {
    link: 'https://github.com/acme/skills',
    source: 'acme/skills',
    pin: 'abc',
    warning: 'Only add skills from people you trust.',
    skills: [
        { name: 'docs-lookup', description: 'Look up docs.', path: 'docs-lookup', has_code: true, permissions: [], permissions_unreadable: false, verdict: 'REVIEW', findings: [], installed: false, update: false },
    ] as unknown as LinkPreview['skills'],
};

describeNative('AddSkillsSheet', () => {
    it('reads a link and sends the person to the PC to add it, with no add control that would be refused', async () => {
        const client = createFakeClient({ linkPreview: () => PREVIEW });
        const wrap = ({ children }: { children: ReactNode }) => (
            <GaiaClientProvider client={client}>
                <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 800 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}>
                    <ThemeProvider>
                        <SheetHost>{children}</SheetHost>
                    </ThemeProvider>
                </SafeAreaProvider>
            </GaiaClientProvider>
        );
        await render(<AddSkillsSheet open onClose={() => {}} />, { wrapper: wrap });

        fireEvent.changeText(await screen.findByLabelText('Link to a skill or skills library'), PREVIEW.link);
        // The sheet shows what its owner last rendered, so press once the typed link has reached it.
        await waitFor(() => expect(screen.getByLabelText('Link to a skill or skills library').props.value).toBe(PREVIEW.link));
        fireEvent.press(screen.getByText('Read'));

        await screen.findByText('docs-lookup');
        expect(screen.getByText('Add these on your PC')).toBeTruthy();
        expect(screen.getByText('Share the link')).toBeTruthy();
        expect(screen.queryByText(/^Add \d/)).toBeNull();
    });
});
