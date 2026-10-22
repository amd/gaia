// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Settings › Model: adding a local model from a Hugging Face link, and
 * removing one the user added.
 */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ModelSettings } from '../ModelSettings';
import { useModelStore } from '../../../stores/modelStore';
import { useChatStore } from '../../../stores/chatStore';
import * as api from '../../../services/api';
import type { ActiveModel, CustomModelReport, ProviderInfo, ProviderModel } from '../../../types';

vi.mock('../../../services/api');

const mockedApi = vi.mocked(api);

const GEMMA = 'Gemma-4-E4B-it-GGUF';
const ADDED = 'user.Qwen3-4B-Instruct-2507-Q4_K_M-GGUF';
const URL = 'https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/tree/main';
const ACTIVE: ActiveModel = {
    provider: 'local', model: GEMMA, display_name: 'Gemma 4 E4B', label: 'Gemma', remote: false,
    is_default: true, downloaded: true,
};
const LOCAL: ProviderInfo = { id: 'local', name: 'Local', remote: false, privacy_notice: 'Runs on this PC.' };

function local(id: string, over: Partial<ProviderModel>): ProviderModel {
    return {
        id, context_length: null, downloaded: true, labels: [], rank: null, note: null, evidence: null,
        state: 'downloaded', available: true, unavailable_reason: null, fits: true, custom: false, ...over,
    };
}

const PASSED: CustomModelReport = {
    ok: true,
    url: URL,
    checks: [
        { id: 'link', label: 'Hugging Face link', ok: true, detail: 'unsloth/Qwen3-4B-Instruct-2507-GGUF' },
        { id: 'architecture', label: 'Supported architecture', ok: true, detail: 'qwen3 runs on llama.cpp b10825' },
        { id: 'fit', label: 'Fits this PC', ok: true, detail: 'Q4_K_M, 2.5 GB' },
    ],
    error: null,
    warnings: [],
    variants: [
        { quant: 'Q4_K_M', file: 'Qwen3-4B-Instruct-2507-Q4_K_M.gguf', size_gb: 2.5, fits: true, reason: null },
        { quant: 'Q8_0', file: 'Qwen3-4B-Instruct-2507-Q8_0.gguf', size_gb: 4.3, fits: true, reason: null },
    ],
    model_id: ADDED,
    display_name: 'Qwen3-4B-Instruct-2507 Q4_K_M',
    quant: 'Q4_K_M',
    file: 'Qwen3-4B-Instruct-2507-Q4_K_M.gguf',
    size_gb: 2.5,
};

const UNSUPPORTED: CustomModelReport = {
    ok: false,
    url: 'https://huggingface.co/unsloth/flint-GGUF',
    checks: [
        { id: 'link', label: 'Hugging Face link', ok: true, detail: 'unsloth/flint-GGUF' },
        {
            id: 'architecture', label: 'Supported architecture', ok: false,
            detail: "This model's architecture, 'flint', is not supported by llama.cpp b10825",
        },
    ],
    error: "This model's architecture, 'flint', is not supported by llama.cpp b10825",
    warnings: [],
    variants: [],
};

let models: ProviderModel[];

beforeEach(() => {
    vi.clearAllMocks();
    models = [local(GEMMA, { display_name: 'Gemma 4 E4B' })];
    useModelStore.setState({ active: ACTIVE, error: null, restoreNotice: null, names: {} });
    useChatStore.setState({ systemStatus: null });
    mockedApi.listProviders.mockResolvedValue({ providers: [LOCAL], active: ACTIVE, lemonade_error: null });
    mockedApi.listProviderModels.mockImplementation(async () => ({
        provider: 'local', models, memory_gb: 112, memory_source: 'AMD iGPU', capacity_error: null,
    }));
    mockedApi.getContextChoice.mockRejectedValue(new Error('not needed'));
    mockedApi.getActiveModel.mockResolvedValue(ACTIVE);
});

async function paste(url: string) {
    render(<ModelSettings />);
    await screen.findByText('Gemma 4 E4B');
    fireEvent.change(screen.getByLabelText('Add a model from Hugging Face'), { target: { value: url } });
    fireEvent.click(screen.getByRole('button', { name: 'Check' }));
}

describe('ModelSettings custom models', () => {
    it('checks a link, shows what passed, and adds the model', async () => {
        mockedApi.checkCustomModel.mockResolvedValue(PASSED);
        mockedApi.addCustomModel.mockImplementation(async () => {
            models = [...models, local(ADDED, {
                display_name: 'Qwen3-4B-Instruct-2507 Q4_K_M', downloaded: false, state: 'not_downloaded',
                custom: true, source_url: URL, size_gb: 2.5,
            })];
            return { report: PASSED };
        });
        await paste(URL);

        expect(await screen.findByText(/qwen3 runs on llama.cpp b10825/)).toBeInTheDocument();
        expect(mockedApi.checkCustomModel).toHaveBeenCalledWith(URL, '');
        fireEvent.click(screen.getByRole('button', { name: /Add Qwen3-4B-Instruct-2507 Q4_K_M/ }));

        await waitFor(() => expect(mockedApi.addCustomModel).toHaveBeenCalledWith(URL, 'Qwen3-4B-Instruct-2507-Q4_K_M.gguf'));
        const row = (await screen.findByText('Added by you')).closest('li') as HTMLElement;
        expect(within(row).getByText(`From ${URL}`)).toBeInTheDocument();
        expect(within(row).getByRole('button', { name: /Download/ })).toBeInTheDocument();
        expect(screen.getByRole('status')).toHaveTextContent('Added Qwen3-4B-Instruct-2507 Q4_K_M');
    });

    it('re-checks with the quantization picked', async () => {
        mockedApi.checkCustomModel.mockResolvedValue(PASSED);
        await paste(URL);
        fireEvent.change(await screen.findByLabelText('Quantization'), { target: { value: 'Qwen3-4B-Instruct-2507-Q8_0.gguf' } });
        await waitFor(() => expect(mockedApi.checkCustomModel).toHaveBeenLastCalledWith(URL, 'Qwen3-4B-Instruct-2507-Q8_0.gguf'));
    });

    it('drops the answer to a link that was edited while it was checked', async () => {
        let answer: (r: CustomModelReport) => void = () => {};
        mockedApi.checkCustomModel.mockImplementation(() => new Promise((resolve) => { answer = resolve; }));
        await paste(URL);
        const input = screen.getByLabelText('Add a model from Hugging Face') as HTMLInputElement;
        expect(input.readOnly).toBe(true);
        // The edit lands anyway (a programmatic change), as a second paste would.
        fireEvent.change(input, { target: { value: 'https://huggingface.co/other/Model-GGUF' } });
        answer(PASSED);
        await waitFor(() => expect(screen.getByRole('button', { name: 'Check' })).not.toBeDisabled());
        expect(screen.queryByRole('button', { name: /^Add / })).not.toBeInTheDocument();
    });

    it('shows why a link is refused and offers no Add', async () => {
        mockedApi.checkCustomModel.mockResolvedValue(UNSUPPORTED);
        await paste('https://huggingface.co/unsloth/flint-GGUF');
        expect(await screen.findByText(/'flint', is not supported by llama.cpp b10825/)).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /^Add / })).not.toBeInTheDocument();
    });

    it('removes an added model only after confirming', async () => {
        models = [...models, local(ADDED, { display_name: 'Qwen3-4B Q4_K_M', custom: true, source_url: URL })];
        mockedApi.removeCustomModel.mockImplementation(async () => {
            models = models.filter((m) => m.id !== ADDED);
            return { removed: ADDED, display_name: 'Qwen3-4B Q4_K_M', cleared: [] };
        });
        render(<ModelSettings />);
        await screen.findByText('Qwen3-4B Q4_K_M');
        // Built-in models have no Remove.
        const gemma = screen.getByText('Gemma 4 E4B').closest('li') as HTMLElement;
        expect(within(gemma).queryByRole('button', { name: /Remove/ })).not.toBeInTheDocument();

        fireEvent.click(screen.getByRole('button', { name: 'Remove Qwen3-4B Q4_K_M' }));
        expect(mockedApi.removeCustomModel).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: 'Remove and delete download' }));

        await waitFor(() => expect(mockedApi.removeCustomModel).toHaveBeenCalledWith(ADDED));
        expect(await screen.findByText('Removed Qwen3-4B Q4_K_M and its download.')).toBeInTheDocument();
        await waitFor(() => expect(screen.queryByText('Qwen3-4B Q4_K_M')).not.toBeInTheDocument());
    });
});
