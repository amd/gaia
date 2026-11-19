// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ModelSettings } from '../ModelSettings';
import { useModelStore } from '../../../stores/modelStore';
import { useChatStore } from '../../../stores/chatStore';
import * as api from '../../../services/api';
import { mockSetupApis } from '../../../test/setupApiMocks';
import type { SystemStatus } from '../../../types';

vi.mock('../../../services/api');

const mockedApi = vi.mocked(api);
const never = () => new Promise<never>(() => {});

beforeEach(() => {
    vi.clearAllMocks();
    mockSetupApis(mockedApi);
    mockedApi.listProviders.mockImplementation(never);
    mockedApi.getActiveModel.mockImplementation(never);
    useModelStore.setState({ active: null, error: null, restoreNotice: null });
    useChatStore.setState({ systemStatus: null, settingsProvider: null });
});

describe('Settings > Model while the model server has not answered yet', () => {
    it('says it is loading, not that no model is chosen', () => {
        render(<ModelSettings />);

        expect(screen.queryByText('No model is chosen yet. Pick one below.')).not.toBeInTheDocument();
        expect(screen.getByRole('status')).toHaveTextContent('Loading…');
    });

    it('says the model server is not answering when the status poll found it hung', () => {
        useChatStore.setState({
            systemStatus: { lemonade_running: false, lemonade_probe: 'timed_out', lemonade_error: 'Lemonade did not answer in time' } as SystemStatus,
        });
        render(<ModelSettings />);

        expect(screen.getByRole('status')).toHaveTextContent('Waiting for the model server to answer.');
    });
});
