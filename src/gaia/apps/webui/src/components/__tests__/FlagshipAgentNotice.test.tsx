// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { FlagshipAgentNotice } from '../FlagshipAgentNotice';

vi.mock('../../services/flagshipInstall', () => ({ installFlagshipAgent: vi.fn() }));

it('offers the install when the agent is only missing', () => {
    render(<FlagshipAgentNotice onInstalled={() => undefined} />);

    expect(screen.getByRole('status')).toHaveTextContent("The GAIA agent isn't installed");
    expect(screen.getByRole('button', { name: 'Install GAIA agent' })).toBeInTheDocument();
});

it('says chat is unavailable, and offers no install, on a PC with no agent build', () => {
    render(<FlagshipAgentNotice onInstalled={() => undefined} noBuild />);

    expect(screen.getByRole('status')).toHaveTextContent(
        "No GAIA agent build for this PC yet, so chat isn't available here.",
    );
    expect(screen.queryByRole('button')).toBeNull();
});
