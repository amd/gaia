// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Agent info (#440): the chat's tools, grouped, and every way they can't be listed. */

import { render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AgentInfoSection, categoryLabel, groupTools } from '../AgentInfoSection';
import { useSessionPanelStore } from '../../../stores/sessionPanelStore';
import { tEn } from '../../../i18n/useT';
import type { AgentTool } from '../../../types/sessionPanel';
import { resetPanelState, SID, stubPanelApi } from './testUtils';

vi.mock('../../../services/api');

let mocked: ReturnType<typeof stubPanelApi>;

const TOOLS: AgentTool[] = [
    { name: 'query_documents', description: 'Search the indexed documents.\n\nArgs: ...', category: 'rag' },
    { name: 'mcp_github_list_issues', description: '[MCP:github] List issues.', category: 'mcp' },
    { name: 'write_file', description: 'Write a file.', category: 'file_io' },
    { name: 'index_document', description: 'Index a document.', category: 'rag' },
    { name: 'rss-digest/fetch_rss', description: '', category: 'skill' },
];

function httpError(httpStatus: number, detail: string) {
    // The real ApiError is mocked away; the section only reads these fields.
    return Object.assign(new Error(detail), { detail, httpStatus });
}

function expand() {
    useSessionPanelStore.getState().setSection('tools', true);
}

beforeEach(() => {
    vi.clearAllMocks();
    mocked = stubPanelApi();
    resetPanelState();
});

describe('AgentInfoSection', () => {
    it('starts collapsed and asks for nothing until expanded', () => {
        render(<AgentInfoSection sessionId={SID} busy={false} />);
        expect(screen.getByRole('button', { name: 'Agent info' })).toHaveAttribute('aria-expanded', 'false');
        expect(mocked.getCapabilities).not.toHaveBeenCalled();
    });

    it('lists the tools grouped by category, each with its first description line', async () => {
        mocked.getCapabilities.mockResolvedValue({ session_id: SID, agent_type: 'gaia', tools: TOOLS });
        expand();
        render(<AgentInfoSection sessionId={SID} busy={false} />);

        const list = await screen.findByRole('group', { name: "Tools this chat's agent can use" });
        expect(mocked.getCapabilities).toHaveBeenCalledWith(SID);
        const rag = within(list).getByRole('region', { name: 'rag' });
        expect(within(rag).getAllByRole('listitem').map((li) => li.querySelector('code')?.textContent))
            .toEqual(['query_documents', 'index_document']);
        expect(rag).toHaveTextContent('Search the indexed documents.');
        expect(rag).not.toHaveTextContent('Args');
        expect(within(list).getByRole('region', { name: 'Connectors' })).toHaveTextContent('mcp_github_list_issues');
        expect(within(list).getByRole('region', { name: 'file io' })).toHaveTextContent('write_file');
        expect(within(list).getByRole('region', { name: 'Skills' })).toHaveTextContent('rss-digest/fetch_rss');
    });

    it('says the agent is not running, from the server, instead of an empty list', async () => {
        const detail = "This chat's agent isn't running, so its tools can't be listed yet.";
        mocked.getCapabilities.mockRejectedValue(httpError(409, detail));
        expand();
        render(<AgentInfoSection sessionId={SID} busy={false} />);

        expect(await screen.findByRole('status')).toHaveTextContent(detail);
        expect(screen.queryByRole('group')).not.toBeInTheDocument();
        expect(screen.queryByText(/has no tools/)).not.toBeInTheDocument();
    });

    it.each([
        [503, 'The installed GAIA agent is too old to list its tools here.'],
        [502, "The GAIA agent sent a tool list GAIA can't read ('write_file' listed twice)."],
    ])('shows a %i as an error with the reason', async (status, detail) => {
        mocked.getCapabilities.mockRejectedValue(httpError(status, detail));
        expand();
        render(<AgentInfoSection sessionId={SID} busy={false} />);

        expect(await screen.findByRole('alert')).toHaveTextContent(`Couldn't list this agent's tools: ${detail}`);
    });

    it('treats a response without a tool list as an error, not as no tools', async () => {
        mocked.getCapabilities.mockResolvedValue({ session_id: SID } as never);
        expand();
        render(<AgentInfoSection sessionId={SID} busy={false} />);

        expect(await screen.findByRole('alert')).toHaveTextContent('the server sent no list of tools');
        expect(screen.queryByText(/has no tools/)).not.toBeInTheDocument();
    });

    it('says so when the running agent really has no tools', async () => {
        mocked.getCapabilities.mockResolvedValue({ session_id: SID, agent_type: 'gaia', tools: [] });
        expand();
        render(<AgentInfoSection sessionId={SID} busy={false} />);

        expect(await screen.findByText(/has no tools/)).toBeInTheDocument();
    });

    it('reads again when a reply ends, so the first reply fills it in', async () => {
        mocked.getCapabilities.mockRejectedValueOnce(httpError(409, 'not running'));
        expand();
        const { rerender } = render(<AgentInfoSection sessionId={SID} busy={false} />);
        await screen.findByRole('status');

        mocked.getCapabilities.mockResolvedValue({ session_id: SID, agent_type: 'gaia', tools: TOOLS });
        rerender(<AgentInfoSection sessionId={SID} busy />);
        rerender(<AgentInfoSection sessionId={SID} busy={false} />);

        await screen.findByRole('group', { name: "Tools this chat's agent can use" });
        expect(screen.queryByRole('status')).not.toBeInTheDocument();
        expect(mocked.getCapabilities).toHaveBeenCalledTimes(2);
    });

    it('drops an answer for the chat it has since left', async () => {
        let resolveOld: (v: unknown) => void = () => {};
        mocked.getCapabilities.mockImplementationOnce(() => new Promise((r) => { resolveOld = r; }) as never);
        mocked.getCapabilities.mockResolvedValueOnce({
            session_id: 'session-2', agent_type: 'gaia', tools: [TOOLS[2]],
        });
        expand();
        const { rerender } = render(<AgentInfoSection sessionId={SID} busy={false} />);
        rerender(<AgentInfoSection sessionId="session-2" busy={false} />);
        await screen.findByText('write_file');

        resolveOld({ session_id: SID, agent_type: 'gaia', tools: [TOOLS[0]] });
        await waitFor(() => expect(screen.queryByText('query_documents')).not.toBeInTheDocument());
        expect(screen.getByText('write_file')).toBeInTheDocument();
    });
});

describe('groupTools', () => {
    it('keeps first-appearance order and never splits a category', () => {
        expect(groupTools(TOOLS).map(([c, g]) => [c, g.length])).toEqual([
            ['rag', 2], ['mcp', 1], ['file_io', 1], ['skill', 1],
        ]);
    });

    it('keeps two tools with the same name in one category apart (no dedupe client-side)', () => {
        const dup = [TOOLS[2], { ...TOOLS[2] }];
        expect(groupTools(dup)[0][1]).toHaveLength(2);
    });
});

describe('categoryLabel', () => {
    it('names the fixed categories and spells out mixin names', () => {
        expect(categoryLabel('mcp', tEn)).toBe('Connectors');
        expect(categoryLabel('skill', tEn)).toBe('Skills');
        expect(categoryLabel('agent', tEn)).toBe('Built in');
        expect(categoryLabel('code_index', tEn)).toBe('code index');
    });
});
