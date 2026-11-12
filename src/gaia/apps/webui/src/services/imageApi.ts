// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Image creation: the default model, size and steps (`/api/images/settings`). */

import { apiFetch } from './api';

export interface ImageModel {
    id: string;
    recipe: string | null;
    downloaded: boolean;
    /** A legacy SD model that downloads the first time an image is made. */
    downloads_on_first_use: boolean;
    size_gb: number | null;
    default_size: string;
    default_steps: number;
    sizes: string[];
}

export interface ImageSettings {
    model: string;
    /** null: the model's own default. */
    size: string | null;
    steps: number | null;
    max_steps: number;
    models: ImageModel[];
    /** false when the saved model is no longer on this Lemonade. */
    model_available: boolean;
    chat_model: string;
    /** false when the chat model is too small to be offered image creation. */
    chat_model_creates_images: boolean;
}

export async function getImageSettings(): Promise<ImageSettings> {
    return apiFetch<ImageSettings>('GET', '/images/settings');
}

/** Fields left out keep their value; null size or steps means automatic. */
export async function updateImageSettings(
    data: Partial<Pick<ImageSettings, 'model' | 'size' | 'steps'>>,
): Promise<ImageSettings> {
    return apiFetch<ImageSettings>('PUT', '/images/settings', data);
}
