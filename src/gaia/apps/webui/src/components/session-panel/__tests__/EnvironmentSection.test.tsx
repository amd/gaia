// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Environment (#5276): where the chat runs and what it may touch, each change sent to the backend. */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { EnvironmentSection } from '../EnvironmentSection';
import { OfflineNotice } from '../OfflineNotice';
import { PermissionModeChip } from '../../PermissionModeChip';
import { useChatStore } from '../../../stores/chatStore';
import { announceMobileAccess } from '../../../utils/mobileAccessEvents';
import { ENV, resetPanelState, SID, stubPanelApi } from './testUtils';

vi.mock('../../../services/api');

let mocked: ReturnType<typeof stubPanelApi>;

function setPlace(remote: boolean) {
    useChatStore.setState({
        sessions: [{
            id: SID, title: 't', created_at: '', updated_at: '', model: 'm', system_prompt: null, message_count: 0,
            document_ids: [], inference_remote: remote,
            inference_provider_name: remote ? 'Fireworks AI' : null,
            inference_description: remote ? 'Your messages are sent to Fireworks AI.' : null,
        } as never],
        currentSessionId: SID,
    });
}

beforeEach(() => {
    vi.clearAllMocks();
    mocked = stubPanelApi();
    resetPanelState();
    setPlace(false);
});

describe('Runs on', () => {
    it('names this PC, the device and backend, the model and its memory', async () => {
        render(<EnvironmentSection sessionId={SID} busy={false} />);
        expect(await screen.findByText(/This PC · GPU · Vulkan/)).toBeInTheDocument();
        expect(screen.getByText('Gemma 4 E4B')).toBeInTheDocument();
        expect(screen.getByText('6.2 GB in use · 40.5 GB free')).toBeInTheDocument();
    });

    it("doesn't describe another chat's model as this one's", async () => {
        useChatStore.setState({
            sessions: [{ ...useChatStore.getState().sessions[0], effective_model: 'Qwen3.6-35B-A3B-GGUF' }],
        });
        render(<EnvironmentSection sessionId={SID} busy={false} />);
        expect(await screen.findByText(/not loaded right now/)).toBeInTheDocument();
        expect(screen.queryByText('Gemma 4 E4B')).not.toBeInTheDocument();
        expect(screen.queryByText(/GB in use/)).not.toBeInTheDocument();
    });

    it('names the cloud provider for a cloud chat, without reading local status', async () => {
        setPlace(true);
        render(<EnvironmentSection sessionId={SID} busy={false} />);
        expect(await screen.findByText(/Fireworks AI \(cloud\)/)).toBeInTheDocument();
        expect(mocked.getLocalModelStatus).not.toHaveBeenCalled();
    });

    it('re-reads where it runs when a reply ends, not before', async () => {
        const { rerender } = render(<EnvironmentSection sessionId={SID} busy={false} />);
        await waitFor(() => expect(mocked.getLocalModelStatus).toHaveBeenCalledTimes(1));
        rerender(<EnvironmentSection sessionId={SID} busy />);
        rerender(<EnvironmentSection sessionId={SID} busy={false} />);
        await waitFor(() => expect(mocked.getLocalModelStatus).toHaveBeenCalledTimes(2));
    });
});

describe('Can read and write', () => {
    it('lists folders first, with where each came from', async () => {
        render(<EnvironmentSection sessionId={SID} busy={false} />);
        const list = await screen.findByRole('list');
        const rows = within(list).getAllByRole('listitem');
        expect(rows[0]).toHaveTextContent('project');
        expect(rows[0]).toHaveTextContent('Added by you');
        expect(rows[1]).toHaveTextContent("GAIA's documents folder");
        expect(rows[2]).toHaveTextContent('Document in this chat');
    });

    it('removes a folder through the backend and shows what it answered', async () => {
        mocked.removeEnvironmentFolder.mockResolvedValue({
            ...ENV,
            folders: ENV.folders.slice(1),
            removed: ['C:\\Users\\me\\project'],
        });
        render(<EnvironmentSection sessionId={SID} busy={false} />);
        fireEvent.click(await screen.findByRole('button', { name: 'Remove C:\\Users\\me\\project from this chat' }));
        await waitFor(() => expect(mocked.removeEnvironmentFolder).toHaveBeenCalledWith(SID, 'C:\\Users\\me\\project'));
        expect(await screen.findByText(/Removed: project\. GAIA is refused there/)).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /Remove C:\\Users\\me\\project from/ })).not.toBeInTheDocument();
    });

    it('adds a folder picked by browsing', async () => {
        mocked.browseFiles.mockImplementation(async (path?: string) => ({
            current_path: path ?? 'C:\\Users\\me',
            parent_path: path ? 'C:\\Users\\me' : null,
            entries: [{ name: 'notes', path: 'C:\\Users\\me\\notes', type: 'folder', size: 0, extension: null, modified: null }],
            quick_links: [],
        }));
        mocked.addEnvironmentFolder.mockResolvedValue(ENV);
        render(<EnvironmentSection sessionId={SID} busy={false} />);
        fireEvent.click(await screen.findByRole('button', { name: /Add folder/ }));
        const dialog = await screen.findByRole('dialog', { name: 'Add a folder to this chat' });
        fireEvent.click(await within(dialog).findByRole('button', { name: /notes/ }));
        await waitFor(() => expect(within(dialog).getByLabelText('Folder path')).toHaveValue('C:\\Users\\me\\notes'));
        fireEvent.click(within(dialog).getByRole('button', { name: 'Use this folder' }));
        await waitFor(() => expect(mocked.addEnvironmentFolder).toHaveBeenCalledWith(SID, 'C:\\Users\\me\\notes'));
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    it("says why a folder can't be added", async () => {
        mocked.browseFiles.mockResolvedValue({ current_path: 'C:\\Users\\me', parent_path: null, entries: [], quick_links: [] });
        mocked.addEnvironmentFolder.mockRejectedValue(
            new Error("GAIA can't be given C:\\Users\\me: it is your home directory."),
        );
        render(<EnvironmentSection sessionId={SID} busy={false} />);
        fireEvent.click(await screen.findByRole('button', { name: /Add folder/ }));
        fireEvent.click(await screen.findByRole('button', { name: 'Use this folder' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('it is your home directory');
    });

    it("says a separate agent isn't held to these settings, and offers no controls", async () => {
        mocked.getEnvironment.mockResolvedValue({ ...ENV, enforced: false });
        useChatStore.setState({ sessions: [{ ...useChatStore.getState().sessions[0], agent_type: 'gaia' }] });
        render(<EnvironmentSection sessionId={SID} busy={false} />);
        expect(await screen.findByText(/runs as its own process/)).toBeInTheDocument();
        // Not a boundary: say so rather than listing folders as if they were the limit.
        expect(screen.getByText('Files GAIA can reach')).toBeInTheDocument();
        expect(screen.getByText(/rest of your user folder/)).toBeInTheDocument();
        expect(screen.queryByText('Can read and write')).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /Add folder/ })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /^Remove / })).not.toBeInTheDocument();
        expect(screen.queryByRole('switch', { name: 'Internet for this chat' })).not.toBeInTheDocument();
        expect(screen.getByText('On')).toBeInTheDocument();
        expect(screen.getAllByText(/can't be changed from/)).toHaveLength(1);
    });

    it("doesn't claim another agent's scope", async () => {
        mocked.getEnvironment.mockResolvedValue({ ...ENV, enforced: false });
        useChatStore.setState({ sessions: [{ ...useChatStore.getState().sessions[0], agent_type: 'email' }] });
        render(<EnvironmentSection sessionId={SID} busy={false} />);
        expect(await screen.findByText(/applies its own limits/)).toBeInTheDocument();
        expect(screen.queryByText(/rest of your user folder/)).not.toBeInTheDocument();
    });

    it('offers no Remove for an attached document; it leaves through the document bar', async () => {
        render(<EnvironmentSection sessionId={SID} busy={false} />);
        await screen.findByText('report.pdf');
        expect(screen.queryByRole('button', { name: /report\.pdf/ })).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Remove C:\\Users\\me\\project from this chat' })).toBeInTheDocument();
    });
});

describe('Internet', () => {
    it('turns off for the chat and the composer says so, with a way back on', async () => {
        mocked.setInternet.mockImplementation(async (_sid, on) => ({ ...ENV, internet: on }));
        render(
            <>
                <EnvironmentSection sessionId={SID} busy={false} />
                <OfflineNotice sessionId={SID} />
            </>,
        );
        const toggle = await screen.findByRole('switch', { name: 'Internet for this chat' });
        expect(toggle).toHaveAttribute('aria-checked', 'true');
        expect(screen.queryByText(/Offline: internet is off/)).not.toBeInTheDocument();

        fireEvent.click(toggle);
        await waitFor(() => expect(mocked.setInternet).toHaveBeenCalledWith(SID, false));
        expect(await screen.findByText(/Offline: internet is off for this chat/)).toBeInTheDocument();
        expect(toggle).toHaveAttribute('aria-checked', 'false');
        expect(screen.getByText('Off: web search and pages are blocked')).toBeInTheDocument();

        fireEvent.click(screen.getByRole('button', { name: 'Turn on' }));
        await waitFor(() => expect(mocked.setInternet).toHaveBeenLastCalledWith(SID, true));
        await waitFor(() => expect(screen.queryByText(/Offline: internet is off/)).not.toBeInTheDocument());
    });

    it("keeps the user's change when an earlier read answers after it", async () => {
        let answerLateRead: (env: typeof ENV) => void = () => {};
        mocked.getEnvironment.mockReturnValueOnce(new Promise((resolve) => { answerLateRead = resolve; }));
        mocked.setInternet.mockResolvedValue({ ...ENV, internet: false });
        render(<EnvironmentSection sessionId={SID} busy={false} />);
        await waitFor(() => expect(mocked.getEnvironment).toHaveBeenCalled());
        // The first read is still out; meanwhile the user's change lands via another reader.
        const { useSessionEnvironment } = await import('../../../hooks/useSessionEnvironment');
        let change: ReturnType<typeof useSessionEnvironment>['change'] | null = null;
        function Grab() { change = useSessionEnvironment(SID).change; return null; }
        render(<Grab />);
        await act(async () => { await change!(() => mocked.setInternet(SID, false)); });
        await act(async () => { answerLateRead(ENV); });
        expect(screen.getByRole('switch', { name: 'Internet for this chat' })).toHaveAttribute('aria-checked', 'false');
    });

    it('shows the offline notice for a chat saved with internet off', async () => {
        mocked.getEnvironment.mockResolvedValue({ ...ENV, internet: false });
        render(<OfflineNotice sessionId={SID} />);
        expect(await screen.findByRole('status')).toHaveTextContent("GAIA won't search the web or open pages");
    });
});

describe('Phone access', () => {
    it('asks the app to start it, then shows the connected device', async () => {
        const requests: boolean[] = [];
        const onRequest = (e: Event) => requests.push((e as CustomEvent<boolean>).detail);
        window.addEventListener('gaia:mobile-access-request', onRequest);
        render(<EnvironmentSection sessionId={SID} busy={false} />);
        const toggle = await screen.findByRole('switch', { name: 'Phone access' });
        fireEvent.click(toggle);
        expect(requests).toEqual([true]);
        expect(screen.getByText('Starting…')).toBeInTheDocument();

        mocked.getTunnelStatus.mockResolvedValue({
            active: true, url: 'https://x', token: 't', startedAt: null, error: null, publicIp: null,
            device: { name: 'iPhone', lastSeen: '2026-10-16T20:00:00+00:00' },
        });
        act(() => announceMobileAccess(true));
        expect(await screen.findByText(/iPhone connected/)).toBeInTheDocument();
        expect(toggle).toHaveAttribute('aria-checked', 'true');
        window.removeEventListener('gaia:mobile-access-request', onRequest);
    });
});

describe('Permission mode', () => {
    it('mirrors the composer chip both ways', async () => {
        mocked.setPermissionMode.mockImplementation(async (_sid, mode) => ({ session_id: SID, mode, grants: [] }));
        render(
            <>
                <PermissionModeChip sessionId={SID} />
                <EnvironmentSection sessionId={SID} busy={false} />
            </>,
        );
        const full = await screen.findByRole('radio', { name: /Full access/ });
        await waitFor(() => expect(screen.getByRole('radio', { name: /Ask/ })).toHaveAttribute('aria-checked', 'true'));

        // Full access needs a second, explicit step.
        fireEvent.click(full);
        expect(mocked.setPermissionMode).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: 'Turn it on' }));
        await waitFor(() => expect(mocked.setPermissionMode).toHaveBeenCalledWith(SID, 'full_access'));
        await waitFor(() => expect(screen.getByRole('button', { name: 'Permission mode: full access' })).toBeInTheDocument());

        // And the chip's change shows up in the panel.
        fireEvent.click(screen.getByRole('button', { name: 'Permission mode: full access' }));
        fireEvent.click(await screen.findByRole('button', { name: /Ask before acting/ }));
        await waitFor(() => expect(screen.getByRole('radio', { name: /Ask/ })).toHaveAttribute('aria-checked', 'true'));
    });
});
