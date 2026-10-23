// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useRef, useState, type ReactNode } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { useFocusTrap } from '../useFocusTrap';

function Dialog({ active = true, children }: { active?: boolean; children: ReactNode }) {
    const ref = useRef<HTMLDivElement>(null);
    useFocusTrap(ref, active);
    return <div ref={ref} role="dialog" aria-modal="true">{children}</div>;
}

function Page({ active, children }: { active?: boolean; children: ReactNode }) {
    return (
        <>
            <button>Behind before</button>
            <Dialog active={active}>{children}</Dialog>
            <button>Behind after</button>
        </>
    );
}

const byName = (name: string) => screen.getByRole('button', { name });

describe('useFocusTrap', () => {
    it('wraps Tab from the last element to the first', async () => {
        const user = userEvent.setup();
        render(<Page><button>First</button><button>Last</button></Page>);
        byName('Last').focus();

        await user.tab();

        expect(byName('First')).toHaveFocus();
    });

    it('wraps Shift+Tab from the first element to the last', async () => {
        const user = userEvent.setup();
        render(<Page><button>First</button><button>Last</button></Page>);
        byName('First').focus();

        await user.tab({ shift: true });

        expect(byName('Last')).toHaveFocus();
    });

    it('skips disabled and hidden elements when finding the ends', async () => {
        const user = userEvent.setup();
        render(
            <Page>
                <button disabled>Disabled first</button>
                <button>First</button>
                <button>Last</button>
                <button tabIndex={-1}>Not tabbable</button>
                <div style={{ display: 'none' }}><button>Display none</button></div>
                <div hidden><button>Hidden attr</button></div>
                <fieldset disabled><input aria-label="In disabled fieldset" /></fieldset>
            </Page>,
        );
        byName('Last').focus();

        await user.tab();
        expect(byName('First')).toHaveFocus();

        await user.tab({ shift: true });
        expect(byName('Last')).toHaveFocus();
    });

    it('reads the focusable elements again after the content changes', async () => {
        const user = userEvent.setup();
        function Growing() {
            const [more, setMore] = useState(false);
            return (
                <Page>
                    <button onClick={() => setMore(true)}>First</button>
                    {more && <button>Added</button>}
                </Page>
            );
        }
        render(<Growing />);

        await user.click(byName('First'));
        await user.tab();
        expect(byName('Added')).toHaveFocus();

        await user.tab();
        expect(byName('First')).toHaveFocus();
    });

    it('pulls focus in when Tab is pressed from outside the dialog', async () => {
        const user = userEvent.setup();
        render(<Page><button>First</button><button>Last</button></Page>);
        byName('Behind after').focus();

        await user.tab();

        expect(byName('First')).toHaveFocus();
    });

    it('treats a closed <details> as just its summary', async () => {
        const user = userEvent.setup();
        render(
            <Page>
                <button>First</button>
                <details>
                    <summary>More</summary>
                    <button>Inside closed</button>
                    <details open><summary>Nested summary</summary><button>Nested inside</button></details>
                </details>
            </Page>,
        );
        const summary = screen.getByText('More');
        summary.focus();

        await user.tab();
        expect(byName('First')).toHaveFocus();

        await user.tab({ shift: true });
        expect(summary).toHaveFocus();
    });

    it('reaches the content of an open <details>', async () => {
        const user = userEvent.setup();
        render(<Page><button>First</button><details open><summary>More</summary><button>Inside open</button></details></Page>);
        byName('First').focus();

        await user.tab({ shift: true });

        expect(byName('Inside open')).toHaveFocus();
    });

    it('stops once per radio group, on the first radio when none is checked', async () => {
        const user = userEvent.setup();
        render(
            <Page>
                <button>First</button>
                <input type="radio" name="mode" aria-label="Fast" />
                <input type="radio" name="mode" aria-label="Slow" />
            </Page>,
        );
        byName('First').focus();

        await user.tab({ shift: true });
        expect(screen.getByLabelText('Fast')).toHaveFocus();

        await user.tab();
        expect(byName('First')).toHaveFocus();
    });

    it('stops once per radio group, on the checked radio', async () => {
        const user = userEvent.setup();
        render(
            <Page>
                <button>First</button>
                <input type="radio" name="size" aria-label="Large" defaultChecked />
                <input type="radio" name="size" aria-label="Small" />
            </Page>,
        );
        byName('First').focus();

        await user.tab({ shift: true });
        expect(screen.getByLabelText('Large')).toHaveFocus();
    });

    it('uses checkVisibility when the browser has it', async () => {
        const user = userEvent.setup();
        const proto = HTMLElement.prototype as unknown as Record<string, unknown>;
        const original = proto.checkVisibility;
        proto.checkVisibility = function (this: HTMLElement) { return this.textContent !== 'Invisible'; };
        try {
            render(<Page><button>First</button><button>Last</button><button>Invisible</button></Page>);
            byName('Last').focus();

            await user.tab();

            expect(byName('First')).toHaveFocus();
        } finally {
            if (original === undefined) delete proto.checkVisibility;
            else proto.checkVisibility = original;
        }
    });

    it('lets only the innermost of two open traps handle Tab', async () => {
        const user = userEvent.setup();
        render(
            <Dialog>
                <button>Outer</button>
                <Dialog><button>Inner first</button><button>Inner last</button></Dialog>
            </Dialog>,
        );
        byName('Inner last').focus();

        await user.tab();

        expect(byName('Inner first')).toHaveFocus();
    });

    it('does nothing while inactive', async () => {
        const user = userEvent.setup();
        render(<Page active={false}><button>First</button><button>Last</button></Page>);
        byName('Last').focus();

        await user.tab();

        expect(byName('Behind after')).toHaveFocus();
    });
});
