// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../../services/api';
import type { ModelServerInfo } from '../../../types/api.gen';
import { BackendChoice } from '../BackendChoice';

vi.mock('../../../services/api', async (importOriginal) => {
    const actual = await importOriginal<typeof import('../../../services/api')>();
    return { ...actual, getModelServer: vi.fn(), setModelServerBackend: vi.fn() };
});

const INFO = (preferred: string | null): ModelServerInfo => ({
    reachable: true,
    available_backends: [{ name: 'vulkan', state: 'installed' }, { name: 'rocm', state: 'installable' }, { name: 'cpu', state: 'installed' }],
    default_backend: 'vulkan',
    preferred_backend: preferred,
} as unknown as ModelServerInfo);

beforeEach(() => vi.clearAllMocks());

describe('choosing the backend in Customize', () => {
    it('offers Auto and what this PC can run, and saves the same setting as Settings', async () => {
        vi.mocked(api.getModelServer).mockResolvedValue(INFO(null));
        vi.mocked(api.setModelServerBackend).mockResolvedValue(INFO('rocm'));
        render(<BackendChoice />);
        const auto = await screen.findByRole('radio', { name: 'Auto (Vulkan)' });
        expect(auto).toHaveAttribute('aria-checked', 'true');
        expect(screen.getAllByRole('radio').map((r) => r.textContent)).toEqual(['Auto (Vulkan)', 'Vulkan', 'ROCm', 'CPU']);
        expect(screen.getByText(/ROCm downloads the first time it runs/)).toBeInTheDocument();
        fireEvent.click(screen.getByRole('radio', { name: 'ROCm' }));
        await waitFor(() => expect(api.setModelServerBackend).toHaveBeenCalledWith('rocm'));
        expect(await screen.findByRole('radio', { name: 'ROCm' })).toHaveAttribute('aria-checked', 'true');
    });

    it('says why when the backends cannot be read', async () => {
        vi.mocked(api.getModelServer).mockRejectedValue(new Error('server not answering'));
        render(<BackendChoice />);
        expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't read the backends: server not answering");
    });
});
