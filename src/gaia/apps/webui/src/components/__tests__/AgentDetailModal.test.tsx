// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Regression tests for issue #2970: the Agent Hub "Details" modal showed an
 * empty DETAILS heading with no version anywhere for installed agents (e.g.
 * the Email agent), because the section only ever rendered Model/Tools/Min
 * Memory/Fallback and never read `agent.version`.
 *
 * Pins:
 *   - the installed version renders when present
 *   - the Details section is omitted entirely (no bare heading) when none of
 *     version/models/tools/min_memory_gb are set
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AgentDetailModal } from '../AgentDetailModal';
import type { AgentInfo } from '../../types';

function makeAgent(overrides: Partial<AgentInfo> = {}): AgentInfo {
    return {
        id: 'email',
        name: 'Email',
        description: 'Email triage agent',
        source: 'installed',
        conversation_starters: [],
        models: [],
        ...overrides,
    };
}

describe('AgentDetailModal', () => {
    it('renders the installed version in the Details section', () => {
        const agent = makeAgent({ version: '1.4.2' });
        render(<AgentDetailModal agent={agent} onClose={() => {}} onStartChat={() => {}} />);

        expect(screen.getByText('Details')).toBeInTheDocument();
        expect(screen.getByText('Version')).toBeInTheDocument();
        expect(screen.getByText('1.4.2')).toBeInTheDocument();
    });

    it('omits the Details section entirely when it has no content', () => {
        const agent = makeAgent({
            version: undefined,
            models: [],
            tools_count: 0,
            min_memory_gb: null,
        });
        render(<AgentDetailModal agent={agent} onClose={() => {}} onStartChat={() => {}} />);

        expect(screen.queryByText('Details')).not.toBeInTheDocument();
    });

    it('still renders Details when only tools/models/memory are present (no version)', () => {
        const agent = makeAgent({ version: undefined, tools_count: 3 });
        render(<AgentDetailModal agent={agent} onClose={() => {}} onStartChat={() => {}} />);

        expect(screen.getByText('Details')).toBeInTheDocument();
        expect(screen.getByText('Tools')).toBeInTheDocument();
        expect(screen.queryByText('Version')).not.toBeInTheDocument();
    });
});
