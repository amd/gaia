// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Fragment } from 'react';
import { useT } from '../i18n/useT';
import { calendarFacts, calendarWhen, CALENDAR_WRITE_TOOLS, unseenWith, visibleWith } from '../utils/calendarApproval.core';

export { CALENDAR_WRITE_TOOLS, unseenWith, visibleWith };

function Name({ value }: { value: string }) {
    return <bdi dir="auto">{value}</bdi>;
}

/** The title, time, account and people a calendar approval turns on, with no click needed. */
export function CalendarApproval({ tool, args }: { tool: string; args: Record<string, unknown> }) {
    const t = useT();
    const { rows, peopleLabel, people, unseenNote } = calendarFacts(tool, args, t);
    return (
        <>
        <dl className="perm-card-facts" data-testid="perm-calendar">
            {rows.map(({ label, value }) => (
                <Fragment key={label}>
                    <dt>{label}</dt>
                    <dd>
                        {value.kind === 'text' ? <Name value={value.text} /> : <When when={value.when} />}
                    </dd>
                </Fragment>
            ))}
            <dt>{peopleLabel}</dt>
            <dd>
                {people.length > 0 && (
                    <div className="perm-card-people" tabIndex={0}>
                        {people.map((person) => <Name key={person} value={person} />)}
                    </div>
                )}
            </dd>
        </dl>
        {unseenNote && <p className="perm-card-risk" role="note" data-testid="perm-calendar-unseen">{unseenNote}</p>}
        </>
    );
}

function When({ when }: { when: ReturnType<typeof calendarWhen> }) {
    const t = useT();
    if (when.raw !== undefined) return <bdi dir="ltr">{when.raw}</bdi>;
    return <><bdi dir="auto">{when.text}</bdi>{when.allDay ? ` (${t('permission.calendar.allDay')})` : null}</>;
}
