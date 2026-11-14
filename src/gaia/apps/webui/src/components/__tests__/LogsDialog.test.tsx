// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../services/api';
import { useChatStore } from '../../stores/chatStore';
import { useLogsStore, type LogSource } from '../../stores/logsStore';
import type { LogReadResponse, LogSourcesResponse } from '../../types/api.gen';
import { LogsDialog } from '../LogsDialog';

vi.mock('../../services/api', async (importOriginal) => {
    const actual = await importOriginal<typeof import('../../services/api')>();
    return { ...actual, getLog: vi.fn(), getLogSources: vi.fn(), createSetupReport: vi.fn(), fetchSetupReport: vi.fn() };
});
const mocked = vi.mocked(api);

const LOG_PATH = 'C:\\Users\\me\\.gaia\\lemonade\\lemond.log';

function serverRead(over: Partial<LogReadResponse> = {}): LogReadResponse {
    return {
        owner: 'gaia',
        base_url: 'http://127.0.0.1:56150/api/v1',
        source: 'file',
        available: true,
        lines: [
            { text: '2026-11-04 10:00:00.000 [Info] (main) Starting Lemonade Server...', severity: 'info', tag: 'main' },
            { text: '2026-11-04 10:00:01.000 [Error] (FLM) npu device busy', severity: 'error', tag: 'FLM' },
        ],
        cursor: '120',
        reset: false,
        dropped: 0,
        log_path: LOG_PATH,
        server_answering: true,
        reason: null,
        detail: null,
        ...over,
    };
}

const SOURCES: LogSourcesResponse = {
    sources: [
        { id: 'model_server', files: [] },
        { id: 'gaia', files: [{ name: 'gaia.log', path: 'C:\\Users\\me\\.gaia\\gaia.log', size_bytes: 9_700_000, modified: 1 }] },
        { id: 'service', files: [] },
        {
            id: 'agent',
            files: [
                { name: 'gaia-agent.log', path: 'C:\\Users\\me\\.gaia\\logs\\gaia-agent.log', size_bytes: 2048, modified: 1 },
                { name: 'gaia/sidecar-54747.log', path: 'C:\\Users\\me\\.gaia\\agents\\gaia\\logs\\sidecar-54747.log', size_bytes: 512, modified: 2 },
            ],
        },
        { id: 'desktop', files: [] },
        { id: 'other', files: [] },
    ],
};

async function openLogs(source?: LogSource) {
    render(<LogsDialog />);
    await act(async () => { useLogsStore.getState().show(source); });
}

describe('the log viewer', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        mocked.getLog.mockResolvedValue(serverRead());
        mocked.getLogSources.mockResolvedValue(SOURCES);
    });

    afterEach(() => {
        vi.useRealTimers();
        act(() => {
            useLogsStore.setState({ open: false, source: 'model_server' });
            useChatStore.setState({ locale: 'en' });
        });
    });

    it('opens on the model server log and says where it came from', async () => {
        await openLogs();
        expect(await screen.findByText(/npu device busy/)).toBeInTheDocument();
        expect(screen.getByRole('dialog', { name: 'Logs' })).toBeInTheDocument();
        expect(screen.getByRole('tab', { name: 'Model server' })).toHaveAttribute('aria-selected', 'true');
        expect(screen.getByText(LOG_PATH)).toHaveAttribute('dir', 'ltr');
        expect(screen.getByLabelText('Model server', { selector: 'pre' })).toHaveAttribute('dir', 'ltr');
        expect(mocked.getLog).toHaveBeenCalledWith('model_server', { file: null, after: null });
    });

    it('gives focus back to the button that opened it when it closes', async () => {
        const opener = document.createElement('button');
        opener.textContent = 'View logs';
        document.body.appendChild(opener);
        opener.focus();
        render(<LogsDialog />);
        await act(async () => { useLogsStore.getState().show(); });
        expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus();
        await act(async () => { fireEvent.keyDown(document, { key: 'Escape' }); });
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
        // Not the page body, where the next Tab would start from the top behind Settings.
        expect(opener).toHaveFocus();
        opener.remove();
    });

    it('has a tab for every log GAIA keeps', async () => {
        await openLogs();
        const tabs = screen.getAllByRole('tab').map((tab) => tab.textContent);
        expect(tabs).toEqual(['Model server', 'GAIA', 'Background service', 'Agent', 'Desktop app', 'Other']);
    });

    it("reads GAIA's own log from its tab", async () => {
        mocked.getLog.mockResolvedValue(serverRead({ owner: null, base_url: null, log_path: 'C:\\Users\\me\\.gaia\\gaia.log', server_answering: null, lines: [{ text: '[2026-11-04 11:48:16,311] | ERROR | gaia.ui | chat.py:1 | turn failed', severity: 'error', tag: null }] }));
        await openLogs();
        fireEvent.click(screen.getByRole('tab', { name: 'GAIA' }));
        expect(await screen.findByText(/turn failed/)).toBeInTheDocument();
        expect(mocked.getLog).toHaveBeenLastCalledWith('gaia', { file: null, after: null });
        expect(screen.getByText(/this app's server, chats, tools/)).toBeInTheDocument();
    });

    it('shows the new tab, not a slow read of the old one', async () => {
        let finishModelServer: (r: LogReadResponse) => void = () => {};
        mocked.getLog.mockImplementation((source) => (source === 'model_server'
            ? new Promise<LogReadResponse>((resolve) => { finishModelServer = resolve; })
            : Promise.resolve(serverRead({ owner: null, base_url: null, lines: [{ text: 'gaia started', severity: 'info', tag: null }], cursor: '9' }))));
        await openLogs();
        fireEvent.click(screen.getByRole('tab', { name: 'GAIA' }));
        expect(await screen.findByText(/gaia started/)).toBeInTheDocument();
        await act(async () => { finishModelServer(serverRead()); });
        expect(screen.queryByText(/npu device busy/)).not.toBeInTheDocument();
        expect(screen.getByText(/gaia started/)).toBeInTheDocument();
    });

    it('picks among a group’s files', async () => {
        await openLogs('agent');
        const picker = await screen.findByRole('combobox', { name: 'File' });
        expect(picker).toHaveValue('gaia-agent.log');
        expect(screen.getAllByRole('option')[0]).toHaveTextContent('gaia-agent.log (⁦2 KB⁩)');
        fireEvent.change(picker, { target: { value: 'gaia/sidecar-54747.log' } });
        await waitFor(() => expect(mocked.getLog).toHaveBeenLastCalledWith('agent', { file: 'gaia/sidecar-54747.log', after: null }));
    });

    it('offers no file picker for a group with one file', async () => {
        await openLogs('gaia');
        await screen.findByText(/npu device busy/);
        expect(screen.queryByRole('combobox')).toBeNull();
    });

    it('says the model server is down, above its last lines', async () => {
        mocked.getLog.mockResolvedValue(serverRead({ server_answering: false }));
        await openLogs();
        expect(await screen.findByRole('alert')).toHaveTextContent("The model server isn't answering right now");
        expect(screen.getByText(/npu device busy/)).toBeInTheDocument();
    });

    it('filters to warnings and errors', async () => {
        await openLogs();
        await screen.findByText(/npu device busy/);
        fireEvent.click(screen.getByLabelText('Warnings and errors only'));
        expect(screen.queryByText(/Starting Lemonade Server/)).toBeNull();
        expect(screen.getByText(/npu device busy/)).toBeInTheDocument();
    });

    it('follows new lines from the cursor, replacing them when the log started over', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
        mocked.getLog
            .mockResolvedValueOnce(serverRead())
            .mockResolvedValueOnce(serverRead({ lines: [{ text: 'next line', severity: null, tag: null }], cursor: '200', server_answering: null }))
            .mockResolvedValueOnce(serverRead({ lines: [{ text: 'fresh start', severity: 'info', tag: 'main' }], cursor: '10', reset: true }));
        await openLogs();
        await screen.findByText(/npu device busy/);
        await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
        expect(mocked.getLog).toHaveBeenLastCalledWith('model_server', { file: null, after: '120' });
        expect(screen.getByText(/next line/)).toBeInTheDocument();
        expect(screen.getByText(/npu device busy/)).toBeInTheDocument();
        await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
        expect(mocked.getLog).toHaveBeenLastCalledWith('model_server', { file: null, after: '200' });
        expect(screen.getByText(/fresh start/)).toBeInTheDocument();
        expect(screen.queryByText(/npu device busy/)).toBeNull();
    });

    it('stops polling when following is off', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
        await openLogs();
        await screen.findByText(/npu device busy/);
        fireEvent.click(screen.getByLabelText('Follow new lines'));
        await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
        expect(mocked.getLog).toHaveBeenCalledTimes(1);
    });

    it.each<[LogSource, string, string]>([
        ['model_server', 'not_running', "Your Lemonade Server isn't answering"],
        ['model_server', 'remote', 'runs on another computer'],
        ['model_server', 'no_stream', "doesn't share its log"],
        ['model_server', 'no_file', "hasn't written a log yet"],
        ['service', 'no_file', 'Nothing has written this log on this PC yet'],
    ])('says why there is no %s log (%s) instead of an empty box', async (source, reason, text) => {
        mocked.getLog.mockResolvedValue(serverRead({
            available: false, source: null, lines: [], cursor: null, reason, detail: 'ws://x:9000/logs/stream: refused',
        }));
        await openLogs(source);
        expect(await screen.findByRole('alert')).toHaveTextContent(text);
        expect(document.querySelector('.log-viewer-lines')).toBeNull();
        expect(screen.getByRole('button', { name: /^Copy/ })).toBeDisabled();
    });

    it('reports a failed read', async () => {
        mocked.getLog.mockRejectedValue(new Error('HTTP 409: credential store locked'));
        await openLogs();
        expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't read the log: HTTP 409: credential store locked");
    });

    it('copies the lines shown', async () => {
        const writeText = vi.fn().mockResolvedValue(undefined);
        Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
        await openLogs();
        await screen.findByText(/npu device busy/);
        await act(async () => { fireEvent.click(screen.getByRole('button', { name: /^Copy/ })); });
        expect(writeText).toHaveBeenCalledWith(expect.stringContaining('[Error] (FLM) npu device busy'));
        expect(screen.getByRole('status')).toHaveTextContent('Copied the lines shown.');
    });

    it('saves every log as one report', async () => {
        mocked.createSetupReport.mockResolvedValue({ name: 'diagnostics-20261104-120000.tgz', path: 'C:\\Users\\me\\.gaia\\diagnostics-20261104-120000.tgz', size_bytes: 2_400_000 });
        mocked.fetchSetupReport.mockResolvedValue(new Blob(['x']));
        URL.createObjectURL = vi.fn(() => 'blob:x');
        URL.revokeObjectURL = vi.fn();
        await openLogs();
        await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save all logs' })); });
        expect(mocked.createSetupReport).toHaveBeenCalled();
        expect(mocked.fetchSetupReport).toHaveBeenCalledWith('diagnostics-20261104-120000.tgz');
        expect(await screen.findByText('C:\\Users\\me\\.gaia\\diagnostics-20261104-120000.tgz')).toHaveAttribute('dir', 'ltr');
    });

    it('closes on Escape', async () => {
        await openLogs();
        await screen.findByText(/npu device busy/);
        fireEvent.keyDown(document, { key: 'Escape' });
        await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    });

    it('reads in Arabic with the log itself left to right', async () => {
        act(() => useChatStore.setState({ locale: 'ar' }));
        await openLogs();
        await screen.findByText(/npu device busy/);
        expect(screen.getByRole('dialog', { name: /السجلات/ })).toBeInTheDocument();
        expect(screen.getByRole('tab', { name: /الخدمة في الخلفية/ })).toBeInTheDocument();
        expect(screen.getByLabelText(/التحذيرات والأخطاء فقط/)).toBeInTheDocument();
        expect(screen.getByText(LOG_PATH)).toHaveAttribute('dir', 'ltr');
    });
});
