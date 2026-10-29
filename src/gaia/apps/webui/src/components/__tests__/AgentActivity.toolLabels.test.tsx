// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { AgentActivity } from '../AgentActivity';
import type { AgentStep } from '../../types';

vi.mock('../../services/api');

function step(tool: string, active = false, success = true): AgentStep {
    return { id: 1, type: 'tool', label: `Using ${tool}`, tool, active, success, timestamp: 1 };
}

it.each([
    ['search_web', 'Searched web'],
    ['request_user_input', 'Asked you'],
    ['find_files', 'Found files'],
    ['search_past_conversations', 'Searched past chats'],
    ['index_documents', 'Indexed documents'],
])('names the GAIA agent\'s %s step instead of "Used tool"', (tool, label) => {
    render(<AgentActivity steps={[step(tool)]} isActive={false} />);

    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.queryByText('Used tool')).toBeNull();
});

it.each(['remember', 'recall', 'update_memory', 'forget'])(
    'keeps a successful %s step out of the activity list',
    (tool) => {
        const steps = [step(tool), { ...step('find_files'), id: 2 }];
        render(<AgentActivity steps={steps} isActive={false} />);

        expect(screen.getByText('Found files')).toBeInTheDocument();
        expect(screen.queryByText(/memory/i)).toBeNull();
    },
);

it('shows nothing when memory was the only activity', () => {
    const { container } = render(
        <AgentActivity steps={[step('remember')]} isActive={false} />,
    );

    expect(container).toBeEmptyDOMElement();
});

it('still shows a memory step that failed', () => {
    render(<AgentActivity steps={[step('remember', false, false)]} isActive={false} />);

    expect(screen.getByText(/memory/i)).toBeInTheDocument();
});

it.each([
    ['write_file', 'Writing file failed', 'Wrote file'],
    ['read_file', 'Reading file failed', 'Read file'],
    ['some_unknown_tool', 'Using some unknown tool failed', 'Used some unknown tool'],
])('does not give a failed or denied %s step the done label', (tool, label, doneLabel) => {
    render(<AgentActivity steps={[step(tool, false, false)]} isActive={false} />);

    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.queryByText(doneLabel)).toBeNull();
});

it('does not say a step stopped before its result "Wrote file"', () => {
    const stopped: AgentStep = { id: 1, type: 'tool', label: 'Using write_file', tool: 'write_file', active: false, timestamp: 1 };
    render(<AgentActivity steps={[stopped]} isActive={false} />);

    expect(screen.getByText("Writing file didn't finish")).toBeInTheDocument();
    expect(screen.queryByText('Wrote file')).toBeNull();
});

it('keeps the done label for a finished step saved without a success flag', () => {
    const legacy: AgentStep = { id: 1, type: 'tool', label: 'Using write_file', tool: 'write_file', active: false, result: 'success', timestamp: 1 };
    render(<AgentActivity steps={[legacy]} isActive={false} />);

    expect(screen.getByText('Wrote file')).toBeInTheDocument();
});

it('names a running GAIA agent step in the present tense', () => {
    render(<AgentActivity steps={[step('request_user_input', true)]} isActive={true} />);

    expect(screen.getByText('Asking you')).toBeInTheDocument();
});

it('leaves hidden memory steps out of a folded summary', () => {
    const steps = [step('remember'), { ...step('find_files'), id: 2 }, { ...step('read_file'), id: 3 }];
    render(<AgentActivity steps={steps} isActive={false} />);

    expect(screen.getByRole('button', { name: /^Found files, read file$/ })).toBeInTheDocument();
});

it('names a tool it has no label for, rather than "Used tool"', () => {
    render(<AgentActivity steps={[step('generate_image')]} isActive={false} />);

    expect(screen.getByText('Used generate image')).toBeInTheDocument();
    expect(screen.queryByText('Used tool')).toBeNull();
});
