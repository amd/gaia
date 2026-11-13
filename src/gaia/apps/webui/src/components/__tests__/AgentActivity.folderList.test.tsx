// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { AgentActivity, toolRowText } from '../AgentActivity';
import type { AgentStep } from '../../types';

vi.mock('../../services/api');

function listing(files: Array<Record<string, unknown>>, result: string): AgentStep {
    return {
        id: 1, type: 'tool', label: 'Using list_files', tool: 'list_files',
        active: false, success: true, result,
        fileList: { files, total: files.length }, timestamp: 1,
    };
}

function expand(step: AgentStep) {
    render(<AgentActivity steps={[step]} isActive={false} />);
    fireEvent.click(screen.getByRole('button', { name: new RegExp(toolRowText(step)) }));
}

function iconKind(name: string): string | null {
    const row = screen.getByText(name, { selector: '.file-list-name' }).closest('.file-list-item');
    return row?.querySelector('svg')?.getAttribute('data-kind') ?? null;
}

it('shows a folder-only listing as folders, not as an empty file list', () => {
    const step = listing(
        [{ name: 'docs', kind: 'folder' }, { name: 'src', kind: 'folder' }],
        'Found 2 folders:\n  docs/\n  src/',
    );
    expand(step);

    expect(iconKind('docs')).toBe('folder');
    expect(iconKind('src')).toBe('folder');
    expect(screen.queryByText(/0 file/)).toBeNull();
});

it('marks only the folders in a mixed listing', () => {
    const step = listing(
        [{ name: 'docs', kind: 'folder' }, { name: 'README.md' }],
        'Found 1 file and 1 folder:\n  docs/\n  README.md',
    );
    expand(step);

    expect(iconKind('docs')).toBe('folder');
    expect(iconKind('README.md')).toBeNull();
});

it('does not treat a file whose name looks like a folder kind as a folder', () => {
    const step = listing([{ name: 'folder', kind: 'file' }], 'Found 1 file:\n  folder');
    expand(step);

    expect(iconKind('folder')).toBeNull();
});
