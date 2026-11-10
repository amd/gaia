// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ImageSettings } from '../ImageSettings';
import { ApiError } from '../../../services/api';
import * as api from '../../../services/imageApi';
import { useChatStore } from '../../../stores/chatStore';
import { useModelStore } from '../../../stores/modelStore';

vi.mock('../../../services/imageApi', async (importOriginal) => {
    const actual = await importOriginal<typeof import('../../../services/imageApi')>();
    return { ...actual, getImageSettings: vi.fn(), updateImageSettings: vi.fn() };
});

const mockedApi = vi.mocked(api);

const Z: api.ImageModel = {
    id: 'Z-Image-Turbo', recipe: 'sd-cpp', downloaded: true, downloads_on_first_use: false,
    size_gb: 20.7, default_size: '1024x1024', default_steps: 9,
    sizes: ['512x512', '1024x1024', '1280x720'],
};
const SDXL: api.ImageModel = {
    id: 'SDXL-Turbo', recipe: 'sd-cpp', downloaded: false, downloads_on_first_use: true,
    size_gb: 6.46, default_size: '512x512', default_steps: 4,
    sizes: ['512x512', '768x768', '1024x1024'],
};
const SETTINGS: api.ImageSettings = {
    model: 'Z-Image-Turbo', size: null, steps: null, max_steps: 100,
    models: [Z, SDXL], model_available: true,
    chat_model: 'Qwen3.6-35B-A3B-GGUF', chat_model_creates_images: true,
};

const text = (el: Element) => (el.textContent ?? '').replace(/[⁦⁩]/g, '');

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.getImageSettings.mockResolvedValue(SETTINGS);
    mockedApi.updateImageSettings.mockImplementation(async (change) => ({ ...SETTINGS, ...change }));
});

describe('ImageSettings', () => {
    it("offers each model, and the chosen model's own sizes and steps", async () => {
        render(<ImageSettings />);
        const model = await screen.findByLabelText('Model');
        expect(within(model).getAllByRole('option').map(text)).toEqual([
            'Z-Image-Turbo',
            'SDXL-Turbo (downloads on first use, 6.46 GB)',
        ]);
        expect(within(screen.getByLabelText('Size')).getAllByRole('option').map(text)).toEqual([
            'Automatic (1024x1024)', '512x512', '1024x1024', '1280x720',
        ]);
        expect(within(screen.getByLabelText('Steps')).getAllByRole('option').map(text)).toEqual([
            'Automatic (9)', '4', '8', '9', '12', '20', '30', '50',
        ]);
    });

    it('saves a model, a size and steps, and Automatic as null', async () => {
        render(<ImageSettings />);
        fireEvent.change(await screen.findByLabelText('Size'), { target: { value: '1280x720' } });
        await waitFor(() => expect(mockedApi.updateImageSettings).toHaveBeenCalledWith({ size: '1280x720' }));
        fireEvent.change(screen.getByLabelText('Steps'), { target: { value: '' } });
        await waitFor(() => expect(mockedApi.updateImageSettings).toHaveBeenCalledWith({ steps: null }));
        fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'SDXL-Turbo' } });
        await waitFor(() => expect(mockedApi.updateImageSettings).toHaveBeenCalledWith({ model: 'SDXL-Turbo' }));
    });

    it('says when the chat model will not create images, and leads to the fix', async () => {
        mockedApi.getImageSettings.mockResolvedValue({
            ...SETTINGS, chat_model: 'Gemma-4-E4B-it-GGUF', chat_model_creates_images: false,
        });
        useModelStore.setState({ names: { 'Gemma-4-E4B-it-GGUF': 'Gemma 4 E4B' } });
        render(<ImageSettings />);
        const notice = (await screen.findByRole('status')).textContent;
        expect(notice).toContain('Gemma 4 E4B');
        expect(notice).not.toContain('Gemma-4-E4B-it-GGUF');
        fireEvent.click(screen.getByRole('button', { name: 'Choose a chat model' }));
        expect(useChatStore.getState().settingsSection).toBe('model');
    });

    it('says when the saved model is gone instead of showing another as chosen', async () => {
        mockedApi.getImageSettings.mockResolvedValue({ ...SETTINGS, model: 'Krea-2-Turbo', model_available: false });
        render(<ImageSettings />);
        expect((await screen.findByRole('alert')).textContent).toContain('Krea-2-Turbo is no longer');
        expect((screen.getByLabelText('Model') as HTMLSelectElement).value).toBe('');
    });

    it('says why it could not load or save', async () => {
        mockedApi.updateImageSettings.mockRejectedValue(new ApiError('x', 422, 'Z-Image-Turbo takes the sizes 512x512', null));
        render(<ImageSettings />);
        fireEvent.change(await screen.findByLabelText('Size'), { target: { value: '512x512' } });
        expect((await screen.findByRole('alert')).textContent).toBe(
            "Couldn't save the image settings: Z-Image-Turbo takes the sizes 512x512",
        );
    });

    it('says why it could not load instead of showing empty controls', async () => {
        mockedApi.getImageSettings.mockRejectedValue(new ApiError('x', 500, 'config.json is not valid JSON', null));
        render(<ImageSettings />);
        expect((await screen.findByRole('alert')).textContent).toContain('config.json is not valid JSON');
        expect(screen.queryByLabelText('Model')).toBeNull();
    });

    it('says plainly when Lemonade is not answering, and loads again on Try again', async () => {
        const raw = "Could not read the image models from Lemonade at http://127.0.0.1:13305/api/v1: HTTPConnectionPool(host='127.0.0.1')";
        mockedApi.getImageSettings.mockRejectedValueOnce(new ApiError('x', 503, raw, null));
        render(<ImageSettings />);
        const alert = await screen.findByRole('alert');
        expect(alert.textContent).toContain("Lemonade isn't answering");
        expect(alert.textContent).not.toContain('HTTPConnectionPool');
        expect(screen.getByText(raw)).toBeTruthy();
        fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
        expect(await screen.findByLabelText('Model')).toBeTruthy();
    });
});
