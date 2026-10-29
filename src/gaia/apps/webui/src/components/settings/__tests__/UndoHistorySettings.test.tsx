// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { UndoHistorySettings } from '../UndoHistorySettings';
import * as api from '../../../services/api';
import type { UndoSettings } from '../../../types/changes';

vi.mock('../../../services/api', async (importOriginal) => {
    const actual = await importOriginal<typeof import('../../../services/api')>();
    return { ...actual, getUndoSettings: vi.fn(), updateUndoSettings: vi.fn() };
});

const DAYS_ERROR = 'Keep for: enter a whole number of days from 1 to 365.';
const MB_ERROR = 'Use at most: enter a whole number of MB from 10 to 100,000.';

const current: UndoSettings = {
    retention_days: 30, max_mb: 500, used_bytes: 2048, turns: 3, path: 'C:\\tmp\\undo',
};

async function renderLoaded() {
    render(<UndoHistorySettings />);
    await screen.findByLabelText('Keep for (days)');
    return {
        days: screen.getByLabelText('Keep for (days)') as HTMLInputElement,
        mb: screen.getByLabelText('Use at most (MB)') as HTMLInputElement,
        save: screen.getByRole('button', { name: 'Save' }),
    };
}

beforeEach(() => {
    vi.mocked(api.getUndoSettings).mockReset().mockResolvedValue(current);
    vi.mocked(api.updateUndoSettings).mockReset();
});

describe('UndoHistorySettings limits', () => {
    it.each([
        ['days below range', 'days', '-5', DAYS_ERROR],
        ['days zero', 'days', '0', DAYS_ERROR],
        ['days decimal', 'days', '1.5', DAYS_ERROR],
        ['days cleared', 'days', '', DAYS_ERROR],
        ['MB above range', 'mb', '99999999', MB_ERROR],
        ['MB below range', 'mb', '5', MB_ERROR],
        ['MB decimal', 'mb', '20.5', MB_ERROR],
        ['MB cleared', 'mb', '', MB_ERROR],
    ])('%s names the field and does not save', async (_name, field, value, message) => {
        const inputs = await renderLoaded();
        const input = field === 'days' ? inputs.days : inputs.mb;
        fireEvent.change(input, { target: { value } });

        const error = screen.getByText(message);
        expect(input).toHaveAttribute('aria-invalid', 'true');
        expect(input.getAttribute('aria-describedby')).toBe(error.id);
        // Inline errors are not live regions, so typing digit by digit doesn't re-announce them.
        expect(screen.queryByRole('alert')).toBeNull();
        expect(inputs.save).toBeDisabled();
        fireEvent.click(inputs.save);
        expect(api.updateUndoSettings).not.toHaveBeenCalled();
        expect(screen.queryByText(/Input should be/)).toBeNull();
    });

    it('saves valid values', async () => {
        vi.mocked(api.updateUndoSettings).mockResolvedValue({ ...current, retention_days: 7, max_mb: 100 });
        const { days, mb, save } = await renderLoaded();
        fireEvent.change(days, { target: { value: '7' } });
        fireEvent.change(mb, { target: { value: '100' } });
        expect(days).toHaveAttribute('aria-invalid', 'false');
        fireEvent.click(save);

        await screen.findByText(/^Saved\./);
        expect(api.updateUndoSettings).toHaveBeenCalledWith(7, 100);
        expect(screen.queryByText(DAYS_ERROR)).toBeNull();
        expect(screen.queryByText(MB_ERROR)).toBeNull();
    });

    it('rewords a backend 422 per field instead of showing pydantic text', async () => {
        vi.mocked(api.updateUndoSettings).mockRejectedValue(new api.ApiError(
            'Input should be less than or equal to 100000', 422,
            'Input should be less than or equal to 100000', null, ['max_mb'],
        ));
        const { days, mb, save } = await renderLoaded();
        fireEvent.change(days, { target: { value: '7' } });
        fireEvent.click(save);

        await waitFor(() => expect(screen.getByText(MB_ERROR)).toBeInTheDocument());
        expect(mb).toHaveAttribute('aria-invalid', 'true');
        expect(screen.queryByText(DAYS_ERROR)).toBeNull();
        expect(screen.queryByText(/Input should be/)).toBeNull();
        expect(screen.getByRole('alert')).toHaveTextContent('Not saved. Fix the limit marked above.');
    });
});

describe('parseErrorFields', () => {
    it('reads the field name from each 422 loc', () => {
        const body = JSON.stringify({ detail: [
            { loc: ['body', 'retention_days'], msg: 'x' },
            { loc: ['body', 'max_mb'], msg: 'y' },
        ] });
        expect(api.parseErrorFields(body)).toEqual(['retention_days', 'max_mb']);
        expect(api.parseErrorFields('{"detail":"nope"}')).toEqual([]);
        expect(api.parseErrorFields('<html>')).toEqual([]);
    });
});
