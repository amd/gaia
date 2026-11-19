// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ModelServerPanel } from '../ModelServerPanel';
import * as api from '../../../services/api';
import { useChatStore } from '../../../stores/chatStore';
import type { ModelServerInfo } from '../../../types/api.gen';

vi.mock('../../../services/api');
const mockedApi = vi.mocked(api);

function info(overrides: Partial<ModelServerInfo> = {}): ModelServerInfo {
    return {
        reachable: true, error: null, base_url: 'http://localhost:51234/api/v1', port: 51234,
        owner: 'gaia', version: '2026.41.1',
        chat_model: {
            name: 'Qwen3.6-35B-A3B-GGUF', recipe: 'llamacpp', device: 'gpu',
            backend: 'vulkan', backend_source: 'lemonade_default', ctx_size: 65536,
        },
        embedding_model: {
            name: 'embeddinggemma-300m-GGUF', recipe: 'llamacpp', device: 'gpu',
            backend: 'vulkan', backend_source: 'requested', ctx_size: null,
        },
        default_backend: 'vulkan',
        // The server lists only backends GAIA has tested.
        available_backends: [{ name: 'vulkan', state: 'installed' }],
        preferred_backend: null, system_info_error: null,
        loaded_models: [],
        ...overrides,
    };
}

beforeEach(() => {
    vi.clearAllMocks();
});

describe('ModelServerPanel', () => {
    it('shows other loaded models with their type, backend, and context', async () => {
        mockedApi.getModelServer.mockResolvedValue(info({
            loaded_models: [
                {
                    name: 'Gemma-3n-Vision-GGUF',
                    model_type: 'vision',
                    recipe: 'llamacpp',
                    device: 'gpu',
                    backend: 'vulkan',
                    backend_source: 'lemonade_default',
                    ctx_size: 4096,
                },
                {
                    name: 'bge-reranker-v2-m3-GGUF',
                    model_type: 'reranking',
                    recipe: 'llamacpp',
                    device: 'gpu',
                    backend: 'rocm',
                    backend_source: 'requested',
                    ctx_size: null,
                },
            ],
        }));
        render(<ModelServerPanel />);

        expect(await screen.findByText('Vision model')).toBeInTheDocument();
        expect(screen.getByText(/Gemma-3n-Vision-GGUF · Vulkan.*4K tokens/)).toBeInTheDocument();
        expect(screen.getByText('Reranker model')).toBeInTheDocument();
        expect(screen.getByText(/bge-reranker-v2-m3-GGUF · ROCm/)).toBeInTheDocument();
    });

    it('names the server, its address, and what the chat model runs on', async () => {
        mockedApi.getModelServer.mockResolvedValue(info());
        render(<ModelServerPanel />);

        expect(await screen.findByText("GAIA's own Lemonade v2026.41.1")).toBeInTheDocument();
        expect(screen.getByText('http://localhost:51234/api/v1')).toBeInTheDocument();
        expect(screen.getByText("Vulkan (Lemonade's default)")).toBeInTheDocument();
        expect(screen.getByText('64K tokens')).toBeInTheDocument();
        expect(screen.getByText(/embeddinggemma-300m-GGUF · Vulkan/)).toBeInTheDocument();
    });

    it('names a cloud chat model without its routing prefix, keeping the id in the tooltip', async () => {
        mockedApi.getModelServer.mockResolvedValue(info({
            chat_model: {
                name: 'fireworks.deepseek-v4p1-flash', recipe: 'cloud', device: null,
                backend: 'remote', backend_source: null, ctx_size: null,
            },
        }));
        render(<ModelServerPanel />);
        const name = await screen.findByText('deepseek-v4p1-flash');
        expect(name).toHaveAttribute('title', 'fireworks.deepseek-v4p1-flash');
        expect(screen.queryByText('fireworks.deepseek-v4p1-flash')).not.toBeInTheDocument();
    });

    it('says why the embedding model runs on the CPU with ROCm, and only then', async () => {
        const note = 'Embedding model runs on the CPU with ROCm to avoid a known GPU driver issue.';
        mockedApi.getModelServer.mockResolvedValue(info({ embedding_note: note }));
        const { unmount } = render(<ModelServerPanel />);
        expect(await screen.findByText(note)).toBeInTheDocument();
        unmount();

        mockedApi.getModelServer.mockResolvedValue(info());
        render(<ModelServerPanel />);
        await screen.findByText("GAIA's own Lemonade v2026.41.1");
        expect(screen.queryByText(/known GPU driver issue/)).not.toBeInTheDocument();
    });

    it('offers only the backends GAIA has tested, with no Auto', async () => {
        mockedApi.getModelServer.mockResolvedValue(info());
        render(<ModelServerPanel />);
        await screen.findByRole('radiogroup', { name: 'llama.cpp backend' });

        expect(screen.getAllByRole('radio').map((b) => b.textContent)).toEqual(['Vulkan']);
        expect(screen.getByRole('radio', { name: 'Vulkan' })).toHaveAttribute('aria-checked', 'true');
        expect(screen.getByText(/NPU models run on FastFlowLM/)).toBeInTheDocument();
    });

    it('keeps an untested backend saved earlier visible, so it can be switched to Vulkan', async () => {
        mockedApi.getModelServer.mockResolvedValue(info({ preferred_backend: 'rocm' }));
        mockedApi.setModelServerBackend.mockResolvedValue(info({ preferred_backend: 'vulkan' }));
        render(<ModelServerPanel />);

        expect(await screen.findByRole('radio', { name: 'ROCm' })).toHaveAttribute('aria-checked', 'true');
        fireEvent.click(screen.getByRole('radio', { name: 'Vulkan' }));

        await screen.findByText(/applies the next time the chat model loads/);
        expect(mockedApi.setModelServerBackend).toHaveBeenCalledWith('vulkan');
        expect(screen.getByRole('radio', { name: 'Vulkan' })).toHaveAttribute('aria-checked', 'true');
    });

    it('shows a refused choice as an error', async () => {
        mockedApi.getModelServer.mockResolvedValue(info({ preferred_backend: 'rocm' }));
        mockedApi.setModelServerBackend.mockRejectedValue(new Error('Lemonade is not answering'));
        render(<ModelServerPanel />);

        fireEvent.click(await screen.findByRole('radio', { name: 'Vulkan' }));

        expect(await screen.findByRole('alert')).toHaveTextContent('not answering');
        expect(screen.getByRole('radio', { name: 'ROCm' })).toHaveAttribute('aria-checked', 'true');
    });

    it('reads unknown, not a crash, when an older Lemonade omits the backend', async () => {
        mockedApi.getModelServer.mockResolvedValue(info({
            version: null,
            chat_model: { name: 'Old-Model', recipe: 'llamacpp', device: null, backend: null, backend_source: null, ctx_size: null },
            embedding_model: null, default_backend: null, available_backends: [],
        }));
        render(<ModelServerPanel />);

        expect(await screen.findByText("GAIA's own Lemonade")).toBeInTheDocument();
        expect(screen.getAllByText('unknown')).toHaveLength(2);
        expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument();
        expect(screen.getByText(/nothing to\s+choose/)).toBeInTheDocument();
    });

    it('shows a cloud model as remote, with no local context window', async () => {
        mockedApi.getModelServer.mockResolvedValue(info({
            owner: 'user',
            chat_model: { name: 'amd.DeepSeek-V4.1-Flash', recipe: 'cloud', device: null, backend: 'remote', backend_source: null, ctx_size: null },
        }));
        render(<ModelServerPanel />);

        expect(await screen.findByText('Remote (cloud)')).toBeInTheDocument();
        expect(screen.queryByText('Context window')).not.toBeInTheDocument();
        expect(screen.queryByText('Reload chat model now')).not.toBeInTheDocument();
    });

    it('keeps a very long model name whole in its tooltip', async () => {
        const long = `user.${'Very-Long-Model-Name-'.repeat(20)}GGUF`;
        mockedApi.getModelServer.mockResolvedValue(info({
            chat_model: { ...info().chat_model!, name: long },
        }));
        render(<ModelServerPanel />);

        const cell = await screen.findByText(long);
        expect(cell).toHaveAttribute('title', long);
        expect(cell).toHaveClass('model-server-value');
    });

    it('is one plain line when Lemonade is unreachable', async () => {
        mockedApi.getModelServer.mockResolvedValue({
            reachable: false, error: 'Lemonade is not answering at http://localhost:13305/api/v1.',
            base_url: 'http://localhost:13305/api/v1', owner: 'user',
        });
        render(<ModelServerPanel />);

        expect(await screen.findByRole('alert')).toHaveTextContent(
            'Lemonade is not answering at http://localhost:13305/api/v1.',
        );
        expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument();
    });

    it('opens the model picker', async () => {
        mockedApi.getModelServer.mockResolvedValue(info());
        render(<ModelServerPanel />);

        fireEvent.click(await screen.findByRole('button', { name: 'Change model' }));
        await waitFor(() => expect(useChatStore.getState().settingsSection).toBe('model'));
    });

    it('reloads the chat model at its current context window', async () => {
        mockedApi.getModelServer.mockResolvedValue(info());
        mockedApi.loadModelAskingFirst.mockResolvedValue(true);
        render(<ModelServerPanel />);

        fireEvent.click(await screen.findByRole('button', { name: 'Reload chat model now' }));
        await waitFor(() => expect(mockedApi.loadModelAskingFirst).toHaveBeenCalledWith('Qwen3.6-35B-A3B-GGUF', 65536));
    });
});
