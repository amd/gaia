// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Fragment, type ReactNode } from 'react';
import { useT } from '../i18n/useT';

/** The gated calendar tools (`calendar_tools.py`), whose arguments a person must see to approve. */
export const CALENDAR_WRITE_TOOLS = new Set(['create_calendar_event', 'update_calendar_event', 'cancel_calendar_event']);

const BARE_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
const DAY = { year: 'numeric', month: 'short', day: 'numeric' } as const;
const CLOCK = { hour: 'numeric', minute: '2-digit', timeZoneName: 'short' } as const;

interface Formats { time: Intl.DateTimeFormat; timeOnly: Intl.DateTimeFormat; date: Intl.DateTimeFormat }
const FORMATS = new Map<string, Formats>();

/** Dates in the UI's language, built once per locale. */
function formatsFor(locale: string): Formats {
    let formats = FORMATS.get(locale);
    if (!formats) {
        formats = {
            time: new Intl.DateTimeFormat(locale, { ...DAY, ...CLOCK }),
            timeOnly: new Intl.DateTimeFormat(locale, CLOCK),
            date: new Intl.DateTimeFormat(locale, DAY),
        };
        FORMATS.set(locale, formats);
    }
    return formats;
}

/** Characters that can hide or reorder text (`person_approval.py`'s `_HIDDEN_RE`). */
// eslint-disable-next-line no-control-regex
const HIDDEN = /[\u0000-\u0008\u000b-\u001f\u007f-\u009f\u00ad\u180e\u200b\u2028-\u202e\u2060-\u2064\u2066-\u2069\ufeff]/g;

/** `[U+202E]` in place of each, so what is approved reads as what is saved. */
function visibleText(value: string): string {
    return value.replace(HIDDEN, (c) => `[U+${c.charCodeAt(0).toString(16).toUpperCase().padStart(4, '0')}]`);
}

/** A model may copy an event's fence back into the call (`wrap_untrusted_field`). */
function text(value: unknown): string {
    return typeof value === 'string' ? visibleText(value.replace(/<<<(END_)?UNTRUSTED_TEXT>>>/g, '').trim()) : '';
}

/** Local midnight, so an all-day date can't slide to a neighbouring day. */
function bareDate(value: string): Date | null {
    const m = BARE_DATE.exec(value);
    return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null;
}

function When({ start, end }: { start: string; end: string }) {
    const t = useT();
    const { time, timeOnly, date } = formatsFor(t.locale);
    const firstDay = bareDate(start);
    if (firstDay) {
        const lastDay = bareDate(end);
        const range = lastDay && lastDay > firstDay
            ? `${date.format(firstDay)} – ${date.format(lastDay)}`
            : date.format(firstDay);
        return <><bdi dir="auto">{range}</bdi> ({t('permission.calendar.allDay')})</>;
    }
    const begins = new Date(start);
    const ends = new Date(end);
    if (Number.isNaN(begins.getTime())) return <bdi dir="ltr">{start}</bdi>;
    let shown = time.format(begins);
    if (!Number.isNaN(ends.getTime()) && ends > begins) {
        const sameDay = date.format(begins) === date.format(ends);
        shown += ` – ${(sameDay ? timeOnly : time).format(ends)}`;
    }
    return <bdi dir="auto">{shown}</bdi>;
}

function Name({ value }: { value: string }) {
    return <bdi dir="auto">{value}</bdi>;
}

/** The title, time, account and people a calendar approval turns on, with no click needed. */
export function CalendarApproval({ tool, args }: { tool: string; args: Record<string, unknown> }) {
    const t = useT();
    const people = Array.isArray(args.notify_attendees) ? args.notify_attendees.map(text).filter(Boolean) : [];
    const rows: Array<[string, ReactNode]> = [];
    const named = (label: string, value: unknown) => {
        const shown = text(value);
        if (shown) rows.push([label, <Name value={shown} />]);
    };

    if (tool === 'create_calendar_event') {
        named(t('permission.calendar.title'), args.title);
        rows.push([t('permission.calendar.when'), <When start={text(args.start)} end={text(args.end)} />]);
        named(t('permission.calendar.account'), args.calendar_account);
        named(t('permission.calendar.location'), args.location);
        named(t('permission.calendar.description'), args.description);
    } else {
        named(t('permission.calendar.event'), args.expected_title);
        rows.push([t('permission.calendar.when'), <When start={text(args.expected_start)} end="" />]);
        named(t('permission.calendar.newTitle'), args.new_title);
        if (text(args.new_start)) {
            rows.push([t('permission.calendar.newTime'), <When start={text(args.new_start)} end={text(args.new_end)} />]);
        }
        named(t('permission.calendar.newLocation'), args.new_location);
        named(t('permission.calendar.newDescription'), args.new_description);
    }

    return (
        <dl className="perm-card-facts" data-testid="perm-calendar">
            {rows.map(([label, value]) => (
                <Fragment key={label}>
                    <dt>{label}</dt>
                    <dd>{value}</dd>
                </Fragment>
            ))}
            <dt>{people.length ? t.plural('permission.calendar.notifies', people.length) : t('permission.calendar.noOne')}</dt>
            <dd>
                {people.length > 0 && (
                    <div className="perm-card-people" tabIndex={0}>
                        {people.map((person) => <Name key={person} value={person} />)}
                    </div>
                )}
            </dd>
        </dl>
    );
}
