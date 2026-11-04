// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useRef, useState, type ReactNode } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { useDialogFocus } from '../useDialogFocus';

function Dialog({
    isOpen = true,
    initial,
    children,
}: {
    isOpen?: boolean;
    initial?: string;
    children: ReactNode;
}) {
    const ref = useRef<HTMLDivElement>(null);
    useDialogFocus(
        ref,
        isOpen,
        initial ? () => ref.current?.querySelector<HTMLElement>(initial) : undefined,
    );
    return (
        <div ref={ref} role="dialog" aria-modal="true" aria-label="Test dialog">
            {children}
        </div>
    );
}

/** A trigger that mounts the dialog, the way DocumentLibrary and FileBrowser open. */
function Page({ dialog }: { dialog: (close: () => void) => ReactNode }) {
    const [open, setOpen] = useState(false);
    return (
        <>
            <button onClick={() => setOpen(true)}>Open</button>
            {open && dialog(() => setOpen(false))}
        </>
    );
}

const byName = (name: string) => screen.getByRole('button', { name });

describe('useDialogFocus', () => {
    it('moves focus to the first tabbable element when the dialog opens', async () => {
        const user = userEvent.setup();
        render(
            <Page
                dialog={() => (
                    <Dialog>
                        <span>Not focusable</span>
                        <button tabIndex={-1}>Skipped</button>
                        <button>Close</button>
                        <button>Other</button>
                    </Dialog>
                )}
            />,
        );

        await user.click(byName('Open'));

        expect(byName('Close')).toHaveFocus();
    });

    it('returns focus to the trigger when the dialog unmounts', async () => {
        const user = userEvent.setup();
        render(
            <Page
                dialog={(close) => (
                    <Dialog>
                        <button onClick={close}>Close</button>
                    </Dialog>
                )}
            />,
        );

        await user.click(byName('Open'));
        await user.click(byName('Close'));

        expect(byName('Open')).toHaveFocus();
    });

    it('returns focus when isOpen turns false on a dialog that stays mounted', async () => {
        const user = userEvent.setup();
        function Mounted() {
            const [open, setOpen] = useState(false);
            return (
                <>
                    <button onClick={() => setOpen(true)}>Open</button>
                    <Dialog isOpen={open}>
                        <button onClick={() => setOpen(false)}>Close</button>
                    </Dialog>
                </>
            );
        }
        render(<Mounted />);

        await user.click(byName('Open'));
        expect(byName('Close')).toHaveFocus();
        await user.click(byName('Close'));

        expect(byName('Open')).toHaveFocus();
    });

    it('does nothing while closed', () => {
        render(
            <>
                <button>Outside</button>
                <Dialog isOpen={false}>
                    <button>Inside</button>
                </Dialog>
            </>,
        );
        byName('Outside').focus();

        expect(byName('Outside')).toHaveFocus();
    });

    it('uses initialFocus when given', async () => {
        const user = userEvent.setup();
        render(
            <Page
                dialog={() => (
                    <Dialog initial=".active">
                        <button>First</button>
                        <button className="active">Active</button>
                    </Dialog>
                )}
            />,
        );

        await user.click(byName('Open'));

        expect(byName('Active')).toHaveFocus();
    });

    it('leaves an autoFocus field inside the dialog focused', async () => {
        const user = userEvent.setup();
        render(
            <Page
                dialog={() => (
                    <Dialog>
                        <button>Close</button>
                        <textarea aria-label="When" autoFocus />
                    </Dialog>
                )}
            />,
        );

        await user.click(byName('Open'));

        expect(screen.getByRole('textbox', { name: 'When' })).toHaveFocus();
    });

    it('focuses the dialog itself when it has nothing tabbable', async () => {
        const user = userEvent.setup();
        render(<Page dialog={() => <Dialog><p>Loading…</p></Dialog>} />);

        await user.click(byName('Open'));

        expect(screen.getByRole('dialog')).toHaveFocus();
    });

    it('does not throw or move focus when the trigger is gone by close time', async () => {
        const user = userEvent.setup();
        function Vanishing() {
            const [open, setOpen] = useState(false);
            const [trigger, setTrigger] = useState(true);
            return (
                <>
                    {trigger && <button onClick={() => setOpen(true)}>Open</button>}
                    <button>Elsewhere</button>
                    {open && (
                        <Dialog>
                            <button onClick={() => { setTrigger(false); setOpen(false); }}>
                                Close
                            </button>
                        </Dialog>
                    )}
                </>
            );
        }
        render(<Vanishing />);

        await user.click(byName('Open'));
        await user.click(byName('Close'));

        expect(screen.queryByRole('button', { name: 'Open' })).toBeNull();
        expect(document.activeElement).not.toBe(byName('Elsewhere'));
    });
});
