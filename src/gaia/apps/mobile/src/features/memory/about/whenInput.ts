// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The date-and-time field's format, "2026-11-20 09:00"; the PC takes the same value a web date-time field gives, "2026-11-20T09:00". */
const WHEN = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})$/;

/** What the person typed as the PC wants it, or null when it isn't a real date and time. */
export function parseWhen(text: string): string | null {
    const m = WHEN.exec(text.trim());
    if (!m) return null;
    const [year, month, day, hour, minute] = m.slice(1).map(Number) as [number, number, number, number, number];
    const date = new Date(year, month - 1, day, hour, minute);
    const real = date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day && hour < 24 && minute < 60;
    return real ? `${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}` : null;
}

/** A web date-time value as the field shows it. */
export function whenText(inputValue: string): string {
    return inputValue.replace('T', ' ');
}
