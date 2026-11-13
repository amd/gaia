// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// A direction character in a name or text a tool returned is shown as a code,
// so the rest of the row can't be drawn reversed.

import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AgentActivity, formatArgsDisplay, linkifyPaths, toolRowText } from '../AgentActivity';
import { StepResult } from '../LongContent';
import { RenderCard } from '../render/RenderCard';
import type { AgentStep } from '../../types';

vi.mock('../../services/api');

const RLO = String.fromCodePoint(0x202e);
const NAME = `report${RLO}txt.md`;
const SHOWN = 'report[U+202E]txt.md';

function step(over: Partial<AgentStep>): AgentStep {
    return {
        id: 1, type: 'tool', label: 'Using tool', tool: 'search_file',
        active: false, success: true, result: 'done', timestamp: 1,
        ...over,
    };
}

function expanded(over: Partial<AgentStep>): HTMLElement {
    const { container } = render(<AgentActivity steps={[step(over)]} isActive={false} />);
    fireEvent.click(container.querySelector('.flow-tool-header')!);
    return container;
}

describe('activity rows', () => {
    it('marks the narration line, its tooltip and a skill name', () => {
        expect(toolRowText(step({ narration: `Reading file: ${NAME}` }))).toBe(`Reading file: ${SHOWN}`);
        expect(toolRowText(step({ tool: 'use_skill', detail: `brief${RLO}fdp` }))).toContain('brief[U+202E]fdp');
    });

    it('marks file names and folders in a file list', () => {
        const container = expanded({
            fileList: { files: [{ name: NAME, directory: `C:\\d${RLO}x` }], total: 1 },
        });
        expect(container.querySelector('.file-list-name')!.textContent).toBe(SHOWN);
        expect(container.querySelector('.file-list-dir')!.textContent).toBe('C:\\d[U+202E]x');
    });

    it('marks overrides in command output and leaves the rest as written', () => {
        const container = expanded({
            tool: 'run_shell_command',
            commandOutput: { command: `cat ${NAME}`, stdout: `ok ${NAME}\nسلام`, stderr: `no ${NAME}`, returnCode: 0 },
        });
        expect(container.querySelector('.cmd-text')!.textContent).toBe(`cat ${SHOWN}`);
        expect(container.querySelector('.cmd-stdout')!.textContent).toBe(`ok ${SHOWN}\nسلام`);
        expect(container.querySelector('.cmd-stderr')!.textContent).toBe(`no ${SHOWN}`);
    });

    it('marks a retrieved chunk source and its text', () => {
        const container = expanded({
            tool: 'query_documents',
            retrievalChunks: [{ id: 7, source: NAME, preview: `pre ${RLO}x`, content: `full ${RLO}x` }],
        });
        expect(container.querySelector('.chunk-source')!.textContent).toBe(SHOWN);
        expect(container.querySelector('.chunk-preview')!.textContent).toBe('pre [U+202E]x');
        fireEvent.click(container.querySelector('.chunk-header')!);
        expect(container.querySelector('.chunk-content')!.textContent).toBe('full [U+202E]x');
    });

    it('marks arguments', () => {
        const { container } = render(<>{formatArgsDisplay(`path: C:\\d\\${NAME}, mode: w`)}</>);
        expect(container.textContent).toContain(`C:\\d\\${SHOWN}`);
        expect(container.textContent).not.toContain(RLO);
    });
});

describe('tool results', () => {
    it('marks text around a linked path and the path itself', () => {
        const { container } = render(<>{linkifyPaths(`Wrote ${RLO}x to C:\\d\\${NAME} then ${RLO}y`)}</>);
        expect(container.textContent).toBe(`Wrote [U+202E]x to C:\\d\\${SHOWN} then [U+202E]y`);
    });

    it('marks a result with no path', () => {
        const { container } = render(<>{linkifyPaths(`saved ${NAME}`)}</>);
        expect(container.textContent).toBe(`saved ${SHOWN}`);
    });

    it('marks a long result shown without path links', () => {
        const long = `${'line\n'.repeat(30)}C:\\d\\${NAME}`;
        const { container } = render(<StepResult text={long} renderShort={(t) => t} />);
        expect(container.textContent).toContain(`C:\\d\\${SHOWN}`);
        expect(container.textContent).not.toContain(RLO);
    });
});

describe('audio card', () => {
    it('marks the file name under the player', () => {
        const { container } = render(
            <RenderCard render="audio" data={{ src: '/api/media/audio/a.wav', path: `C:\\out\\${NAME}` }} />,
        );
        expect(container.querySelector('.render-audio__meta')!.textContent).toBe(SHOWN);
    });
});
