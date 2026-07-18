// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { OnboardingWizard } from '../OnboardingWizard';
import type { PreflightReport } from '../../../types';

// Mock the API surface the wizard + steps touch.
vi.mock('../../../services/api', () => ({
    getOnboardingPreflight: vi.fn(),
    getSystemStatus: vi.fn(),
    downloadModel: vi.fn(),
    listConnectors: vi.fn(),
    getConnector: vi.fn(),
    authorizeConnector: vi.fn(),
    completeOnboarding: vi.fn(),
}));

import * as api from '../../../services/api';

const okReport: PreflightReport = {
    os: 'win-x64',
    detected_platform: 'win-x64',
    ram_gb: 32,
    disk_free_gb: 500,
    npu_detected: true,
    gpu_name: 'Radeon 780M',
    gpu_vram_gb: 16,
    lemonade_running: true,
    tier: 'full',
    recommended_profile: 'chat',
    recommended_model: 'Gemma-4-E4B-it-GGUF',
    required_disk_gb: 6,
    required_memory_gb: 8,
    compatible: true,
    blockers: [],
    warnings: [],
};

const blockedReport: PreflightReport = {
    ...okReport,
    tier: 'insufficient',
    disk_free_gb: 0.5,
    compatible: false,
    blockers: ['Not enough disk space: install needs ~6.0 GB but only 0.5 GB is free.'],
};

beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getSystemStatus).mockResolvedValue({ download_progress: null } as never);
});

describe('OnboardingWizard', () => {
    it('renders the welcome step first', () => {
        render(<OnboardingWizard onComplete={vi.fn()} />);
        expect(screen.getByTestId('onboarding-welcome')).toBeInTheDocument();
    });

    it('skip on welcome writes the marker (skipped) and completes', async () => {
        vi.mocked(api.completeOnboarding).mockResolvedValue({ initialized: true, skipped: true, completed_at: null });
        const onComplete = vi.fn();
        render(<OnboardingWizard onComplete={onComplete} />);

        fireEvent.click(screen.getByText(/Skip — I know what I'm doing/));

        await waitFor(() => expect(api.completeOnboarding).toHaveBeenCalledWith(true));
        expect(onComplete).toHaveBeenCalled();
    });

    it('runs the pre-flight scan when advancing from welcome', async () => {
        vi.mocked(api.getOnboardingPreflight).mockResolvedValue(okReport);
        render(<OnboardingWizard onComplete={vi.fn()} />);

        fireEvent.click(screen.getByText(/Get started/));

        await screen.findByTestId('onboarding-preflight');
        expect(api.getOnboardingPreflight).toHaveBeenCalled();
        // Compatible report ⇒ Continue is enabled.
        await waitFor(() => expect(screen.getByTestId('onboarding-next')).not.toBeDisabled());
    });

    it('gates Continue when the machine fails the hardware check', async () => {
        vi.mocked(api.getOnboardingPreflight).mockResolvedValue(blockedReport);
        render(<OnboardingWizard onComplete={vi.fn()} />);

        fireEvent.click(screen.getByText(/Get started/));

        await screen.findByTestId('preflight-blockers');
        expect(screen.getByTestId('onboarding-next')).toBeDisabled();
    });

    it('advances into the download step once hardware passes', async () => {
        vi.mocked(api.getOnboardingPreflight).mockResolvedValue(okReport);
        render(<OnboardingWizard onComplete={vi.fn()} />);

        fireEvent.click(screen.getByText(/Get started/));
        await waitFor(() => expect(screen.getByTestId('onboarding-next')).not.toBeDisabled());
        fireEvent.click(screen.getByTestId('onboarding-next'));

        await screen.findByTestId('onboarding-download');
    });
});
