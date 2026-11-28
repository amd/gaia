// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useLayoutEffect, useRef, useState } from 'react';
import { useT } from '../../i18n/useT';

/** A skill's description, cut at one line until the reader asks for the rest. */
export function SkillDescription({ text }: { text: string }) {
    const t = useT();
    const ref = useRef<HTMLSpanElement>(null);
    const [open, setOpen] = useState(false);
    const [cut, setCut] = useState(false);

    useLayoutEffect(() => {
        const el = ref.current;
        if (!el || open) return;
        const measure = () => setCut(el.scrollHeight > el.clientHeight + 1);
        measure();
        if (typeof ResizeObserver === 'undefined') return;
        const ro = new ResizeObserver(measure);
        ro.observe(el);
        return () => ro.disconnect();
    }, [text, open]);

    return (
        <>
            <span
                ref={ref}
                className={`skill-row-desc${open ? ' is-open' : ''}${cut || open ? ' is-toggle' : ''}`}
                dir="auto"
                onClick={cut || open ? (e) => { e.preventDefault(); setOpen(!open); } : undefined}
            >
                <bdi>{text}</bdi>
            </span>
            {(cut || open) && (
                <button type="button" className="skill-row-more" aria-expanded={open} onClick={() => setOpen(!open)}>
                    {t(open ? 'settings.skills.less' : 'settings.skills.more')}
                </button>
            )}
        </>
    );
}
