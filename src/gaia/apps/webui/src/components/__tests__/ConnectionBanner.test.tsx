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

describe('the model server is down', () => {
    it.each([
        '/usr/bin/lemonade-server serve --ctx-size 32768',
        'LEMONADE_CTX_SIZE=32768 /usr/bin/lemond',
    ])('prints the backend start command %s verbatim', (command) => {
        useChatStore.setState({ systemStatus: { lemonade_running: false, start_command: command } as SystemStatus });
        const { container } = render(<ConnectionBanner />);
        expect(container.querySelector('code')?.textContent).toBe(command);
    });

    it('never invents a start command the backend did not give', () => {
        useChatStore.setState({ systemStatus: { lemonade_running: false, start_command: null } as SystemStatus });
        const { container } = render(<ConnectionBanner />);
        expect(screen.getByText(/The model server stopped responding/)).toBeInTheDocument();
        expect(container.querySelector('code')).toBeNull();
        expect(container).not.toHaveTextContent('lemonade-server serve');
    });

    it('says why the status could not be read', () => {
        useChatStore.setState({ systemStatus: { lemonade_running: false, lemonade_error: 'connection refused' } as SystemStatus });
        render(<ConnectionBanner />);
        expect(screen.getByText(/Could not read the model server's status: connection refused/)).toBeInTheDocument();
    });
});

describe('context window too small', () => {
    it('points at Settings instead of a shell command', () => {
        useChatStore.setState({ systemStatus: {
            lemonade_running: true, model_loaded: 'Gemma-4-E4B-it-GGUF', model_downloaded: true, expected_model_loaded: true,
            context_size_sufficient: false, model_context_size: 8192, start_command: 'LEMONADE_CTX_SIZE=32768 /usr/bin/lemond',
        } as SystemStatus });
        const { container } = render(<ConnectionBanner />);
        expect(screen.getByText(/LLM context window is too small/)).toBeInTheDocument();
        expect(screen.getByText(/Settings → Advanced/)).toBeInTheDocument();
        expect(container.querySelector('code')).toBeNull();
    });
});

describe('an unreadable config is surfaced, not hidden', () => {
    it('shows the backend config error verbatim', () => {
        const message = 'GAIA config at /home/u/.gaia/config.json is not valid JSON: x. Delete it to reset to defaults.';
        useChatStore.setState({ systemStatus: {
            lemonade_running: true, model_loaded: 'Gemma-4-E4B-it-GGUF', model_downloaded: true, expected_model_loaded: true,
            context_size_sufficient: true, config_error: message,
        } as SystemStatus });
        render(<ConnectionBanner />);
        expect(screen.getByRole('alert')).toHaveTextContent('GAIA settings could not be loaded.');
        expect(screen.getByText(message)).toBeInTheDocument();
        expect(screen.queryByLabelText('Dismiss')).toBeNull();
    });
});
