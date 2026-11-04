// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../../services/api';
import type { SetupPlan } from '../../../types';
import type { ModelServerInfo } from '../../../types/api.gen';
import { BackendChoice, applyTestedBackend } from '../BackendChoice';
import { initialSelection } from '../selection';
import { EMBED, GEMMA, QWEN, entry, plan } from './fixtures';

vi.mock('../../../services/api', async (importOriginal) => {
    const actual = await importOriginal<typeof import('../../../services/api')>();
    return { ...actual, getModelServer: vi.fn(), setModelServerBackend: vi.fn() };
});

// GAIA's own NPU model, as setup lists it on a PC whose NPU holds it.
const NPU = entry({ id: 'gemma4-it-e4b-FLM', name: 'Gemma 4 E4B (NPU)', recipe: 'flm', size_gb: 9.1, full_size_gb: 9.1, fits: true });

const SERVER = (preferred: string | null, fallback = 'vulkan'): ModelServerInfo => ({
    reachable: true, available_backends: [{ name: 'vulkan', state: 'installed' }], default_backend: fallback, preferred_backend: preferred,
} as unknown as ModelServerInfo);

function Harness({ setup }: { setup: SetupPlan }) {
    const [selection, setSelection] = useState(() => initialSelection(setup));
    return (
        <>
            <BackendChoice plan={setup} selection={selection} onSelection={setSelection} />
            <output data-testid="chat">{selection.defaultChat}</output>
        </>
    );
}

beforeEach(() => vi.clearAllMocks());

describe('choosing where the chat model runs', () => {
    it('offers only the two tested backends, and NPU picks the NPU chat model', () => {
        render(<Harness setup={plan([QWEN, GEMMA, EMBED, NPU])} />);
        expect(screen.getAllByRole('radio').map((r) => r.textContent)).toEqual(['Graphics (GPU)', 'Ryzen AI NPU']);
        expect(screen.getByRole('radio', { name: 'Graphics (GPU)' })).toHaveAttribute('aria-checked', 'true');
        fireEvent.click(screen.getByRole('radio', { name: 'Ryzen AI NPU' }));
        expect(screen.getByTestId('chat')).toHaveTextContent(NPU.id);
        expect(screen.getByRole('radio', { name: 'Ryzen AI NPU' })).toHaveAttribute('aria-checked', 'true');
        // Back to the GPU: setup's own GPU pick. Choosing saves nothing; the start does.
        fireEvent.click(screen.getByRole('radio', { name: 'Graphics (GPU)' }));
        expect(screen.getByTestId('chat')).toHaveTextContent(QWEN.id);
        expect(api.getModelServer).not.toHaveBeenCalled();
        expect(api.setModelServerBackend).not.toHaveBeenCalled();
    });

    it("offers NPU for an NPU model the user already has, when GAIA's isn't listed", () => {
        const theirs = entry({ id: 'gemma4-it-e2b-FLM', name: 'Gemma 4 E2B (NPU)', recipe: 'flm', gaia_model: false, downloaded: true, size_gb: 0 });
        render(<Harness setup={plan([QWEN, GEMMA, EMBED, theirs])} />);
        fireEvent.click(screen.getByRole('radio', { name: 'Ryzen AI NPU' }));
        expect(screen.getByTestId('chat')).toHaveTextContent(theirs.id);
    });

    // #6632: Lemonade saw the NPU, but FastFlowLM can't run here, so NPU isn't offered and the hint says why.
    it("offers no NPU where its engine can't run, even for an NPU model already downloaded", () => {
        const why = { fits: false as const, fit_reason: 'needs an AMD Ryzen AI NPU and its driver', fit_code: 'npu' as const };
        const ours = { ...NPU, ...why };
        const theirs = entry({ id: 'gemma4-it-e2b-FLM', name: 'Gemma 4 E2B (NPU)', recipe: 'flm', gaia_model: false, downloaded: true, size_gb: 0, ...why });
        render(<Harness setup={plan([QWEN, GEMMA, EMBED, ours, theirs])} />);
        expect(screen.getAllByRole('radio').map((r) => r.textContent)).toEqual(['Graphics (GPU)']);
        expect(screen.getByText("Chat runs on this PC's graphics (GPU). This PC's Ryzen AI NPU can't run GAIA's models.")).toBeInTheDocument();
    });

    it('offers no NPU on a PC without one', () => {
        render(<Harness setup={plan([QWEN, GEMMA, EMBED])} />);
        expect(screen.getAllByRole('radio').map((r) => r.textContent)).toEqual(['Graphics (GPU)']);
        expect(screen.getByText(/Chat runs on this PC's graphics/)).toBeInTheDocument();
    });

    it('keeps llama.cpp on Vulkan at the start, only where this PC runs it', async () => {
        vi.mocked(api.getModelServer).mockResolvedValue(SERVER('rocm'));
        await applyTestedBackend();
        expect(api.setModelServerBackend).toHaveBeenCalledExactlyOnceWith('vulkan');

        vi.clearAllMocks();
        // Lemonade's own default is already Vulkan: nothing to save.
        vi.mocked(api.getModelServer).mockResolvedValue(SERVER(null));
        await applyTestedBackend();
        expect(api.setModelServerBackend).not.toHaveBeenCalled();

        // A Mac or CPU-only PC: no tested GPU backend to switch to, so nothing is asked.
        vi.mocked(api.getModelServer).mockResolvedValue({ reachable: true, available_backends: [], default_backend: 'metal', preferred_backend: null } as unknown as ModelServerInfo);
        await applyTestedBackend();
        // A server that isn't answering: the download reports it, not this.
        vi.mocked(api.getModelServer).mockResolvedValue({ reachable: false, error: 'not answering' } as unknown as ModelServerInfo);
        await applyTestedBackend();
        expect(api.setModelServerBackend).not.toHaveBeenCalled();
    });
});
