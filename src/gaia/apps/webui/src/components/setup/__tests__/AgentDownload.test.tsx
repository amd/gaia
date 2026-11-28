// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../../services/api';
import { stripIsolates } from '../../../i18n/registry';
import { useChatStore } from '../../../stores/chatStore';
import { AgentDownloadRow, useAgentDownload } from '../AgentDownload';

vi.mock('../../../services/api', async (importOriginal) => {
    const real = await importOriginal<typeof api>();
    return { ...real, startAgentDownload: vi.fn(), getAgentDownload: vi.fn() };
});

const mockedApi = vi.mocked(api);

function Harness() {
    const { job, start } = useAgentDownload(() => {});
    return (
        <>
            <button type="button" onClick={() => void start()}>go</button>
            {job && <AgentDownloadRow job={job} name="gaiacpp" onRetry={() => void start()} />}
        </>
    );
}

beforeEach(() => {
    vi.clearAllMocks();
    useChatStore.setState({ locale: 'en' });
});

describe('AgentDownload refusals', () => {
    it.each([
        ['gaiacpp_update_chat_busy', 'GAIA is answering in a chat', 'يجيب GAIA حاليًا في محادثة'],
        ['gaiacpp_update_setup_busy', 'GAIA is still setting up', 'ما زال GAIA يكمِل الإعداد'],
        ['gaiacpp_already_installed', 'gaiacpp is already installed on this PC', 'مثبّت بالفعل على هذا الجهاز'],
    ] as const)('shows refusal %s in the chosen language, not the server\'s English', async (code, en, ar) => {
        const raw = 'raw English from server';
        mockedApi.startAgentDownload.mockRejectedValue(
            new api.ApiError(raw, 409, raw, code, [], { status: code, message: raw }),
        );
        const { unmount } = render(<Harness />);
        fireEvent.click(screen.getByRole('button', { name: 'go' }));
        expect(await screen.findByRole('alert')).toHaveTextContent(en);
        unmount();

        useChatStore.setState({ locale: 'ar' });
        render(<Harness />);
        fireEvent.click(screen.getByRole('button', { name: 'go' }));
        const alert = await screen.findByRole('alert');
        expect(stripIsolates(alert.textContent ?? '')).toContain(ar);
        expect(alert.textContent).not.toContain(raw);
    });

    it('keeps an unexplained failure behind Details', async () => {
        mockedApi.startAgentDownload.mockRejectedValue(new Error('Connection reset.'));
        render(<Harness />);
        fireEvent.click(screen.getByRole('button', { name: 'go' }));
        const alert = await screen.findByRole('alert');
        expect(alert.querySelector('pre')).toHaveTextContent('Connection reset.');
    });
});
