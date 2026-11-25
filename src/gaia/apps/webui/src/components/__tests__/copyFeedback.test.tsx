// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A copy button says "Copied" only once the clipboard took the text, and says so when it didn't. */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MessageBubble } from '../MessageBubble';
import { AgentActivity } from '../AgentActivity';
import type { AgentStep, Message } from '../../types';

vi.mock('../../services/api');

const answer: Message = {
    id: 2, session_id: 's', role: 'assistant', content: 'Run this:\n\n```js\nconsole.log(1)\n```', created_at: '', rag_sources: null,
};

const steps: AgentStep[] = [
    {
        id: 1, type: 'tool', label: 'Using tool', tool: 'run_shell_command', active: false, success: true, timestamp: 1,
        commandOutput: { command: 'dir', stdout: 'a.txt', stderr: '', returnCode: 0 },
    },
    { id: 2, type: 'tool', label: 'Using tool', tool: 'write_file', active: false, success: true, timestamp: 2 },
];

function setClipboard(writeText: ((text: string) => Promise<void>) | null): void {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: writeText ? { writeText } : undefined });
}

function setExecCommand(result: boolean): void {
    Object.defineProperty(document, 'execCommand', { configurable: true, value: vi.fn(() => result) });
}

afterEach(() => {
    setClipboard(null);
    Reflect.deleteProperty(document, 'execCommand');
});

function openCommandOutput(): void {
    render(<AgentActivity steps={steps} isActive={false} />);
    fireEvent.click(screen.getByRole('button', { name: /Ran command, wrote file/ }));
    fireEvent.click(screen.getByRole('button', { name: /Ran command$/ }));
}

describe('Copy message', () => {
    it('says Copied only after the Clipboard API resolves', async () => {
        let resolve!: () => void;
        setClipboard(vi.fn(() => new Promise<void>((r) => { resolve = r; })));
        render(<MessageBubble message={answer} />);
        fireEvent.click(screen.getByRole('button', { name: 'Copy message' }));

        expect(screen.queryByRole('button', { name: 'Copied to clipboard' })).toBeNull();
        await act(async () => resolve());
        expect(screen.getByRole('button', { name: 'Copied to clipboard' })).toBeInTheDocument();
    });

    it('says Copied when the Clipboard API refuses but execCommand works', async () => {
        setClipboard(vi.fn().mockRejectedValue(new Error('denied')));
        setExecCommand(true);
        render(<MessageBubble message={answer} />);
        fireEvent.click(screen.getByRole('button', { name: 'Copy message' }));

        expect(await screen.findByRole('button', { name: 'Copied to clipboard' })).toBeInTheDocument();
    });

    it("says it couldn't copy, never Copied, when both routes fail", async () => {
        setClipboard(vi.fn().mockRejectedValue(new Error('denied')));
        setExecCommand(false);
        render(<MessageBubble message={answer} />);
        fireEvent.click(screen.getByRole('button', { name: 'Copy message' }));

        const button = await screen.findByRole('button', { name: "Couldn't copy" });
        expect(button).toHaveClass('failed');
        expect(button).not.toHaveClass('copied');
        expect(screen.queryByRole('button', { name: 'Copied to clipboard' })).toBeNull();
    });
});

describe('Copy code', () => {
    it('says Copied after the Clipboard API resolves', async () => {
        setClipboard(vi.fn().mockResolvedValue(undefined));
        render(<MessageBubble message={answer} />);
        fireEvent.click(screen.getByRole('button', { name: 'Copy code' }));

        const button = await screen.findByRole('button', { name: 'Copied to clipboard' });
        expect(button).toHaveTextContent('Copied');
    });

    it("shows Couldn't copy, never Copied, when both routes fail", async () => {
        setClipboard(null);
        setExecCommand(false);
        render(<MessageBubble message={answer} />);
        fireEvent.click(screen.getByRole('button', { name: 'Copy code' }));

        const button = await screen.findByRole('button', { name: "Couldn't copy" });
        expect(button).toHaveTextContent("Couldn't copy");
        expect(button).not.toHaveClass('copied');
    });
});

describe('Copy command output', () => {
    it('says Copied when execCommand works after the Clipboard API refuses', async () => {
        setClipboard(vi.fn().mockRejectedValue(new Error('denied')));
        setExecCommand(true);
        openCommandOutput();
        fireEvent.click(screen.getByRole('button', { name: 'Copy command output' }));

        expect(await screen.findByRole('button', { name: 'Copied to clipboard' })).toHaveClass('copied');
    });

    it("says it couldn't copy, never Copied, when both routes fail", async () => {
        setClipboard(vi.fn().mockRejectedValue(new Error('denied')));
        setExecCommand(false);
        openCommandOutput();
        fireEvent.click(screen.getByRole('button', { name: 'Copy command output' }));

        const button = await screen.findByRole('button', { name: "Couldn't copy" });
        expect(button).toHaveClass('failed');
        expect(screen.queryByRole('button', { name: 'Copied to clipboard' })).toBeNull();
    });
});
