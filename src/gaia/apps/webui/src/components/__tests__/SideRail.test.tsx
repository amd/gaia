// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The rail beside the answer: sources, files, and where the turn's data went (#5222). */

import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FILE_RECHECK_MS, SideRail, SIDE_RAIL_FOLD_QUERY, liveDataFailed } from '../SideRail';
import type { Message, TurnSummary } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const LOCAL_MODEL = { remote: false, provider: 'lemonade', provider_name: 'Lemonade on this machine', name: 'Gemma-4-E4B-it-GGUF' };
const CLOUD_MODEL = { remote: true, provider: 'fireworks', provider_name: 'Fireworks AI', name: 'glm-5p3-flash' };

function summary(over: Partial<TurnSummary> = {}): TurnSummary {
    return {
        pages: [],
        documents: [],
        files: [],
        sites: [],
        live_data: { attempted: 0, failed: 0 },
        cost: { kind: 'local' },
        model: LOCAL_MODEL,
        ...over,
    };
}

function turn(s: TurnSummary | undefined, id = 2): Message[] {
    return [
        { id: 1, session_id: 's', role: 'user', content: 'q', created_at: '', rag_sources: null },
        { id, session_id: 's', role: 'assistant', content: 'a', created_at: '', rag_sources: null, turnSummary: s },
    ];
}

function stubWindowWidth(narrow: boolean) {
    Object.defineProperty(window, 'matchMedia', {
        configurable: true,
        value: (query: string) => ({
            matches: query === SIDE_RAIL_FOLD_QUERY && narrow,
            media: query,
            addEventListener: vi.fn(),
            removeEventListener: vi.fn(),
        }),
    });
}

function rail() {
    return screen.getByRole('complementary', { name: 'Sources and turn details' });
}

beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    stubWindowWidth(false);
    mockedApi.openFileInApp.mockResolvedValue({ status: 'ok', path: '' });
    mockedApi.openFileOrFolder.mockResolvedValue({ status: 'ok', path: '' });
    mockedApi.getFileStatus.mockImplementation(async (paths) => ({
        files: paths.map((path) => ({ path, exists: true, mtime: 100 })),
    }));
});

afterEach(() => {
    delete (window as { matchMedia?: unknown }).matchMedia;
    vi.useRealTimers();
});

const USER_FOLLOW_UP: Message = { id: 9, session_id: 's', role: 'user', content: 'next', created_at: '', rag_sources: null };

describe('SideRail', () => {
    it('stays hidden until a turn has something to show', () => {
        const { container, rerender } = render(<SideRail messages={[]} />);
        expect(container).toBeEmptyDOMElement();
        rerender(<SideRail messages={turn(undefined)} />);
        expect(container).toBeEmptyDOMElement();
        rerender(<SideRail messages={[USER_FOLLOW_UP]} busy />);
        expect(container).toBeEmptyDOMElement();
    });

    it("tracks the latest turn, not an older one's summary", () => {
        const messages = [...turn(summary()), ...turn(undefined, 4)];
        const { container } = render(<SideRail messages={messages} />);
        expect(container).toBeEmptyDOMElement();
    });

    it("never shows the previous answer's data while a new turn runs", () => {
        const before = summary({ model: CLOUD_MODEL, sites: ['amd.com'] });
        const { rerender } = render(<SideRail messages={turn(before)} />);
        expect(rail()).toHaveTextContent('Fireworks AI (cloud)');

        rerender(<SideRail messages={[...turn(before), USER_FOLLOW_UP]} busy />);
        expect(rail()).toHaveTextContent('Working on this answer');
        expect(rail()).not.toHaveTextContent('Fireworks AI');
        expect(rail()).not.toHaveTextContent('amd.com');
    });

    it("hides a previous answer's data when the latest message has no answer yet", () => {
        const { container } = render(<SideRail messages={[...turn(summary()), USER_FOLLOW_UP]} />);
        expect(container).toBeEmptyDOMElement();
    });

    it('shows a file an Undo removed as gone, with nothing to open', async () => {
        vi.useFakeTimers();
        render(<SideRail messages={turn(summary({
            files: [{ path: 'C:/out/report.md', name: 'report.md', openable: true, mtime: 100 }],
        }))} />);
        await act(async () => { await Promise.resolve(); });
        expect(screen.getByRole('button', { name: 'Open' })).toBeInTheDocument();

        mockedApi.getFileStatus.mockResolvedValue({ files: [{ path: 'C:/out/report.md', exists: false }] });
        await act(async () => { vi.advanceTimersByTime(FILE_RECHECK_MS); await Promise.resolve(); });
        expect(screen.getByText('No longer on disk')).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Open' })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /Show in folder/ })).not.toBeInTheDocument();
    });

    it('marks a file restored or edited after the turn as changed', async () => {
        mockedApi.getFileStatus.mockResolvedValue({ files: [{ path: 'C:/out/data.csv', exists: true, mtime: 250 }] });
        render(<SideRail messages={turn(summary({
            files: [{ path: 'C:/out/data.csv', name: 'data.csv', openable: true, mtime: 100 }],
        }))} />);
        expect(await screen.findByText('Changed since this turn')).toBeInTheDocument();
        expect(mockedApi.getFileStatus).toHaveBeenCalledWith(['C:/out/data.csv']);
    });

    it('says so when the files cannot be re-checked', async () => {
        mockedApi.getFileStatus.mockRejectedValue(new Error('server down'));
        render(<SideRail messages={turn(summary({
            files: [{ path: 'C:/out/a.md', name: 'a.md', openable: true, mtime: 1 }],
        }))} />);
        expect(await screen.findByText(/Couldn't check whether these files changed: server down/)).toBeInTheDocument();
    });

    it('lists the pages a web-research answer used, as links', () => {
        render(<SideRail messages={turn(summary({
            pages: [
                { url: 'https://en.wikipedia.org/wiki/Kestrel', title: 'Kestrel - Wikipedia', kind: 'page' },
                { url: 'https://www.audubon.org/kestrel', title: null, kind: 'search_result' },
            ],
            sites: ['duckduckgo.com', 'en.wikipedia.org'],
        }))} />);
        const link = screen.getByRole('link', { name: /Kestrel - Wikipedia/ });
        expect(link).toHaveAttribute('href', 'https://en.wikipedia.org/wiki/Kestrel');
        expect(link).toHaveAttribute('target', '_blank');
        expect(screen.getByRole('link', { name: /audubon\.org/ })).toBeInTheDocument();
        expect(screen.getByText(/search result/)).toBeInTheDocument();
        expect(screen.getByText('duckduckgo.com, en.wikipedia.org')).toBeInTheDocument();
    });

    it('names document passages by file, with pages and counts', () => {
        render(<SideRail messages={turn(summary({
            documents: [{ name: 'kestrel-plan.pdf', path: 'C:/u/uploads/f27f.pdf', pages: [1, 2], passages: 3 }],
        }))} />);
        expect(screen.getByText('kestrel-plan.pdf')).toBeInTheDocument();
        expect(screen.getByText('pp. 1, 2 · 3 passages')).toBeInTheDocument();
        expect(screen.queryByText(/f27f/)).not.toBeInTheDocument();
        expect(screen.getByText('Read on this PC and stayed here.')).toBeInTheDocument();
    });

    it('says a local turn ran on this PC with no cloud charges', () => {
        render(<SideRail messages={turn(summary())} />);
        const facts = within(rail());
        expect(facts.getByText('This PC')).toBeInTheDocument();
        expect(facts.getByText('No cloud charges')).toBeInTheDocument();
        expect(facts.getByText("None from GAIA's web tools")).toBeInTheDocument();
    });

    it('names the cloud provider, says passages were sent there, and shows the estimate', () => {
        render(<SideRail messages={turn(summary({
            model: CLOUD_MODEL,
            cost: { kind: 'estimate', usd: 0.0123 },
            documents: [{ name: 'plan.docx', path: '/d/plan.docx', pages: [], passages: 1 }],
        }))} />);
        expect(screen.getByText('Fireworks AI (cloud)')).toBeInTheDocument();
        expect(screen.queryByText('This PC')).not.toBeInTheDocument();
        expect(screen.getByText('Searched on this PC. The passages used were sent to Fireworks AI.')).toBeInTheDocument();
        expect(screen.getByText('About $0.01 at list price')).toBeInTheDocument();
    });

    it('reports an unknown location instead of assuming this PC', () => {
        render(<SideRail messages={turn(summary({ model: null, cost: { kind: 'unknown' } }))} />);
        expect(screen.getByText('Not reported')).toBeInTheDocument();
        expect(screen.queryByText('This PC')).not.toBeInTheDocument();
        expect(screen.getByText('Not known')).toBeInTheDocument();
    });

    it('opens a written file or shows it in its folder', async () => {
        render(<SideRail messages={turn(summary({
            files: [
                { path: 'C:/out/recommendation.md', name: 'recommendation.md', openable: true },
                { path: 'C:/out/build.py', name: 'build.py', openable: false },
            ],
        }))} />);
        const [md, py] = screen.getAllByRole('listitem');
        await act(async () => { fireEvent.click(within(md).getByRole('button', { name: 'Open' })); });
        expect(mockedApi.openFileInApp).toHaveBeenCalledWith('C:/out/recommendation.md');
        expect(within(py).queryByRole('button', { name: 'Open' })).not.toBeInTheDocument();
        await act(async () => { fireEvent.click(within(py).getByRole('button', { name: /Show in folder/ })); });
        expect(mockedApi.openFileOrFolder).toHaveBeenCalledWith('C:/out/build.py', true);
    });

    it('says so when a file cannot be opened', async () => {
        mockedApi.openFileInApp.mockRejectedValue(new Error('Path not found'));
        render(<SideRail messages={turn(summary({
            files: [{ path: 'C:/out/gone.md', name: 'gone.md', openable: true }],
        }))} />);
        await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Open' })); });
        expect(screen.getByRole('alert')).toHaveTextContent("Couldn't open gone.md: Path not found");
    });

    it("labels the answer \"couldn't get live data\" only when every live-data tool failed", () => {
        const failed = summary({ live_data: { attempted: 2, failed: 2 } });
        expect(liveDataFailed(failed)).toBe(true);
        expect(liveDataFailed(summary({ live_data: { attempted: 2, failed: 1 } }))).toBe(false);
        expect(liveDataFailed(summary())).toBe(false);
        render(<SideRail messages={turn(failed)} />);
        expect(screen.getByRole('status')).toHaveTextContent("Couldn't get live data");
    });

    it('says when the summary could not be built instead of claiming no sites', () => {
        render(<SideRail messages={turn({ cost: { kind: 'unknown' }, error: 'KeyError: model' })} />);
        expect(screen.getByRole('alert')).toHaveTextContent('KeyError: model');
        expect(screen.queryByText('Websites')).not.toBeInTheDocument();
    });

    it('collapses to a button and remembers it', () => {
        const { unmount } = render(<SideRail messages={turn(summary())} />);
        fireEvent.click(screen.getByRole('button', { name: 'Hide sources and turn details' }));
        expect(screen.queryByRole('complementary')).not.toBeInTheDocument();
        unmount();
        render(<SideRail messages={turn(summary())} />);
        fireEvent.click(screen.getByRole('button', { name: 'Show sources and turn details' }));
        expect(rail()).toHaveClass('side-rail-docked');
    });

    it('folds away on a narrow window and opens as an overlay', () => {
        stubWindowWidth(true);
        render(<SideRail messages={turn(summary({ live_data: { attempted: 1, failed: 1 } }))} />);
        expect(screen.queryByRole('complementary')).not.toBeInTheDocument();
        const tab = screen.getByRole('button', { name: 'Show sources and turn details' });
        expect(tab).toHaveTextContent("Couldn't get live data");
        fireEvent.click(tab);
        expect(rail()).toHaveClass('side-rail-overlay');
        fireEvent.click(screen.getByRole('button', { name: 'Hide sources and turn details' }));
        expect(screen.queryByRole('complementary')).not.toBeInTheDocument();
    });
});
