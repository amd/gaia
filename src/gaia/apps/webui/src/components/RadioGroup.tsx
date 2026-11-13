// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useLayoutEffect, useRef, type HTMLAttributes, type KeyboardEvent } from 'react';

const radiosOf = (group: HTMLElement) =>
    [...group.querySelectorAll<HTMLElement>('[role="radio"]')].filter((r) => r.closest('[role="radiogroup"]') === group);

const usable = (r: HTMLElement) => !r.matches(':disabled') && r.getAttribute('aria-disabled') !== 'true';

/**
 * A radiogroup of role="radio" buttons that keys like native radios: one Tab stop
 * (the checked option), arrow keys move and select, Home and End jump to the ends.
 */
export function RadioGroup({ children, ...rest }: Omit<HTMLAttributes<HTMLDivElement>, 'role' | 'onKeyDown'>) {
    const ref = useRef<HTMLDivElement>(null);

    useLayoutEffect(() => {
        const radios = radiosOf(ref.current!);
        const stop = radios.find((r) => r.getAttribute('aria-checked') === 'true' && usable(r)) ?? radios.find(usable);
        for (const r of radios) r.tabIndex = r === stop ? 0 : -1;
    });

    const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
        const radios = radiosOf(e.currentTarget).filter(usable);
        const at = radios.indexOf(document.activeElement as HTMLElement);
        if (at < 0) return;
        const back = getComputedStyle(e.currentTarget).direction === 'rtl' ? 'ArrowRight' : 'ArrowLeft';
        const next = e.key === 'Home' ? 0
            : e.key === 'End' ? radios.length - 1
            : e.key === 'ArrowUp' || e.key === back ? at - 1
            : e.key === 'ArrowDown' || e.key === 'ArrowRight' || e.key === 'ArrowLeft' ? at + 1
            : null;
        if (next === null) return;
        e.preventDefault();
        const target = radios[(next + radios.length) % radios.length];
        target.focus();
        if (target.getAttribute('aria-checked') !== 'true') target.click();
    };

    return (
        <div ref={ref} role="radiogroup" onKeyDown={onKeyDown} {...rest}>
            {children}
        </div>
    );
}
