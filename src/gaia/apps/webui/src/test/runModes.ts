// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { expect } from 'vitest';
import type { RunMode, RunModeId, RunModesView } from '../types';

/** A `/run-modes` answer: which mode is in use and each mode's chat model. */
export function runModesView(
    active: RunModeId,
    chat: { local?: string; cloud?: string } = {},
    over: Partial<Record<RunModeId, Partial<RunMode['chat']>>> = {},
): RunModesView {
    const mode = (id: RunModeId, model: string): RunMode => ({
        id,
        active: id === active,
        chat: {
            model,
            display_name: model,
            provider: id === 'cloud' ? 'fireworks' : 'local',
            provider_name: id === 'cloud' ? 'Fireworks AI' : null,
            default_model: model,
            default_display_name: model,
            customized: false,
            ...over[id],
        },
        capabilities: [
            { id: 'vision', place: 'local', model: 'Gemma-4-E4B-it-GGUF', display_name: 'Gemma-4-E4B-it-GGUF', unavailable: null, cloud_note: id === 'cloud' ? 'not_tested' : null, settings: null },
            { id: 'speech_to_text', place: 'local', model: 'Whisper-Large-v3-Turbo', display_name: 'Whisper-Large-v3-Turbo', unavailable: null, cloud_note: id === 'cloud' ? 'not_offered' : null, settings: null },
            { id: 'read_aloud', place: 'local', model: 'kokoro-v1', display_name: 'kokoro-v1', unavailable: null, cloud_note: id === 'cloud' ? 'not_offered' : null, settings: 'voice' },
            { id: 'images', place: 'local', model: 'SDXL-Turbo', display_name: 'SDXL-Turbo', unavailable: null, cloud_note: id === 'cloud' ? 'not_offered' : null, settings: 'images' },
            { id: 'documents', place: 'local', model: 'user.embeddinggemma-300m-GGUF', display_name: 'user.embeddinggemma-300m-GGUF', unavailable: null, cloud_note: id === 'cloud' ? 'not_tested' : null, settings: null },
        ],
    });
    return {
        active,
        modes: [
            mode('local', chat.local ?? 'Gemma-4-E4B-it-GGUF'),
            mode('cloud', chat.cloud ?? 'fireworks.deepseek-v4p1-flash'),
        ],
    };
}

/** Open the composer's run-mode menu with both modes' model lists showing. */
export async function openModeMenu(): Promise<HTMLElement> {
    fireEvent.click(screen.getByRole('button', { name: /^(Local|Cloud|Mode)\b/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Choose where GAIA runs' });
    // A model server that can't be read shows its error instead of the modes.
    await waitFor(() => expect(dialog.querySelector('[role="listbox"], [role="alert"]')).not.toBeNull());
    for (const mode of ['Local', 'Cloud']) {
        const expand = within(dialog).queryByRole('button', { name: `Show ${mode} models` });
        if (expand && expand.getAttribute('aria-expanded') !== 'true') fireEvent.click(expand);
    }
    return dialog;
}
