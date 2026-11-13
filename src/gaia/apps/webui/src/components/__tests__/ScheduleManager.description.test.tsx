// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ScheduleManager, describeSchedule } from '../ScheduleManager';
import { useChatStore } from '../../stores/chatStore';
import { useScheduleStore } from '../../stores/scheduleStore';
import { parseScheduleInput } from '../../services/api';
import { stripIsolates } from '../../i18n/registry';
import { translator } from '../../i18n/useT';
import type { ParsedSchedule, Schedule, ScheduleDescription } from '../../types';

vi.mock('../../services/api');

const parts = (p: Partial<ScheduleDescription> & Pick<ScheduleDescription, 'kind'>): ScheduleDescription => ({
    count: null, unit: null, days: null, time: null, start_hour: null, end_hour: null, ...p,
});
const every = (count: number, unit: ScheduleDescription['unit'], days: number[] | null = null) =>
    parts({ kind: 'every', count, unit, days });
const WEEKDAYS = [0, 1, 2, 3, 4];

const clock = (locale: string | undefined, h: number) =>
    new Intl.DateTimeFormat(locale, { hour: 'numeric', minute: '2-digit' }).format(new Date(2000, 0, 1, h, 0));
const EN_8 = clock(undefined, 8);
const EN_18 = clock(undefined, 18);
const AR_8 = clock('ar-u-nu-latn', 8);
const AR_18 = clock('ar-u-nu-latn', 18);

const en = (p: ScheduleDescription) => describeSchedule(p, translator('en'));
const ar = (p: ScheduleDescription) => stripIsolates(describeSchedule(p, translator('ar')) ?? '');

describe('describeSchedule in English', () => {
    it.each([
        [every(2, 'minute'), 'Every 2 minutes'],
        [every(1, 'hour'), 'Every hour'],
        [every(90, 'second'), 'Every 90 seconds'],
        [every(2, 'week'), 'Every 2 weeks'],
        [every(2, 'hour', WEEKDAYS), 'Every 2 hours on weekdays'],
        [parts({ kind: 'daily', time: '08:00' }), `Daily at ${EN_8}`],
        [parts({ kind: 'days', days: WEEKDAYS, time: '08:00' }), `Weekdays at ${EN_8}`],
        [parts({ kind: 'days', days: [5, 6] }), 'Weekends'],
        [parts({ kind: 'days', days: [0, 1, 2, 3, 4, 5, 6], time: '08:00' }), `Daily at ${EN_8}`],
        [parts({ kind: 'days', days: [0, 2], time: '08:00' }), `Every Monday, Wednesday at ${EN_8}`],
        [parts({ kind: 'window', count: 1, unit: 'hour', start_hour: 8, end_hour: 18, days: WEEKDAYS }), `Every hour, ${EN_8} - ${EN_18}, weekdays`],
        [parts({ kind: 'window', count: 30, unit: 'minute', start_hour: 8, end_hour: 18 }), `Every 30 minutes, ${EN_8} - ${EN_18}`],
    ])('%j reads %s', (p, text) => {
        expect(en(p)).toBe(text);
    });
});

describe('describeSchedule in Arabic', () => {
    it.each([
        [1, 'كل دقيقة (Every minute)'],
        [2, 'كل دقيقتين (Every 2 minutes)'],
        [5, 'كل 5 دقائق (Every 5 minutes)'],
        [15, 'كل 15 دقيقة (Every 15 minutes)'],
        [100, 'كل 100 دقيقة (Every 100 minutes)'],
    ])('agrees minutes with %i', (n, text) => {
        expect(ar(every(n, 'minute'))).toBe(text);
    });

    it.each([
        [every(2, 'hour'), 'كل ساعتين (Every 2 hours)'],
        [every(3, 'day'), 'كل 3 أيام (Every 3 days)'],
        [every(15, 'day'), 'كل 15 يومًا (Every 15 days)'],
        [every(2, 'week'), 'كل أسبوعين (Every 2 weeks)'],
        [every(90, 'second'), 'كل 90 ثانية (Every 90 seconds)'],
    ])('words %j as %s', (p, text) => {
        expect(ar(p)).toBe(text);
    });

    it('names days and times in Arabic', () => {
        expect(ar(parts({ kind: 'days', days: WEEKDAYS, time: '08:00' }))).toBe(
            `من الاثنين إلى الجمعة الساعة ${AR_8} (Weekdays at ${EN_8})`);
        expect(ar(parts({ kind: 'days', days: [1] }))).toBe('كل يوم الثلاثاء (Every Tuesday)');
        expect(ar(parts({ kind: 'days', days: [0, 2], time: '08:00' }))).toBe(
            `كل يومي الاثنين والأربعاء الساعة ${AR_8} (Every Monday, Wednesday at ${EN_8})`);
        expect(ar(parts({ kind: 'daily', time: '18:00' }))).toBe(`يوميًا الساعة ${AR_18} (Daily at ${EN_18})`);
        expect(ar(parts({ kind: 'window', count: 1, unit: 'hour', start_hour: 8, end_hour: 18, days: WEEKDAYS }))).toBe(
            `كل ساعة، من ${AR_8} إلى ${AR_18}، من الاثنين إلى الجمعة (Every hour, ${EN_8} - ${EN_18}, weekdays)`);
        expect(AR_8).toMatch(/ص/);
    });

    it('has no structured form for a shape it cannot word', () => {
        expect(describeSchedule(every(0, 'minute'), translator('ar'))).toBeNull();
        expect(describeSchedule(parts({ kind: 'days' }), translator('ar'))).toBeNull();
        expect(describeSchedule(null, translator('ar'))).toBeNull();
    });
});

const schedule: Schedule = {
    id: 's1', name: 'brief', interval_seconds: 86400, prompt: 'Summarize', status: 'active',
    created_at: null, last_run_at: null, next_run_at: null, last_result: null, run_count: 0, error_count: 0,
    session_id: null, schedule_config: null, description: 'Weekdays at 8:00 AM',
    description_parts: parts({ kind: 'days', days: WEEKDAYS, time: '08:00' }), last_error: null,
};

const valid: ParsedSchedule = {
    interval_seconds: 120, time_of_day: null, start_hour: null, end_hour: null, days_of_week: null,
    description: 'Every 2 minutes', description_parts: every(2, 'minute'), next_run_at: null, valid: true,
};

/** Description text a reader sees, outside the English gloss. */
const meta = (container: HTMLElement) =>
    stripIsolates(container.querySelector('.schedule-card-meta span')?.textContent ?? '');

function typeSchedule(text: string) {
    fireEvent.click(screen.getByRole('button', { name: /New schedule|مهمة مجدولة جديدة/ }));
    fireEvent.change(screen.getByRole('textbox', { name: /When to run|موعد التشغيل/ }), { target: { value: text } });
}

beforeEach(() => {
    useChatStore.setState({ showSchedules: true });
    useScheduleStore.setState({ draft: null, schedules: [], loading: false, error: null, actionError: null, loadSchedules: vi.fn(async () => {}) });
});

afterEach(() => {
    act(() => useChatStore.getState().setLocale('en'));
    localStorage.removeItem('gaia-chat-locale');
});

describe.each(['en', 'ar'])('schedule descriptions on screen (%s)', (locale) => {
    beforeEach(() => act(() => useChatStore.getState().setLocale(locale)));

    it('words the preview', async () => {
        vi.mocked(parseScheduleInput).mockResolvedValue(valid);
        const { container } = render(<ScheduleManager />);
        typeSchedule('every 2m');
        const preview = await screen.findByText((_, el) => el?.classList.contains('schedule-parsed-description') ?? false, {}, { timeout: 3000 });
        const text = stripIsolates(preview.textContent ?? '');
        expect(text).toContain(locale === 'ar' ? 'كل دقيقتين' : 'Every 2 minutes');
        expect(container.querySelector('.schedule-parsed-description bdi')).toBeNull();
    });

    it('words the list row and its tooltip', () => {
        useScheduleStore.setState({ schedules: [schedule] });
        const { container } = render(<ScheduleManager />);
        const time = locale === 'ar' ? AR_8 : EN_8;
        expect(meta(container)).toContain(locale === 'ar' ? `من الاثنين إلى الجمعة الساعة ${time}` : `Weekdays at ${time}`);
        const title = stripIsolates(container.querySelector('.schedule-card-meta span')?.getAttribute('title') ?? '');
        expect(title).toContain(locale === 'ar' ? 'من الاثنين إلى الجمعة' : 'Weekdays');
    });

    it('shows the English description, isolated, when there is no structured form', () => {
        useScheduleStore.setState({ schedules: [{ ...schedule, description_parts: parts({ kind: 'days' }) }] });
        const { container } = render(<ScheduleManager />);
        expect(container.querySelector('.schedule-card-meta bdi')?.textContent).toBe('Weekdays at 8:00 AM');
    });
});
