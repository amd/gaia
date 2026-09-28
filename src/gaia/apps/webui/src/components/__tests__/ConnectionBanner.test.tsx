// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { ConnectionBanner } from '../ConnectionBanner';
import { useChatStore } from '../../stores/chatStore';
import type { SystemStatus } from '../../types';

beforeEach(() => {
    useChatStore.setState({ backendConnected: true, currentSessionId: null, messages: [] });
});

describe('context-size remediation comes from the backend', () => {
    it.each([
        '/usr/bin/lemonade-server serve --ctx-size 32768',
        'LEMONADE_CTX_SIZE=32768 /usr/bin/lemond',
    ])('prints %s verbatim without appending flags', (command) => {
        useChatStore.setState({ systemStatus: {
            lemonade_running: true, model_loaded: 'Gemma-4-E4B-it-GGUF', model_downloaded: true, expected_model_loaded: true,
            context_size_sufficient: false, model_context_size: 8192, start_command: command,
        } as SystemStatus });
        const { container } = render(<ConnectionBanner />);
        expect(container.querySelector('code')?.textContent).toBe(command);
    });

    it('prints model-reload instructions when there is no restart command', () => {
        const instruction = 'Set the context size to 32768 and reload the model.';
        useChatStore.setState({ systemStatus: {
            lemonade_running: true, model_loaded: 'Gemma-4-E4B-it-GGUF', model_downloaded: true, expected_model_loaded: true,
            context_size_sufficient: false, model_context_size: 8192,
            start_command: null, start_instruction: instruction,
        } as SystemStatus });
        const { container } = render(<ConnectionBanner />);
        expect(screen.getByText(instruction, { exact: false })).toBeInTheDocument();
        expect(container.querySelector('code')).toBeNull();
    });
});
