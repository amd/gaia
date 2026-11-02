// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Fragment, type ReactNode } from 'react';
import { useT } from '../i18n/useT';

/** The gated calendar tools (`calendar_tools.py`), whose arguments a person must see to approve. */
export const CALENDAR_WRITE_TOOLS = new Set(['create_calendar_event', 'update_calendar_event', 'cancel_calendar_event']);

const BARE_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
const TIME_FORMAT = new Intl.DateTimeFormat('en-US', {
    year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
});
const TIME_ONLY_FORMAT = new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit', timeZoneName: 'short' });
const DATE_FORMAT = new Intl.DateTimeFormat('en-US', { year: 'numeric', month: 'short', day: 'numeric' });

/** A model may copy an event's fence back into the call (`wrap_untrusted_field`). */
function text(value: unknown): string {
    return typeof value === 'string' ? value.replace(/<<<(END_)?UNTRUSTED_TEXT>>>/g, '').trim() : '';
}

/** Local midnight, so an all-day date can't slide to a neighbouring day. */
function bareDate(value: string): Date | null {
    const m = BARE_DATE.exec(value);
    return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null;
}

function When({ start, end }: { start: string; end: string }) {
    const t = useT();
    const firstDay = bareDate(start);
    if (firstDay) {
        const lastDay = bareDate(end);
        const range = lastDay && lastDay > firstDay
            ? `${DATE_FORMAT.format(firstDay)} – ${DATE_FORMAT.format(lastDay)}`
            : DATE_FORMAT.format(firstDay);
        return <><bdi dir="ltr">{range}</bdi> ({t('permission.calendar.allDay')})</>;
    }
    const begins = new Date(start);
    const ends = new Date(end);
    let shown = start;
    if (!Number.isNaN(begins.getTime())) {
        shown = TIME_FORMAT.format(begins);
        if (!Number.isNaN(ends.getTime()) && ends > begins) {
            const sameDay = DATE_FORMAT.format(begins) === DATE_FORMAT.format(ends);
            shown += ` – ${(sameDay ? TIME_ONLY_FORMAT : TIME_FORMAT).format(ends)}`;
        }
    }
    return <bdi dir="ltr">{shown}</bdi>;
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
