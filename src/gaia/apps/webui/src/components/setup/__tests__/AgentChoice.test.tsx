// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../../services/api';
import { useChatStore } from '../../../stores/chatStore';
import { AGENT_PROGRAMS, SETTINGS, withoutGaiacpp } from '../../../test/setupApiMocks';
import type { AgentDownload } from '../../../types';
import { AgentChoice } from '../AgentChoice';

vi.mock('../../../services/api', async (importOriginal) => {
    const real = await importOriginal<typeof api>();
    return { ...real, getAgentPrograms: vi.fn(), updateSettings: vi.fn(), startAgentDownload: vi.fn(), getAgentDownload: vi.fn() };
});

const mockedApi = vi.mocked(api);

const radio = (name: RegExp) => screen.findByRole('radio', { name });
const job = (over: Partial<AgentDownload>): AgentDownload => ({ state: 'downloading', bytes_done: 0, bytes_total: 6_800_000, error: null, ...over });

beforeEach(() => {
    vi.clearAllMocks();
    useChatStore.setState({ locale: 'en' });
    mockedApi.getAgentPrograms.mockResolvedValue(AGENT_PROGRAMS);
    mockedApi.updateSettings.mockImplementation(async (body) => ({ ...SETTINGS, ...body }));
});

afterEach(() => {
    vi.useRealTimers();
});

describe('AgentChoice', () => {
    it('shows each agent as one short row with its size, the comparison behind a toggle', async () => {
        render(<AgentChoice />);
        const py = await radio(/gaiapy/);
        const cpp = await radio(/gaiacpp/);
        expect(py).toHaveAttribute('aria-checked', 'true');
        expect(cpp).toHaveAttribute('aria-checked', 'false');
        expect(py).toHaveTextContent(/^Full-featuredgaiapy412 MBRecommended$/);
        expect(cpp).toHaveTextContent(/^Fast and lightgaiacpp7 MBBeta$/);
        // The friendly name leads; the id is the quiet second label docs and the CLI use.
        expect(cpp.querySelector('.agent-choice-what')?.firstElementChild).toHaveTextContent('Fast and light');
        expect(cpp.querySelector('.agent-choice-name')).toHaveTextContent('gaiacpp');

        const more = screen.getByRole('button', { name: 'Compare the two' });
        expect(more).toHaveAttribute('aria-expanded', 'false');
        expect(screen.queryByText(/most tested/)).not.toBeVisible();
        expect(screen.queryByText('You can switch later in Settings › Agent & model.')).not.toBeVisible();

        fireEvent.click(more);
        expect(more).toHaveAttribute('aria-expanded', 'true');
        expect(screen.getByText(/Full-featured \(gaiapy\) has every tool and feature, and is the most tested/)).toBeVisible();
        expect(screen.getByText(/Fast and light \(gaiacpp\) starts in a fraction of a second/)).toBeVisible();
        expect(screen.getByText('You can switch later in Settings › Agent & model.')).toBeVisible();
    });

    it('selects nothing for a saved agent this version cannot run, and lets the user pick one', async () => {
        mockedApi.getAgentPrograms.mockResolvedValue({ ...AGENT_PROGRAMS, current: 'gaiacode' });
        render(<AgentChoice inSettings />);
        const py = await radio(/gaiapy/);
        expect(py).toHaveAttribute('aria-checked', 'false');
        expect(await radio(/gaiacpp/)).toHaveAttribute('aria-checked', 'false');

        fireEvent.click(py);
        await waitFor(() => expect(mockedApi.updateSettings).toHaveBeenCalledWith({ flagship_agent: 'gaiapy' }));
        await waitFor(() => expect(py).toHaveAttribute('aria-checked', 'true'));
    });

    it('shows no size it could not read', async () => {
        mockedApi.getAgentPrograms.mockResolvedValue({ ...AGENT_PROGRAMS, programs: AGENT_PROGRAMS.programs.map((p) => ({ ...p, size_bytes: null })) });
        render(<AgentChoice />);
        expect(await radio(/gaiapy/)).toHaveTextContent(/^Full-featuredgaiapyRecommended$/);
        expect(document.querySelector('.agent-choice-size')).toBeNull();
    });

    it('saves the agent picked', async () => {
        render(<AgentChoice inSettings />);
        fireEvent.click(await radio(/gaiacpp/));
        expect(mockedApi.updateSettings).toHaveBeenCalledWith({ flagship_agent: 'gaiacpp' });
        await waitFor(() => expect(screen.getByRole('radio', { name: /gaiacpp/ })).toHaveAttribute('aria-checked', 'true'));
        expect(screen.queryByRole('alert')).toBeNull();
    });

    it('keys like radios: an arrow moves to the other agent and picks it', async () => {
        render(<AgentChoice inSettings />);
        const py = await radio(/gaiapy/);
        py.focus();
        fireEvent.keyDown(py, { key: 'ArrowDown' });
        expect(mockedApi.updateSettings).toHaveBeenCalledWith({ flagship_agent: 'gaiacpp' });
    });

    it.each([true, false])("can't pick gaiacpp where it can't be had, and says why in view under its row (inSettings %s)", async (inSettings) => {
        mockedApi.getAgentPrograms.mockResolvedValue(withoutGaiacpp('This is a development build; gaiacpp comes with releases.'));
        render(<AgentChoice inSettings={inSettings} onPending={vi.fn()} />);
        const cpp = await radio(/gaiacpp/);
        expect(cpp).toHaveAttribute('aria-disabled', 'true');
        expect(within(cpp).getByText('Not installed')).toBeVisible();
        const why = "It isn't on this PC and can't be downloaded here: This is a development build; gaiacpp comes with releases.";
        expect(cpp).toHaveAccessibleDescription(why);
        // Read without opening anything: the comparison stays shut, and the reason is said once.
        expect(screen.getByRole('button', { name: 'Compare the two' })).toHaveAttribute('aria-expanded', 'false');
        expect(screen.getAllByText(why)).toHaveLength(1);
        expect(screen.getByText(why)).toBeVisible();
        expect(screen.getByText(why).closest('[hidden]')).toBeNull();
        expect(cpp.nextElementSibling).toBe(screen.getByText(why));
        fireEvent.click(cpp);
        const py = screen.getByRole('radio', { name: /gaiapy/ });
        py.focus();
        fireEvent.keyDown(py, { key: 'ArrowDown' });
        expect(mockedApi.updateSettings).not.toHaveBeenCalled();
        expect(mockedApi.startAgentDownload).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: 'Compare the two' }));
        expect(screen.getAllByText(why)).toHaveLength(1);
    });

    it.each([
        ['no_release', 'this GAIA release has no gaiacpp to download', 'لا يتضمّن gaiacpp للتنزيل'],
        ['unreachable', 'Check your internet connection, or try again later', 'تحقّق من اتصالك بالإنترنت'],
        ['unverifiable', "can't be verified, so GAIA won't install it", 'تعذّر التحقّق من gaiacpp'],
    ] as const)('says a reason the server names by code (%s) in the language in use', async (code, en, ar) => {
        const listing = withoutGaiacpp('english from the server');
        mockedApi.getAgentPrograms.mockResolvedValue({
            ...listing,
            programs: listing.programs.map((p) => (p.id === 'gaiacpp' ? { ...p, unavailable_code: code } : p)),
        });
        const { unmount } = render(<AgentChoice inSettings />);
        await radio(/gaiacpp/);
        expect(document.querySelector('.agent-choice-why')).toHaveTextContent(en);
        unmount();

        useChatStore.setState({ locale: 'ar' });
        render(<AgentChoice inSettings />);
        await radio(/gaiacpp/);
        const why = document.querySelector('.agent-choice-why');
        expect(why).toHaveTextContent(ar);
        expect(why).not.toHaveTextContent('english from the server');
    });

    it("shows the server's refusal and puts the choice back", async () => {
        mockedApi.updateSettings.mockRejectedValue(new api.ApiError('gaiacpp is not installed on this PC.', 422, 'gaiacpp is not installed on this PC.', null));
        render(<AgentChoice inSettings />);
        fireEvent.click(await radio(/gaiacpp/));
        expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't switch the agent: gaiacpp is not installed on this PC.");
        await waitFor(() => expect(screen.getByRole('radio', { name: /gaiapy/ })).toHaveAttribute('aria-checked', 'true'));
        expect(screen.getByRole('button', { name: 'Compare the two' })).toHaveAttribute('aria-expanded', 'false');
    });

    it('after a failure that may have saved, shows what the server holds', async () => {
        mockedApi.updateSettings.mockRejectedValue(new api.ApiError('Saved, but the running agent could not be stopped.', 500, 'Saved, but the running agent could not be stopped.', null));
        render(<AgentChoice inSettings />);
        fireEvent.click(await radio(/gaiacpp/));
        mockedApi.getAgentPrograms.mockResolvedValue({ ...AGENT_PROGRAMS, current: 'gaiacpp' });
        expect(await screen.findByRole('alert')).toHaveTextContent('the running agent could not be stopped');
        await waitFor(() => expect(mockedApi.getAgentPrograms).toHaveBeenCalledTimes(2));
        expect(screen.getByRole('radio', { name: /gaiacpp/ })).toHaveAttribute('aria-checked', 'true');
    });

    it('says when it could not read the agents, instead of guessing', async () => {
        mockedApi.getAgentPrograms.mockRejectedValue(new Error('Service unavailable.'));
        render(<AgentChoice />);
        expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't read which agent is in use: Service unavailable.");
        expect(screen.queryByRole('radio')).toBeNull();
    });

    it('keeps gaiapy selectable even when the listing says it is not installed', async () => {
        mockedApi.getAgentPrograms.mockResolvedValue({
            ...AGENT_PROGRAMS,
            current: 'gaiacpp',
            programs: AGENT_PROGRAMS.programs.map((p) => (p.id === 'gaiapy' ? { ...p, installed: false } : p)),
        });
        render(<AgentChoice inSettings />);
        const py = await radio(/gaiapy/);
        expect(py).not.toHaveAttribute('aria-disabled');
        expect(screen.queryByRole('button', { name: /Download/ })).toBeNull();
        fireEvent.click(py);
        expect(mockedApi.updateSettings).toHaveBeenCalledWith({ flagship_agent: 'gaiapy' });
        expect(mockedApi.startAgentDownload).not.toHaveBeenCalled();
    });

    it('a busy agent (409) says to finish the answer first, and keeps the current one', async () => {
        const busy = 'GAIA is answering in another chat. Finish or stop the answer first, then switch.';
        mockedApi.updateSettings.mockRejectedValue(new api.ApiError(busy, 409, busy, null));
        render(<AgentChoice inSettings />);
        fireEvent.click(await radio(/gaiacpp/));
        expect(await screen.findByRole('alert')).toHaveTextContent(`Couldn't switch the agent: ${busy}`);
        await waitFor(() => expect(screen.getByRole('radio', { name: /gaiapy/ })).toHaveAttribute('aria-checked', 'true'));
        expect(mockedApi.getAgentPrograms).toHaveBeenCalledTimes(1);
    });

    it('waits while setup is busy', async () => {
        const onPending = vi.fn();
        render(<AgentChoice disabled onPending={onPending} />);
        fireEvent.click(await radio(/gaiacpp/));
        expect(onPending).not.toHaveBeenCalled();
        expect(mockedApi.updateSettings).not.toHaveBeenCalled();
    });
});

describe('AgentChoice in Settings: downloading gaiacpp', () => {
    beforeEach(() => {
        mockedApi.getAgentPrograms.mockResolvedValue(withoutGaiacpp());
    });

    const download = () => screen.findByRole('button', { name: 'Download gaiacpp' });

    it('selecting never downloads: the row waits, and arrow keys skip it', async () => {
        render(<AgentChoice inSettings />);
        const cpp = await radio(/gaiacpp/);
        expect(cpp).toHaveAttribute('aria-disabled', 'true');
        expect(cpp).toHaveAccessibleDescription("Download gets it first; GAIA switches to it once it's in.");
        fireEvent.click(cpp);
        const py = screen.getByRole('radio', { name: /gaiapy/ });
        py.focus();
        fireEvent.keyDown(py, { key: 'ArrowDown' });
        fireEvent.keyDown(py, { key: 'End' });
        expect(mockedApi.startAgentDownload).not.toHaveBeenCalled();
        expect(mockedApi.updateSettings).not.toHaveBeenCalled();
        expect(py).toHaveAttribute('aria-checked', 'true');
        expect(await download()).toBeVisible();
    });

    it('Download downloads it with a bar, then switches to it', async () => {
        mockedApi.startAgentDownload.mockResolvedValue(job({ bytes_done: 2_720_000 }));
        mockedApi.getAgentDownload.mockResolvedValue(job({ state: 'done', bytes_done: 6_800_000 }));
        render(<AgentChoice inSettings />);
        fireEvent.click(await download());
        expect(mockedApi.startAgentDownload).toHaveBeenCalledTimes(1);
        expect(mockedApi.updateSettings).not.toHaveBeenCalled();

        const bar = await screen.findByRole('progressbar', { name: /gaiacpp/ });
        await waitFor(() => expect(bar).toHaveAttribute('aria-valuenow', '40'));
        expect(screen.getByText('3 MB / 7 MB')).toBeVisible();
        expect(screen.getByRole('radio', { name: /gaiapy/ })).toHaveAttribute('aria-checked', 'true');
        expect(screen.queryByRole('button', { name: 'Download gaiacpp' })).toBeNull();

        mockedApi.getAgentPrograms.mockResolvedValue({ ...AGENT_PROGRAMS, current: 'gaiapy' });
        await waitFor(() => expect(mockedApi.updateSettings).toHaveBeenCalledWith({ flagship_agent: 'gaiacpp' }), { timeout: 3000 });
        await waitFor(() => expect(screen.getByRole('radio', { name: /gaiacpp/ })).toHaveAttribute('aria-checked', 'true'));
        expect(screen.queryByRole('progressbar')).toBeNull();
        expect(screen.queryByRole('button', { name: 'Download gaiacpp' })).toBeNull();
    });

    it('a failed download says so in view, keeps the agent, and retries', async () => {
        mockedApi.startAgentDownload.mockResolvedValueOnce(job({ state: 'failed', error: 'Checksum mismatch for gaiacpp-win32-x64.' }));
        render(<AgentChoice inSettings />);
        fireEvent.click(await download());
        const alert = await screen.findByRole('alert');
        expect(alert).toHaveTextContent("gaiacpp didn't download.");
        expect(alert).toHaveTextContent('gaiapy stays the agent.');
        expect(alert).toBeVisible();
        expect(screen.getByRole('radio', { name: /gaiapy/ })).toHaveAttribute('aria-checked', 'true');
        expect(mockedApi.updateSettings).not.toHaveBeenCalled();
        expect(screen.queryByRole('button', { name: 'Download gaiacpp' })).toBeNull();

        mockedApi.startAgentDownload.mockResolvedValueOnce(job({ bytes_done: 1_000_000 }));
        mockedApi.getAgentDownload.mockResolvedValue(job({ bytes_done: 1_000_000 }));
        await act(async () => { fireEvent.click(within(alert).getByRole('button', { name: /Retry/ })); });
        expect(mockedApi.startAgentDownload).toHaveBeenCalledTimes(2);
        expect(await screen.findByRole('progressbar', { name: /gaiacpp/ })).toBeVisible();
    });

    it('a download the server forgot (it restarted) is a failure with Retry, not a vanished row', async () => {
        mockedApi.startAgentDownload.mockResolvedValue(job({ bytes_done: 2_720_000 }));
        mockedApi.getAgentDownload.mockResolvedValue({ state: 'idle', bytes_done: 0, bytes_total: null, error: null });
        render(<AgentChoice inSettings />);
        fireEvent.click(await download());
        const alert = await screen.findByRole('alert', {}, { timeout: 3000 });
        expect(alert).toHaveTextContent("gaiacpp didn't download.");
        expect(alert).toHaveTextContent('The download stopped because GAIA restarted.');
        expect(within(alert).getByRole('button', { name: /Retry/ })).toBeVisible();
        expect(mockedApi.updateSettings).not.toHaveBeenCalled();
    });

    it('follows a download already running, and does not switch for it', async () => {
        mockedApi.getAgentPrograms.mockResolvedValue({ ...withoutGaiacpp(), download: job({ bytes_done: 2_720_000 }) });
        mockedApi.getAgentDownload.mockResolvedValueOnce(job({ bytes_done: 2_720_000 })).mockResolvedValue(job({ state: 'done', bytes_done: 6_800_000 }));
        render(<AgentChoice inSettings />);
        expect(await screen.findByRole('progressbar', { name: /gaiacpp/ })).toHaveAttribute('aria-valuenow', '40');
        expect(mockedApi.startAgentDownload).not.toHaveBeenCalled();
        mockedApi.getAgentPrograms.mockResolvedValue(AGENT_PROGRAMS);
        await waitFor(() => expect(screen.queryByRole('progressbar')).toBeNull(), { timeout: 4000 });
        await waitFor(() => expect(screen.getByRole('radio', { name: /gaiacpp/ })).not.toHaveAttribute('aria-disabled'));
        expect(mockedApi.updateSettings).not.toHaveBeenCalled();
    });

    it('says, behind the toggle, what Download does', async () => {
        render(<AgentChoice inSettings />);
        await radio(/gaiacpp/);
        fireEvent.click(screen.getByRole('button', { name: 'Compare the two' }));
        expect(screen.getByText("Download gets it first; GAIA switches to it once it's in.")).toBeVisible();
        expect(screen.getByText('A switch applies from the next message in every chat.')).toBeVisible();
    });
});

describe('AgentChoice in setup: a pick is held for Continue', () => {
    it('gaiacpp not on this PC: held with "download", nothing downloads or saves here', async () => {
        mockedApi.getAgentPrograms.mockResolvedValue(withoutGaiacpp());
        const onPending = vi.fn();
        const { rerender } = render(<AgentChoice onPending={onPending} />);
        const cpp = await radio(/gaiacpp/);
        expect(cpp).not.toHaveAttribute('aria-disabled');
        expect(screen.queryByRole('button', { name: /Download/ })).toBeNull();
        fireEvent.click(cpp);
        expect(onPending).toHaveBeenCalledWith({ id: 'gaiacpp', download: true, current: 'gaiapy' });
        expect(mockedApi.startAgentDownload).not.toHaveBeenCalled();
        expect(mockedApi.updateSettings).not.toHaveBeenCalled();
        rerender(<AgentChoice pending="gaiacpp" onPending={onPending} />);
        expect(screen.getByRole('radio', { name: /gaiacpp/ })).toHaveAttribute('aria-checked', 'true');
        fireEvent.click(screen.getByRole('button', { name: 'Compare the two' }));
        expect(screen.getByText('If you pick it, it downloads first in the Download step; the models follow.')).toBeVisible();
    });

    it('an installed agent is held too, and picking the one in use clears the hold', async () => {
        const onPending = vi.fn();
        render(<AgentChoice onPending={onPending} />);
        fireEvent.click(await radio(/gaiacpp/));
        expect(onPending).toHaveBeenLastCalledWith({ id: 'gaiacpp', download: false, current: 'gaiapy' });
        fireEvent.click(screen.getByRole('radio', { name: /gaiapy/ }));
        expect(onPending).toHaveBeenLastCalledWith(null);
        expect(mockedApi.updateSettings).not.toHaveBeenCalled();
    });
});
