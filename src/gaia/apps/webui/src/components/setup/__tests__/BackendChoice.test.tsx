// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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
    return {
        ...actual,
        getModelServer: vi.fn(),
        setModelServerBackend: vi.fn(),
        fixSetupNpuMemlock: vi.fn(),
        recheckSetupNpu: vi.fn(),
    };
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

    it('offers no NPU on a PC without one', () => {
        render(<Harness setup={plan([QWEN, GEMMA, EMBED])} />);
        expect(screen.getAllByRole('radio').map((r) => r.textContent)).toEqual(['Graphics (GPU)']);
        expect(screen.getByText(/Chat runs on this PC's graphics/)).toBeInTheDocument();
    });

    // #6632: a stock desktop leaves memlock at 8 MB; the NPU model must not be picked.
    const notReady = (memlockFix: 'apply' | 'restart') => plan([QWEN, GEMMA, EMBED, NPU], {
        npu: {
            present: true,
            ready: false,
            problems: [
                { code: 'memlock', title: '', fix: '', params: { limit: '8 MB', fix: memlockFix } },
                { code: 'access', title: '', fix: '', params: { device: '/dev/accel/accel0', group: 'render' } },
            ],
        },
    });

    function WithRecheck({ setup, onRecheck }: { setup: SetupPlan; onRecheck: () => void }) {
        const [selection, setSelection] = useState(() => initialSelection(setup));
        return (
            <>
                <BackendChoice plan={setup} selection={selection} onSelection={setSelection} onRecheck={onRecheck} />
                <output data-testid="chat">{selection.defaultChat}</output>
            </>
        );
    }

    it('shows an NPU that is not set up yet, with what to fix, and does not offer it', async () => {
        const onRecheck = vi.fn();
        vi.mocked(api.recheckSetupNpu).mockResolvedValue(notReady('apply'));
        render(<WithRecheck setup={notReady('apply')} onRecheck={onRecheck} />);
        const npu = screen.getByRole('radio', { name: 'Ryzen AI NPU' });
        expect(npu).toBeDisabled();
        fireEvent.click(npu);
        expect(screen.getByTestId('chat')).toHaveTextContent(QWEN.id);
        const note = screen.getByRole('note');
        expect(note).toHaveTextContent('The locked-memory limit is 8 MB, and the NPU needs it unlimited.');
        expect(note).toHaveTextContent('GAIA can raise it for your account once');
        expect(note).toHaveTextContent('Add your account to the render group, then sign out and back in.');
        expect(note).toHaveTextContent('Until then chat runs on the graphics (GPU).');
        fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
        await waitFor(() => expect(onRecheck).toHaveBeenCalledOnce());
        expect(api.recheckSetupNpu).toHaveBeenCalledOnce();
    });

    it('raises the locked-memory limit behind the administrator prompt, then checks again', async () => {
        const onRecheck = vi.fn();
        vi.mocked(api.fixSetupNpuMemlock).mockResolvedValue(notReady('restart'));
        render(<WithRecheck setup={notReady('apply')} onRecheck={onRecheck} />);
        fireEvent.click(screen.getByRole('button', { name: 'Raise the limit' }));
        await waitFor(() => expect(onRecheck).toHaveBeenCalledOnce());
        expect(api.fixSetupNpuMemlock).toHaveBeenCalledOnce();
        expect(screen.queryByRole('alert')).toBeNull();
    });

    it('says so when the administrator prompt is closed', async () => {
        const onRecheck = vi.fn();
        vi.mocked(api.fixSetupNpuMemlock).mockRejectedValue(
            new api.ApiError('closed', 409, 'closed', null, [], { code: 'cancelled', message: 'closed' }),
        );
        render(<WithRecheck setup={notReady('apply')} onRecheck={onRecheck} />);
        fireEvent.click(screen.getByRole('button', { name: 'Raise the limit' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('The administrator prompt was closed, so nothing changed.');
        expect(onRecheck).not.toHaveBeenCalled();
    });

    it('asks only for a restart once the limit is raised', () => {
        render(<WithRecheck setup={notReady('restart')} onRecheck={vi.fn()} />);
        const note = screen.getByRole('note');
        expect(note).toHaveTextContent('The locked-memory limit is still 8 MB.');
        expect(note).toHaveTextContent('Restart the PC to finish.');
        expect(screen.queryByRole('button', { name: 'Raise the limit' })).toBeNull();
    });

    it('offers a set-up NPU as before', () => {
        render(<Harness setup={plan([QWEN, GEMMA, EMBED, NPU], { npu: { present: true, ready: true, problems: [] } })} />);
        expect(screen.getByRole('radio', { name: 'Ryzen AI NPU' })).toBeEnabled();
        expect(screen.queryByRole('note')).toBeNull();
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
