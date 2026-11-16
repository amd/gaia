// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ScheduleManager, formatNextRun } from '../ScheduleManager';
import { useChatStore } from '../../stores/chatStore';
import { useScheduleStore } from '../../stores/scheduleStore';
import { parseScheduleInput } from '../../services/api';
import { stripIsolates } from '../../i18n/registry';
import { dateLocale, translator } from '../../i18n/useT';
import { readsAs } from '../../test/readsAs';
import type { Schedule } from '../../types';

vi.mock('../../services/api');

const schedule: Schedule = {
    id: 's1',
    name: 'morning-brief',
    interval_seconds: 86400,
    prompt: 'Summarize my inbox',
    status: 'paused',
    created_at: null,
    last_run_at: null,
    next_run_at: null,
    last_result: null,
    run_count: 9,
    error_count: 2,
    session_id: 'chat-1',
    schedule_config: null,
    description: 'Every day',
    description_parts: { kind: 'every', count: 1, unit: 'day', days: null, time: null, start_hour: null, end_hour: null },
    last_error: 'boom',
};

/** Visible text with Latin words outside names, backend text and the English gloss. */
function leftoverEnglish(root: HTMLElement): string[] {
    const left: string[] = [];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        const text = stripIsolates(n.textContent ?? '').trim();
        if (!/[A-Za-z]{3,}/.test(text) || /[؀-ۿ]/.test(text)) continue;
        if (n.parentElement?.closest('bdi, textarea, .i18n-gloss, .schedule-card-name, .schedule-card-prompt')) continue;
        left.push(text);
    }
    return left;
}

const attrs = (root: HTMLElement) =>
    [...root.querySelectorAll('[title], [aria-label], [placeholder]')].flatMap((el) =>
        ['title', 'aria-label', 'placeholder'].map((a) => el.getAttribute(a)).filter((v): v is string => !!v && v !== schedule.description && v !== schedule.name && v !== schedule.prompt));

beforeEach(() => {
    act(() => useChatStore.getState().setLocale('ar'));
    useChatStore.setState({ showSchedules: true });
    useScheduleStore.setState({ draft: null, schedules: [], loading: false, error: null, actionError: null, loadSchedules: vi.fn(async () => {}) });
});

afterEach(() => {
    act(() => useChatStore.getState().setLocale('en'));
    localStorage.removeItem('gaia-chat-locale');
});

describe('Scheduled tasks in Arabic', () => {
    it('translates the empty dialog, punctuation kept at the end', () => {
        const { container } = render(<ScheduleManager />);
        expect(screen.getByRole('dialog', { name: readsAs('المهام المجدولة (Scheduled tasks)') })).toBeInTheDocument();
        expect(screen.getByText(readsAs('لا توجد مهام مجدولة بعد. أنشئ مهمة للبدء. (No scheduled tasks yet. Create one to get started.)'))).toBeInTheDocument();
        expect(leftoverEnglish(container)).toEqual([]);
        for (const a of attrs(container)) expect(a, a).toMatch(/[؀-ۿ]/);
    });

    it('translates the new-schedule form and its validation message', async () => {
        vi.mocked(parseScheduleInput).mockResolvedValue({
            interval_seconds: 0, time_of_day: null, start_hour: null, end_hour: null, days_of_week: null,
            description: "25:00 isn't a time.", next_run_at: null, valid: false,
        });
        const { container } = render(<ScheduleManager />);
        fireEvent.click(screen.getByRole('button', { name: /مهمة مجدولة جديدة/ }));
        expect(screen.getByLabelText(readsAs('موعد التشغيل (When to run)')).tagName).toBe('TEXTAREA');
        fireEvent.change(screen.getByLabelText(readsAs('موعد التشغيل (When to run)')), { target: { value: 'daily at 25:00' } });
        expect(await screen.findByText(readsAs('لا يمكن استخدام هذا الموعد. (That schedule can\'t be used.)'), {}, { timeout: 3000 })).toBeInTheDocument();
        expect(leftoverEnglish(container)).toEqual([]);
        for (const a of attrs(container)) expect(a, a).toMatch(/[؀-ۿ]/);
    });

    it('explains in Arabic that a schedule typed in Arabic must be written in English', async () => {
        vi.mocked(parseScheduleInput).mockResolvedValue({
            interval_seconds: 0, time_of_day: null, start_hour: null, end_hour: null, days_of_week: null,
            description: "GAIA couldn't read 'كل يوم الساعة 8 صباحا'. Use a form like 'every 30m'.", next_run_at: null, valid: false,
        });
        const { container } = render(<ScheduleManager />);
        fireEvent.click(screen.getByRole('button', { name: /مهمة مجدولة جديدة/ }));
        const field = screen.getByLabelText(readsAs('موعد التشغيل (When to run)'));
        fireEvent.change(field, { target: { value: 'كل يوم الساعة 8 صباحا' } });
        expect(await screen.findByText(readsAs('لا يمكن استخدام هذا الموعد. (That schedule can\'t be used.)'), {}, { timeout: 3000 })).toBeInTheDocument();
        expect(screen.queryByText(/couldn't read/)).not.toBeInTheDocument();
        const hint = container.querySelector('.schedule-field-hint')!;
        expect(stripIsolates(hint.textContent ?? '')).toContain('اكتب الموعد بالإنجليزية، مثل "every 30m"');
        expect(field.getAttribute('aria-describedby')).toContain(hint.id);
        expect(leftoverEnglish(container)).toEqual([]);
    });

    it('translates a list row and every action on it', () => {
        useScheduleStore.setState({ schedules: [schedule] });
        const { container } = render(<ScheduleManager />);
        expect(screen.getByText(readsAs('متوقفة مؤقتًا (paused)'))).toBeInTheDocument();
        expect(screen.getByText(readsAs('مرات التشغيل: 9 (Runs: 9)'))).toBeInTheDocument();
        expect(screen.getByText(readsAs('آخر تشغيل: لا يوجد (Last: never)'))).toBeInTheDocument();
        expect(leftoverEnglish(container)).toEqual([]);
        for (const a of attrs(container)) expect(a, a).toMatch(/[؀-ۿ]/);
    });

    it('says a paused task is paused, not that it never runs', () => {
        useScheduleStore.setState({ schedules: [schedule] });
        render(<ScheduleManager />);
        expect(screen.getByText(readsAs('التشغيل التالي: متوقفة مؤقتًا (Next: paused)'))).toBeInTheDocument();
    });
});

describe('Scheduled tasks in English', () => {
    beforeEach(() => act(() => useChatStore.getState().setLocale('en')));

    it('heads the list for every status and shows a paused task as paused', () => {
        useScheduleStore.setState({ schedules: [schedule] });
        render(<ScheduleManager />);
        expect(screen.getByRole('heading', { name: 'Your schedules' })).toBeInTheDocument();
        expect(screen.getByText('Next: paused')).toBeInTheDocument();
        expect(screen.queryByText('Next: never')).not.toBeInTheDocument();
    });
});

describe('formatNextRun', () => {
    it('counts down in the chosen language', () => {
        const now = new Date('2026-10-15T09:16:00Z');
        const text = formatNextRun('2026-10-15T09:16:30Z', now, translator('ar').bare);
        expect(stripIsolates(text)).toContain('بعد 30 ث');
    });
});

const ARABIC_WEEKDAY = /الأحد|الاثنين|الثلاثاء|الأربعاء|الخميس|الجمعة|السبت/;
const ENGLISH_CLOCK = /(Mon|Tue|Wed|Thu|Fri|Sat|Sun|AM|PM)/;

describe('schedule dates follow the interface language, not the PC', () => {
    it('names the next-run day and time in Arabic', () => {
        const now = new Date('2026-10-14T09:00:00Z');
        const text = stripIsolates(formatNextRun('2026-10-16T09:00:00Z', now, translator('ar').bare, dateLocale(translator('ar'))));
        expect(text).toMatch(ARABIC_WEEKDAY);
        expect(text).not.toMatch(ENGLISH_CLOCK);
        expect(text).toContain('بعد 2 ي');
    });

    it('shows an Arabic next run on the card in an Arabic UI', () => {
        useScheduleStore.setState({ schedules: [{ ...schedule, status: 'active', next_run_at: new Date(Date.now() + 3 * 86_400_000).toISOString() }] });
        render(<ScheduleManager />);
        const next = stripIsolates(screen.getByText(/التشغيل التالي/).textContent ?? '');
        expect(next).toMatch(ARABIC_WEEKDAY);
        expect(next).not.toMatch(ENGLISH_CLOCK);
    });

    it('keeps the English gloss of the next run free of Arabic weekdays and times', () => {
        useScheduleStore.setState({ schedules: [{ ...schedule, status: 'active', next_run_at: new Date(Date.now() + 3 * 86_400_000).toISOString() }] });
        render(<ScheduleManager />);
        const gloss = screen.getByText(/التشغيل التالي/).querySelector('.i18n-gloss');
        const text = stripIsolates(gloss?.textContent ?? '');
        expect(text).toContain('Next:');
        expect(text).toMatch(/Mon|Tue|Wed|Thu|Fri|Sat|Sun/);
        expect(text).not.toMatch(/[؀-ۿ]/);
    });

    it('keeps the English gloss of the last run free of Arabic weekdays and times', () => {
        useScheduleStore.setState({ schedules: [{ ...schedule, status: 'active', last_run_at: new Date(Date.now() - 3 * 86_400_000).toISOString() }] });
        render(<ScheduleManager />);
        const gloss = screen.getAllByText(/آخر تشغيل/)[0].querySelector('.i18n-gloss');
        const text = stripIsolates(gloss?.textContent ?? '');
        expect(text).toContain('Last:');
        expect(text).not.toMatch(/[؀-ۿ]/);
    });
});

