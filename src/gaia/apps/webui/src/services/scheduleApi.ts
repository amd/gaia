// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** API client for scheduled tasks. */

import type { Schedule, ScheduleResult, ParsedSchedule } from '../types';
import { apiFetch } from './api';

export async function listSchedules(): Promise<{ schedules: Schedule[]; total: number }> {
    return apiFetch('GET', '/schedules');
}

/** The user's IANA time zone, so "daily at 9am" means 9am where they are. */
export function browserTimeZone(): string | undefined {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;
}

export async function createSchedule(name: string, interval: string, prompt: string): Promise<Schedule> {
    return apiFetch('POST', '/schedules', { name, interval, prompt, timezone: browserTimeZone() });
}

/** Change when a schedule runs and what it asks; its chat and run history stay. */
export async function editSchedule(name: string, interval: string, prompt: string): Promise<Schedule> {
    return apiFetch('PATCH', `/schedules/${encodeURIComponent(name)}`, { interval, prompt, timezone: browserTimeZone() });
}

export async function updateSchedule(name: string, status: string): Promise<Schedule> {
    return apiFetch('PUT', `/schedules/${encodeURIComponent(name)}`, { status });
}

/** Run a schedule once now; its next scheduled time stays as it was. */
export async function runScheduleNow(name: string): Promise<Schedule> {
    return apiFetch('POST', `/schedules/${encodeURIComponent(name)}/run`);
}

export async function deleteSchedule(name: string): Promise<void> {
    return apiFetch('DELETE', `/schedules/${encodeURIComponent(name)}`);
}

export async function getScheduleResults(name: string, limit: number = 20): Promise<{ results: ScheduleResult[]; total: number }> {
    return apiFetch('GET', `/schedules/${encodeURIComponent(name)}/results?limit=${limit}`);
}

export async function parseScheduleInput(input: string): Promise<ParsedSchedule> {
    return apiFetch('POST', '/schedules/parse', { input, timezone: browserTimeZone() });
}
