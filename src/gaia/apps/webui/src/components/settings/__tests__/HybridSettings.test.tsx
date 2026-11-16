// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Settings > Model > Hybrid: models, policy, custom rules, the try-it box, and usage. */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { HybridSettings } from '../HybridSettings';
import * as api from '../../../services/api';
import type { HybridModelOption, HybridRules, HybridSettings as Settings, HybridStats, HybridTemplate } from '../../../types';

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

const MEASURED_NOTE = 'This pairing hasn’t been measured yet, so how much it saves and how well it answers are unknown.';
const CLASSIFIER_NOTE = 'The classifier runs on this PC; a small local model may route less accurately than a cloud one.';

function opt(over: Partial<HybridModelOption> & Pick<HybridModelOption, 'id' | 'display_name' | 'provider'>): HybridModelOption {
    return {
        input_per_million: null, output_per_million: null, cache_read_per_million: null, price_source: null,
        available: true, reason: null, ...over,
    };
}

const SETTINGS: Settings = {
    cheap_model: 'amd.DeepSeek-V4.1-Flash',
    strong_model: 'amd.Claude-Opus-5-5',
    classifier_model: 'amd.DeepSeek-V4.1-Flash',
    classifier_timeout: 15,
    policy: 'quality',
    rules: QUALITY,
    presets: { quality: QUALITY, balanced: BALANCED },
    kinds: ['write_prose', 'answer_question', 'analyze_data', 'operate_tools', 'change_code', 'find_defects', 'plan_or_design', 'other'],
    models: [
        opt({ id: 'claude-opus-5', display_name: 'Claude Opus 5 (direct)', provider: 'claude', input_per_million: null, output_per_million: null, available: true }),
        opt({ id: 'amd.DeepSeek-V4.1-Flash', display_name: 'DeepSeek V4.1 Flash', provider: 'amd', input_per_million: 0.3, output_per_million: 1.2, cache_read_per_million: 0.03, available: true }),
        opt({ id: 'amd.Claude-Opus-5-5', display_name: 'Claude Opus 5.5', provider: 'amd', input_per_million: 5, output_per_million: 25, cache_read_per_million: 0.5, available: true }),
        opt({ id: 'amd.Qwen-Coder', display_name: 'Qwen Coder', provider: 'amd', input_per_million: null, output_per_million: null, cache_read_per_million: null, available: true }),
        opt({ id: 'amd.Retired', display_name: 'Retired model', provider: 'amd', input_per_million: 1, output_per_million: 2, cache_read_per_million: null, available: false }),
        opt({ id: 'Gemma-4-E4B-it-GGUF', display_name: 'Gemma 4 E4B', provider: 'local', available: true }),
        opt({ id: 'Qwen3.6-35B-A3B-GGUF', display_name: 'Qwen3.6 35B A3B', provider: 'local', available: false, reason: 'Download Qwen3.6-35B-A3B-GGUF' }),
        opt({ id: 'fireworks.deepseek-v4p1-flash', display_name: 'deepseek-v4p1-flash', provider: 'fireworks', input_per_million: 0.3, output_per_million: 1.2, price_source: 'Fireworks AI list price', available: true }),
    ],
    models_error: null,
    prices_fetched_at: '2026-11-16T08:00:00Z',
    prices_error: null,
    measured: true,
    measured_note: null,
    classifier_note: null,
    entry: { id: 'hybrid', display_name: 'Hybrid (DeepSeek V4.1 Flash + Claude Opus 5.5)', remote: true, available: true, reason: null, shown: true },
    is_active: true,
    config_error: null,
};

const STATS: HybridStats = {
    since: '2026-11-01T00:00:00Z', turns: 40, cheap_turns: 30, strong_turns: 10, failed_closed: 2,
    cost: 1.5, cost_if_all_strong: 6, saving_pct: 75, unpriced_turns: 0, prices_fetched_at: null, prices_error: null,
};

function template(over: Partial<HybridTemplate> & Pick<HybridTemplate, 'id' | 'name'>): HybridTemplate {
    return {
        description: 'A pair.', cheap_model: 'amd.DeepSeek-V4.1-Flash', strong_model: 'amd.Claude-Opus-5-5', classifier_model: 'amd.DeepSeek-V4.1-Flash',
        policy: 'quality', providers_needed: ['amd'], available: true, reason: null,
        measured: false, measured_note: MEASURED_NOTE, classifier_note: null, ...over,
    };
}

const TEMPLATES: HybridTemplate[] = [
    template({ id: 'gateway-deepseek-opus', name: 'Gateway: DeepSeek + Opus', description: 'The pair the policies were tuned on.', measured: true, measured_note: null }),
    template({ id: 'gateway-deepseek-sonnet', name: 'Gateway: DeepSeek + Sonnet', strong_model: 'amd.Claude-Sonnet-5-5' }),
    template({
        id: 'all-local', name: 'All on this PC', cheap_model: 'Gemma-4-E4B-it-GGUF', strong_model: 'Qwen3.6-35B-A3B-GGUF',
        classifier_model: 'Gemma-4-E4B-it-GGUF', classifier_note: CLASSIFIER_NOTE,
        providers_needed: ['local'], available: false, reason: 'Download Qwen3.6-35B-A3B-GGUF',
    }),
    template({
        id: 'fireworks', name: 'Fireworks + gateway', cheap_model: 'fireworks.deepseek-v4p1-flash',
        providers_needed: ['fireworks', 'amd'], available: false, reason: 'Add a Fireworks key',
    }),
];

/** A 422 as apiFetch throws it: the backend's sentence, and the fields its `loc` names. */
function rejection(message: string, fields: string[] = []): Error {
    return Object.assign(new Error(message), { httpStatus: 422, detail: message, fields });
}

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.getHybridSettings.mockResolvedValue(SETTINGS);
    mockedApi.getHybridStats.mockResolvedValue(STATS);
    mockedApi.updateHybridSettings.mockImplementation(async (update) => ({ ...SETTINGS, ...update }));
    mockedApi.getHybridTemplates.mockResolvedValue(TEMPLATES);
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
        // The policies were tuned on this pair, and a gateway model classifies: no notes.
        expect(within(form).queryByRole('list', { name: 'About this pairing' })).not.toBeInTheDocument();
        expect(within(form).getByLabelText('Classifier model')).toHaveValue('amd.DeepSeek-V4.1-Flash');
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
            reason: 'one-line fix with the cause stated', fix: '', tier: 'cheap', classified: true, seconds: 1.234, policy: 'quality',
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
            reason: 'the classifier took longer than 15 s', fix: 'raise hybrid_classifier_timeout in Settings › Models › Hybrid',
            tier: 'strong', classified: false, seconds: 15.02, policy: 'quality',
        });
        render(<HybridSettings />);
        await screen.findByRole('form', { name: 'Hybrid' });
        fireEvent.change(screen.getByLabelText('Try a request'), { target: { value: 'Review this PR' } });
        fireEvent.keyDown(screen.getByLabelText('Try a request'), { key: 'Enter', ctrlKey: true });

        const result = await screen.findByRole('status', { name: 'Route for this request' });
        expect(result).toHaveTextContent('The classifier couldn’t decide, so the strong model runs.');
        expect(result).toHaveTextContent('Claude Opus 5.5');
        expect(result).toHaveTextContent('Picked by hybrid. the classifier took longer than 15 s');
        expect(result).toHaveTextContent('raise hybrid_classifier_timeout in Settings › Models › Hybrid');
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

    it('says the cost is unknown rather than $0, and counts unpriced turns', async () => {
        mockedApi.getHybridStats.mockResolvedValue({ ...STATS, cost: null, cost_if_all_strong: null, saving_pct: null, unpriced_turns: 3 });
        render(<HybridSettings />);
        const usage = await screen.findByRole('region', { name: 'Usage' });
        await waitFor(() => expect(usage).toHaveTextContent('Cost unknown: a model these turns ran on has no known price.'));
        expect(usage).toHaveTextContent('Turns on models with an unknown price, not counted in the cost: 3.');
        expect(usage).not.toHaveTextContent('$0.00');
    });

    it('shows a stats error without hiding the settings', async () => {
        mockedApi.getHybridStats.mockRejectedValue(new Error('The spending database could not be read.'));
        render(<HybridSettings />);
        expect(await screen.findByText('The spending database could not be read.')).toHaveAttribute('role', 'alert');
        expect(await screen.findByRole('form', { name: 'Hybrid' })).toBeInTheDocument();
    });
});

describe('HybridSettings: models from every provider', () => {
    it('groups each dropdown by provider, with prices when known and the reason a model is unavailable', async () => {
        const form = await renderPanel();
        const strong = within(form).getByLabelText('Strong model');
        const groups = Array.from(strong.querySelectorAll('optgroup'));
        expect(groups.map((g) => g.label)).toEqual(['Local', 'AMD LLM Gateway', 'Fireworks AI', 'Claude']);
        const options = (label: string) => Array.from(groups.find((g) => g.label === label)!.querySelectorAll('option'));

        expect(options('Local').map((o) => o.textContent)).toEqual([
            'Gemma 4 E4B · on this PC', 'Qwen3.6 35B A3B — Download Qwen3.6-35B-A3B-GGUF',
        ]);
        expect(options('Local')[1]).toBeDisabled();
        expect(options('Fireworks AI')[0]).toHaveTextContent('deepseek-v4p1-flash · $0.30 in · $1.20 out per 1M tokens');
        expect(options('Claude')[0]).toHaveTextContent('Claude Opus 5 (direct) · price unknown');
        expect(options('Claude')[0]).toBeEnabled();
    });

    it('saves a model from another provider', async () => {
        const form = await renderPanel();
        fireEvent.change(within(form).getByLabelText('Cheap model'), { target: { value: 'fireworks.deepseek-v4p1-flash' } });
        fireEvent.change(within(form).getByLabelText('Strong model'), { target: { value: 'claude-opus-5' } });
        fireEvent.click(within(form).getByRole('button', { name: 'Save' }));
        await waitFor(() => expect(mockedApi.updateHybridSettings).toHaveBeenCalledWith({
            cheap_model: 'fireworks.deepseek-v4p1-flash', strong_model: 'claude-opus-5',
        }));
    });

    it('shows the API’s notes for an unmeasured pair and a classifier on this PC', async () => {
        mockedApi.getHybridSettings.mockResolvedValue({
            ...SETTINGS, cheap_model: 'Gemma-4-E4B-it-GGUF', classifier_model: 'Gemma-4-E4B-it-GGUF',
            measured: false, measured_note: MEASURED_NOTE, classifier_note: CLASSIFIER_NOTE,
        });
        const form = await renderPanel();
        const notes = within(form).getByRole('list', { name: 'About this pairing' });
        expect(notes).toHaveTextContent(MEASURED_NOTE);
        expect(notes).toHaveTextContent(CLASSIFIER_NOTE);
    });

    it('shows why the saved settings, the model list, or the prices could not be used', async () => {
        mockedApi.getHybridSettings.mockResolvedValue({
            ...SETTINGS, rules: null, policy: 'custom',
            config_error: 'hybrid_rules is required when hybrid_policy is custom.',
            models: [], models_error: 'Couldn’t list the models hybrid can use: the model server is not running.',
            prices_error: 'The gateway price list could not be fetched.',
        });
        const form = await renderPanel();
        expect(screen.getByText('hybrid_rules is required when hybrid_policy is custom.')).toBeInTheDocument();
        expect(within(form).getByText(/the model server is not running/)).toHaveAttribute('role', 'alert');
        expect(within(form).getByText('Prices may be out of date: The gateway price list could not be fetched.')).toBeInTheDocument();
        // The table starts from Quality so the missing rules can be saved.
        expect(within(form).getByLabelText('Code changes: cheap model up to difficulty')).toHaveValue('2');
        fireEvent.click(within(form).getByRole('button', { name: 'Save' }));
        await waitFor(() => expect(mockedApi.updateHybridSettings).toHaveBeenCalledWith({ rules: QUALITY }));
    });
});

describe('HybridSettings: templates', () => {
    it('shows each template with its pair, badge and availability', async () => {
        render(<HybridSettings onShowProvider={vi.fn()} />);
        const list = await screen.findByRole('list');
        const cards = within(list).getAllByRole('listitem');
        expect(cards).toHaveLength(4);

        const measured = within(list).getByRole('listitem', { name: 'Gateway: DeepSeek + Opus' });
        expect(measured).toHaveTextContent('Measured');
        expect(measured).toHaveTextContent('DeepSeek V4.1 Flash + Claude Opus 5.5 · Quality');
        expect(measured).toHaveTextContent('In use');
        expect(within(measured).getByRole('button', { name: 'Use the Gateway: DeepSeek + Opus template' })).toBeEnabled();

        expect(measured).not.toHaveTextContent(MEASURED_NOTE);

        const sonnet = within(list).getByRole('listitem', { name: 'Gateway: DeepSeek + Sonnet' });
        expect(sonnet).toHaveTextContent('Not yet measured');
        expect(sonnet).toHaveTextContent(MEASURED_NOTE);
        expect(sonnet).not.toHaveTextContent('In use');
        expect(within(list).getByRole('listitem', { name: 'All on this PC' })).toHaveTextContent(CLASSIFIER_NOTE);
    });

    it('disables an unavailable template with its reason and a way to set it up', async () => {
        const onShowProvider = vi.fn();
        render(<HybridSettings onShowProvider={onShowProvider} />);
        const card = await screen.findByRole('listitem', { name: 'Fireworks + gateway' });
        const use = within(card).getByRole('button', { name: 'Use the Fireworks + gateway template' });
        expect(use).toBeDisabled();
        expect(use).toHaveAccessibleDescription('Add a Fireworks key');
        fireEvent.click(within(card).getByRole('button', { name: 'Set up Fireworks AI' }));
        expect(onShowProvider).toHaveBeenCalledWith('fireworks');

        const local = screen.getByRole('listitem', { name: 'All on this PC' });
        expect(local).toHaveTextContent('Download Qwen3.6-35B-A3B-GGUF');
        expect(within(local).getByRole('button', { name: 'Set up Local' })).toBeInTheDocument();
    });

    it('applies a template, shows the applied settings, and still lets them be tweaked and saved', async () => {
        const appliedSettings = { ...SETTINGS, strong_model: 'amd.Qwen-Coder', policy: 'balanced' as const, rules: BALANCED };
        mockedApi.applyHybridTemplate.mockResolvedValue(appliedSettings);
        mockedApi.updateHybridSettings.mockImplementation(async (update) => ({ ...appliedSettings, ...update }));
        const onSaved = vi.fn();
        render(<HybridSettings onSaved={onSaved} />);
        const card = await screen.findByRole('listitem', { name: 'Gateway: DeepSeek + Sonnet' });
        fireEvent.click(within(card).getByRole('button', { name: /Use the/ }));

        await waitFor(() => expect(mockedApi.applyHybridTemplate).toHaveBeenCalledWith('gateway-deepseek-sonnet'));
        expect(await screen.findByText('Applied Gateway: DeepSeek + Sonnet. Change anything below and save.')).toBeInTheDocument();
        const form = screen.getByRole('form', { name: 'Hybrid' });
        expect(within(form).getByLabelText('Strong model')).toHaveValue('amd.Qwen-Coder');
        expect(within(form).getByRole('radio', { name: 'Balanced' })).toHaveAttribute('aria-checked', 'true');
        expect(within(form).getByRole('button', { name: 'Save' })).toBeDisabled();
        expect(onSaved).toHaveBeenCalledTimes(1);

        fireEvent.change(within(form).getByLabelText('Classifier time limit (seconds)'), { target: { value: '40' } });
        fireEvent.click(within(form).getByRole('button', { name: 'Save' }));
        await waitFor(() => expect(mockedApi.updateHybridSettings).toHaveBeenCalledWith({ classifier_timeout: 40 }));
    });

    it('shows a 409 on the template it came from and keeps the settings as they were', async () => {
        mockedApi.applyHybridTemplate.mockRejectedValue(Object.assign(
            new Error('Add an Anthropic key in Settings first; claude-direct needs it.'), { httpStatus: 409 },
        ));
        render(<HybridSettings />);
        const card = await screen.findByRole('listitem', { name: 'Gateway: DeepSeek + Sonnet' });
        fireEvent.click(within(card).getByRole('button', { name: /Use the/ }));
        expect(await within(card).findByRole('alert')).toHaveTextContent('Add an Anthropic key in Settings first');
        expect(within(screen.getByRole('form', { name: 'Hybrid' })).getByLabelText('Strong model')).toHaveValue('amd.Claude-Opus-5-5');
        expect(screen.queryByText(/^Applied /)).not.toBeInTheDocument();
    });

    it('shows a 404 for a template the backend no longer has', async () => {
        mockedApi.applyHybridTemplate.mockRejectedValue(Object.assign(
            new Error("There is no hybrid template 'gateway-deepseek-sonnet'; the templates are gateway-deepseek-opus."), { httpStatus: 404 },
        ));
        render(<HybridSettings />);
        const card = await screen.findByRole('listitem', { name: 'Gateway: DeepSeek + Sonnet' });
        fireEvent.click(within(card).getByRole('button', { name: /Use the/ }));
        expect(await within(card).findByRole('alert')).toHaveTextContent('There is no hybrid template');
    });

    it('says why the templates could not be read', async () => {
        mockedApi.getHybridTemplates.mockRejectedValueOnce(new Error('Templates are unavailable: the gateway catalog timed out.'));
        render(<HybridSettings />);
        expect(await screen.findByText('Templates are unavailable: the gateway catalog timed out.')).toHaveAttribute('role', 'alert');
        expect(screen.getByRole('form', { name: 'Hybrid' })).toBeInTheDocument();
    });
});

describe('HybridSettings: end-to-end findings', () => {
    it('toggles a high-stakes switch with a mouse click on its track', async () => {
        const form = await renderPanel();
        fireEvent.click(within(form).getByRole('radio', { name: 'Custom' }));
        const toggle = within(form).getByRole('switch', { name: 'Writing: allow high-stakes requests on the cheap model' });
        expect(toggle).not.toBeChecked();
        // The input itself is invisible; a person clicks the track drawn beside it.
        fireEvent.click(toggle.nextElementSibling!);
        expect(toggle).toBeChecked();
        fireEvent.click(toggle.nextElementSibling!);
        expect(toggle).not.toBeChecked();
    });

    it('keeps Undo while the time limit is invalid, and Undo restores it', async () => {
        const form = await renderPanel();
        const timeout = within(form).getByLabelText('Classifier time limit (seconds)');
        fireEvent.change(timeout, { target: { value: '0' } });
        fireEvent.click(within(form).getByRole('button', { name: 'Undo changes' }));
        expect(timeout).toHaveValue(15);
        expect(within(form).queryByRole('button', { name: 'Undo changes' })).not.toBeInTheDocument();
    });

    it('shows a sub-cent cost instead of rounding it to $0.00', async () => {
        mockedApi.getHybridStats.mockResolvedValue({
            ...STATS, turns: 1, cheap_turns: 1, strong_turns: 0, failed_closed: 0, cost: 0.002832, cost_if_all_strong: 0.04452, saving_pct: 93.6,
        });
        render(<HybridSettings />);
        const usage = await screen.findByRole('region', { name: 'Usage' });
        await waitFor(() => expect(usage).toHaveTextContent('Cost $0.0028, against $0.04 if every turn had run on the strong model (94% saved).'));
        expect(usage).not.toHaveTextContent('$0.00,');
    });

    it('says what the usage covers when the backend names it', async () => {
        mockedApi.getHybridStats.mockResolvedValue({ ...STATS, scope: 'Counts hybrid turns from Agent UI chats.' });
        render(<HybridSettings />);
        const usage = await screen.findByRole('region', { name: 'Usage' });
        await waitFor(() => expect(usage).toHaveTextContent('Counts hybrid turns from Agent UI chats.'));
    });

    it('stays usable when the saved settings are broken, and saves the fix', async () => {
        mockedApi.getHybridSettings.mockResolvedValue({
            ...SETTINGS, policy: 'custom', rules: null,
            config_error: 'hybrid_rules is missing "other". To fix, set every kind in Settings › Models › Hybrid.',
        });
        const form = await renderPanel();
        expect(screen.getByText(/hybrid_rules is missing "other"/).closest('[role="alert"]')).toBeInTheDocument();
        expect(within(form).getByRole('radio', { name: 'Custom' })).toHaveAttribute('aria-checked', 'true');
        fireEvent.click(within(form).getByRole('button', { name: 'Start from Balanced' }));
        fireEvent.click(within(form).getByRole('button', { name: 'Save' }));
        await waitFor(() => expect(mockedApi.updateHybridSettings).toHaveBeenCalledWith({ rules: BALANCED }));
    });
});
