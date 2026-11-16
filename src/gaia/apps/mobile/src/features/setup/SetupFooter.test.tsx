// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react-native';
import { Text } from 'react-native';

import { ThemeProvider } from '@/ui';

import { SetupFooter, SetupFooterBar, SetupFooterMuted, SetupFooterProvider } from './SetupFooter';

function Setup({ covered }: { covered: boolean }) {
    return (
        <SetupFooterProvider>
            <SetupFooterMuted muted={covered}>
                <SetupFooter>
                    <Text>Plan action</Text>
                </SetupFooter>
            </SetupFooterMuted>
            {covered ? (
                <SetupFooter>
                    <Text>Screen action</Text>
                </SetupFooter>
            ) : null}
            <SetupFooterBar />
        </SetupFooterProvider>
    );
}

describe('SetupFooterMuted', () => {
    it("keeps a covered step's actions out of the bar, and brings them back when it is uncovered", async () => {
        const { rerender } = await render(<Setup covered />, { wrapper: ThemeProvider });
        expect(screen.getByText('Screen action')).toBeTruthy();
        expect(screen.queryByText('Plan action')).toBeNull();

        await rerender(<Setup covered={false} />);
        expect(screen.getByText('Plan action')).toBeTruthy();
        expect(screen.queryByText('Screen action')).toBeNull();
    });
});
