// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AgentActivity } from '../AgentActivity';
import { useChatStore } from '../../stores/chatStore';
import type { AgentStep } from '../../types';

vi.mock('../../services/api');

const FSI = '⁨';
const PDI = '⁩';

// What the relay sends for gaiacpp's remember and file_write calls.
function nativeCall(tool: string, narration: string): AgentStep {
    return { id: 1, type: 'tool', label: 'Using tool', tool, narration, active: false, success: true, result: 'ok', timestamp: 1 };
}

function rowText(step: AgentStep): string {
    const { container } = render(<AgentActivity steps={[step]} isActive={false} running={false} />);
    return container.querySelector('.flow-tool-label')?.textContent ?? '';
}

describe('a gaiacpp step line', () => {
    beforeEach(() => {
        useChatStore.setState({ agentSteps: [], locale: 'ar' });
    });

    it('words a memory write in Arabic, the saved text kept once as its own LTR island', () => {
        const text = rowText(nativeCall('remember', 'Saving to memory: My favourite fruit is mango.'));

        expect(text).toContain('الحفظ في الذاكرة');
        expect(text).toContain(`${FSI}My favourite fruit is mango.${PDI}`);
        expect(text.split('My favourite fruit is mango.')).toHaveLength(2);
    });

    it('words a file write in Arabic, the file name kept as sent', () => {
        const text = rowText(nativeCall('write_file', 'Writing file: ui_made.txt'));

        expect(text).toContain('كتابة ملف');
        expect(text).toContain(`${FSI}ui_made.txt${PDI}`);
        expect(text).not.toMatch(/^Writing file/);
    });

    it('reads the same as before in English', () => {
        useChatStore.setState({ locale: 'en' });

        expect(rowText(nativeCall('write_file', 'Writing file: ui_made.txt'))).toBe('Writing file: ui_made.txt');
    });

    it('leaves a narration GAIA has no label for as sent', () => {
        expect(rowText(nativeCall('git_status', 'Checking git status'))).toBe('Checking git status');
    });
});
