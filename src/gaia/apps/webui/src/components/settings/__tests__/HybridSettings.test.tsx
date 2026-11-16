// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Settings > Model > Hybrid: models, policy, custom rules, the try-it box, and usage. */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { HybridSettings } from '../HybridSettings';
import * as api from '../../../services/api';
import type { HybridRules, HybridSettings as Settings, HybridStats } from '../../../types';

vi.mock('../../../services/api');

const mockedApi = vi.mocked(api);

const QUALITY: HybridRules = {
    write_prose: { cheap_max_difficulty: 3, allow_high_stakes: false },
    answer_question: { cheap_max_difficulty: 3, allow_high_stakes: false },
    analyze_data: { cheap_max_difficulty: 3, allow_high_stakes: false },
    operate_tools: { cheap_max_difficulty: 3, allow_high_stakes: false },
    change_code: { cheap_max_difficulty: 2, allow_high_stakes: false },
    find_defects: { cheap_max_difficulty: 0, allow_high_stakes: false },
    plan_or_design: { cheap_max_difficulty: 0, allow_high_stakes: false },
    other: { cheap_max_difficulty: 0, allow_high_stakes: false },
};
const BALANCED: HybridRules = { ...QUALITY, change_code: { cheap_max_difficulty: 3, allow_high_stakes: true } };

const SETTINGS: Settings = {
    cheap_model: 'amd.DeepSeek-V4.1-Flash',
    strong_model: 'amd.Claude-Opus-5-5',
    classifier_model: null,
    classifier_timeout: 15,
    policy: 'quality',
    rules: QUALITY,
    presets: { quality: QUALITY, balanced: BALANCED },
    kinds: ['write_prose', 'answer_question', 'analyze_data', 'operate_tools', 'change_code', 'find_defects', 'plan_or_design', 'other'],
    models: [
        { id: 'amd.DeepSeek-V4.1-Flash', display_name: 'DeepSeek V4.1 Flash', input_per_million: 0.3, output_per_million: 1.2, cache_read_per_million: 0.03, available: true },
        { id: 'amd.Claude-Opus-5-5', display_name: 'Claude Opus 5.5', input_per_million: 5, output_per_million: 25, cache_read_per_million: 0.5, available: true },
        { id: 'amd.Qwen-Coder', display_name: 'Qwen Coder', input_per_million: null, output_per_million: null, cache_read_per_million: null, available: true },
        { id: 'amd.Retired', display_name: 'Retired model', input_per_million: 1, output_per_million: 2, cache_read_per_million: null, available: false },
    ],
    is_active: true,
};

const STATS: HybridStats = {
    since: '2026-11-01T00:00:00Z', turns: 40, cheap_turns: 30, strong_turns: 10, failed_closed: 2,
    cost: 1.5, cost_if_all_strong: 6, saving_pct: 75,
};

/** A 422 as apiFetch throws it: the backend's sentence, and the fields its `loc` names. */
function rejection(message: string, fields: string[] = []): Error {
    return Object.assign(new Error(message), { httpStatus: 422, detail: message, fields });
}

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.getHybridSettings.mockResolvedValue(SETTINGS);
    mockedApi.getHybridStats.mockResolvedValue(STATS);
    mockedApi.updateHybridSettings.mockImplementation(async (update) => ({ ...SETTINGS, ...update }));
});

async function renderPanel() {
    render(<HybridSettings />);
    return screen.findByRole('form', { name: 'Hybrid' });
}

describe('HybridSettings: load', () => {
    it('shows the models with gateway prices, the policy, and the fail-closed note', async () => {
        const form = await renderPanel();
        const cheap = within(form).getByLabelText('Cheap model') as HTMLSelectElement;
        expect(cheap.value).toBe('amd.DeepSeek-V4.1-Flash');
        expect(within(cheap).getByRole('option', { name: 'DeepSeek V4.1 Flash · $0.30 in · $1.20 out per 1M tokens' })).toBeInTheDocument();
        expect(within(cheap).getByRole('option', { name: 'Qwen Coder · price unknown' })).toBeInTheDocument();
        expect(within(cheap).getByRole('option', { name: 'Retired model (not available)' })).toBeDisabled();
        expect(within(form).getByLabelText('Classifier model')).toHaveValue('');
        expect(within(form).getByRole('option', { name: 'Same as the cheap model' })).toBeInTheDocument();
        expect(within(form).getByLabelText('Classifier time limit (seconds)')).toHaveValue(15);
        expect(within(form).getByRole('radio', { name: 'Quality' })).toHaveAttribute('aria-checked', 'true');
        expect(within(form).queryByRole('table')).not.toBeInTheDocument();
        expect(within(form).getByText('If the classifier can’t decide, the strong model runs.')).toBeInTheDocument();
        expect(screen.getByText('New chats use Hybrid.')).toBeInTheDocument();
        expect(within(form).getByRole('button', { name: 'Save' })).toBeDisabled();
    });

    it('says why the settings could not be read, and reads them again on request', async () => {
        mockedApi.getHybridSettings.mockRejectedValueOnce(new Error('hybrid_policy is "fast", which is not a policy. Set it to quality.'));
        render(<HybridSettings />);
        expect(await screen.findByRole('alert')).toHaveTextContent('hybrid_policy is "fast"');
        fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
        expect(await screen.findByRole('form', { name: 'Hybrid' })).toBeInTheDocument();
        expect(mockedApi.getHybridSettings).toHaveBeenCalledTimes(2);
    });
});

describe('HybridSettings: save', () => {
    it('sends only the fields that changed', async () => {
        const onSaved = vi.fn();
        render(<HybridSettings onSaved={onSaved} />);
        const form = await screen.findByRole('form', { name: 'Hybrid' });
        fireEvent.change(within(form).getByLabelText('Strong model'), { target: { value: 'amd.Qwen-Coder' } });
        fireEvent.change(within(form).getByLabelText('Classifier time limit (seconds)'), { target: { value: '30' } });
        fireEvent.click(within(form).getByRole('radio', { name: 'Balanced' }));
        fireEvent.click(within(form).getByRole('button', { name: 'Save' }));

        await waitFor(() => expect(mockedApi.updateHybridSettings).toHaveBeenCalledWith({
            strong_model: 'amd.Qwen-Coder', classifier_timeout: 30, policy: 'balanced',
        }));
        expect(await screen.findByText('Hybrid settings saved.')).toBeInTheDocument();
        expect(onSaved).toHaveBeenCalledTimes(1);
        expect(within(form).getByRole('button', { name: 'Save' })).toBeDisabled();
    });

    it('blocks a time limit out of range before sending it', async () => {
        const form = await renderPanel();
        const timeout = within(form).getByLabelText('Classifier time limit (seconds)');
        fireEvent.change(timeout, { target: { value: '0' } });
        expect(timeout).toHaveAttribute('aria-invalid', 'true');
        expect(within(form).getByText('Enter a number of seconds from 1 to 120.')).toBeInTheDocument();
        expect(within(form).getByRole('button', { name: 'Save' })).toBeDisabled();
        fireEvent.submit(form);
        expect(mockedApi.updateHybridSettings).not.toHaveBeenCalled();
    });

    it('shows the backend’s 422 next to the field it names', async () => {
        mockedApi.updateHybridSettings.mockRejectedValueOnce(rejection(
            'hybrid_strong_model is amd.Qwen-Coder, which the gateway does not deploy. Pick one from the list.',
        ));
        const form = await renderPanel();
        const strong = within(form).getByLabelText('Strong model');
        fireEvent.change(strong, { target: { value: 'amd.Qwen-Coder' } });
        fireEvent.click(within(form).getByRole('button', { name: 'Save' }));

        await waitFor(() => expect(strong).toHaveAttribute('aria-invalid', 'true'));
        const error = document.getElementById(strong.getAttribute('aria-describedby')!);
        expect(error).toHaveTextContent('which the gateway does not deploy');
        expect(screen.queryByText('Hybrid settings saved.')).not.toBeInTheDocument();
    });

    it('uses the 422’s field list when it has one, and shows other errors under Save', async () => {
        mockedApi.updateHybridSettings.mockRejectedValueOnce(rejection('Input should be less than or equal to 120', ['body', 'classifier_timeout']));
        const form = await renderPanel();
        const timeout = within(form).getByLabelText('Classifier time limit (seconds)');
        fireEvent.change(timeout, { target: { value: '20' } });
        fireEvent.click(within(form).getByRole('button', { name: 'Save' }));
        await waitFor(() => expect(timeout).toHaveAttribute('aria-invalid', 'true'));
        expect(within(form).getByText('Input should be less than or equal to 120')).toBeInTheDocument();

        mockedApi.updateHybridSettings.mockRejectedValueOnce(new Error('Service unavailable. Is the backend running?'));
        fireEvent.change(timeout, { target: { value: '25' } });
        fireEvent.click(within(form).getByRole('button', { name: 'Save' }));
        expect(await within(form).findByText('Service unavailable. Is the backend running?')).toHaveAttribute('role', 'alert');
        expect(timeout).toHaveAttribute('aria-invalid', 'false');
    });
});

describe('HybridSettings: custom rules', () => {
    it('lists a row per kind with human labels, starting from the rules in effect', async () => {
        const form = await renderPanel();
        fireEvent.click(within(form).getByRole('radio', { name: 'Custom' }));
        const table = within(form).getByRole('table', { name: 'Rules' });
        const rows = within(table).getAllByRole('row').slice(1);
        expect(rows.map((r) => within(r).getByRole('rowheader').textContent)).toEqual([
            'Writing', 'Questions', 'Data analysis', 'Tool tasks', 'Code changes', 'Finding defects', 'Planning & design', 'Other',
        ]);
        expect(within(table).getByLabelText('Code changes: cheap model up to difficulty')).toHaveValue('2');
        expect(within(table).getByLabelText('Finding defects: cheap model up to difficulty')).toHaveValue('0');
        expect(within(within(table).getByLabelText('Finding defects: cheap model up to difficulty')).getByRole('option', { name: 'Never' })).toBeInTheDocument();
        // A kind that never runs cheap has no stakes to allow.
        expect(within(table).getByRole('switch', { name: 'Finding defects: allow high-stakes requests on the cheap model' })).toBeDisabled();
    });

    it('saves the edited table with the custom policy', async () => {
        const form = await renderPanel();
        fireEvent.click(within(form).getByRole('radio', { name: 'Custom' }));
        fireEvent.change(within(form).getByLabelText('Code changes: cheap model up to difficulty'), { target: { value: '4' } });
        fireEvent.click(within(form).getByRole('switch', { name: 'Code changes: allow high-stakes requests on the cheap model' }));
        fireEvent.change(within(form).getByLabelText('Other: cheap model up to difficulty'), { target: { value: '1' } });
        fireEvent.click(within(form).getByRole('button', { name: 'Save' }));

        await waitFor(() => expect(mockedApi.updateHybridSettings).toHaveBeenCalledWith({
            policy: 'custom',
            rules: {
                ...QUALITY,
                change_code: { cheap_max_difficulty: 4, allow_high_stakes: true },
                other: { cheap_max_difficulty: 1, allow_high_stakes: false },
            },
        }));
    });

    it('copies a preset into the table', async () => {
        const form = await renderPanel();
        fireEvent.click(within(form).getByRole('radio', { name: 'Custom' }));
        fireEvent.click(within(form).getByRole('button', { name: 'Start from Balanced' }));
        expect(within(form).getByLabelText('Code changes: cheap model up to difficulty')).toHaveValue('3');
        expect(within(form).getByRole('switch', { name: 'Code changes: allow high-stakes requests on the cheap model' })).toBeChecked();

        fireEvent.click(within(form).getByRole('button', { name: 'Start from Quality' }));
        expect(within(form).getByLabelText('Code changes: cheap model up to difficulty')).toHaveValue('2');
        expect(within(form).getByRole('switch', { name: 'Code changes: allow high-stakes requests on the cheap model' })).not.toBeChecked();
    });

    it('shows a rules error from the backend under the table', async () => {
        mockedApi.updateHybridSettings.mockRejectedValueOnce(rejection('hybrid_rules is missing "other". All 8 kinds are required.'));
        const form = await renderPanel();
        fireEvent.click(within(form).getByRole('radio', { name: 'Custom' }));
        fireEvent.click(within(form).getByRole('button', { name: 'Save' }));
        const table = within(form).getByRole('table', { name: 'Rules' });
        await waitFor(() => expect(table).toHaveAttribute('aria-describedby'));
        expect(document.getElementById(table.getAttribute('aria-describedby')!)).toHaveTextContent('All 8 kinds are required.');
    });
});

describe('HybridSettings: try a request', () => {
    it('shows the model, kind, difficulty, stakes, reason and time', async () => {
        mockedApi.previewHybridRoute.mockResolvedValue({
            model: 'amd.DeepSeek-V4.1-Flash', kind: 'change_code', difficulty: 2, stakes: 'low',
            reason: 'one-line fix with the cause stated', classified: true, seconds: 1.234, policy: 'quality',
        });
        render(<HybridSettings />);
        await screen.findByRole('form', { name: 'Hybrid' });
        expect(screen.getByRole('button', { name: 'Show the route' })).toBeDisabled();
        fireEvent.change(screen.getByLabelText('Try a request'), { target: { value: 'Fix the typo in README' } });
        fireEvent.click(screen.getByRole('button', { name: 'Show the route' }));

        const result = await screen.findByRole('status', { name: 'Route for this request' });
        expect(mockedApi.previewHybridRoute).toHaveBeenCalledWith('Fix the typo in README');
        const text = result.textContent;
        expect(text).toContain('DeepSeek V4.1 Flash');
        expect(text).toContain('Code changes');
        expect(text).toContain('Low');
        expect(text).toContain('Picked by hybrid. one-line fix with the cause stated');
        expect(text).toContain('1.2 s');
        expect(within(result).getByText('Difficulty').nextElementSibling).toHaveTextContent('2');
    });

    it('says when the classifier failed and the strong model was picked', async () => {
        mockedApi.previewHybridRoute.mockResolvedValue({
            model: 'amd.Claude-Opus-5-5', kind: null, difficulty: null, stakes: null,
            reason: 'the classifier took longer than 15 s', classified: false, seconds: 15.02, policy: 'quality',
        });
        render(<HybridSettings />);
        await screen.findByRole('form', { name: 'Hybrid' });
        fireEvent.change(screen.getByLabelText('Try a request'), { target: { value: 'Review this PR' } });
        fireEvent.keyDown(screen.getByLabelText('Try a request'), { key: 'Enter', ctrlKey: true });

        const result = await screen.findByRole('status', { name: 'Route for this request' });
        expect(result).toHaveTextContent('The classifier couldn’t decide, so the strong model runs.');
        expect(result).toHaveTextContent('Claude Opus 5.5');
        expect(result).toHaveTextContent('Picked by hybrid. the classifier took longer than 15 s');
    });

    it('shows the error when the preview fails', async () => {
        mockedApi.previewHybridRoute.mockRejectedValue(new Error('The AMD gateway is not set up. Connect it in Settings > Model.'));
        render(<HybridSettings />);
        await screen.findByRole('form', { name: 'Hybrid' });
        fireEvent.change(screen.getByLabelText('Try a request'), { target: { value: 'hello' } });
        fireEvent.click(screen.getByRole('button', { name: 'Show the route' }));
        expect(await screen.findByText('The AMD gateway is not set up. Connect it in Settings > Model.')).toHaveAttribute('role', 'alert');
    });

    it('notes that unsaved changes are not what the preview uses', async () => {
        const form = await renderPanel();
        expect(screen.queryByText('Uses the saved settings; save to try your changes.')).not.toBeInTheDocument();
        fireEvent.click(within(form).getByRole('radio', { name: 'Balanced' }));
        expect(screen.getByText('Uses the saved settings; save to try your changes.')).toBeInTheDocument();
    });
});

describe('HybridSettings: usage', () => {
    it('shows cheap and strong turns, failed-closed turns, and the saving', async () => {
        render(<HybridSettings />);
        const usage = await screen.findByRole('region', { name: 'Usage' });
        await waitFor(() => expect(usage).toHaveTextContent('40 · cheap model: 30 · strong model: 10.'));
        expect(usage).toHaveTextContent('Sent to the strong model because the classifier couldn’t decide: 2.');
        expect(usage).toHaveTextContent('Cost $1.50, against $6.00 if every turn had run on the strong model (75% saved).');
    });

    it('says so when nothing has been routed yet', async () => {
        mockedApi.getHybridStats.mockResolvedValue({ ...STATS, turns: 0, cheap_turns: 0, strong_turns: 0, failed_closed: 0, cost: 0, cost_if_all_strong: 0, saving_pct: null });
        render(<HybridSettings />);
        expect(await screen.findByText('No hybrid turns yet.')).toBeInTheDocument();
    });

    it('shows a stats error without hiding the settings', async () => {
        mockedApi.getHybridStats.mockRejectedValue(new Error('The spending database could not be read.'));
        render(<HybridSettings />);
        expect(await screen.findByText('The spending database could not be read.')).toHaveAttribute('role', 'alert');
        expect(await screen.findByRole('form', { name: 'Hybrid' })).toBeInTheDocument();
    });
});
