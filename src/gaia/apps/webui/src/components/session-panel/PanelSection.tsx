// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, type ReactNode } from 'react';
import { ChevronRight } from 'lucide-react';
import { useSessionPanelStore, type PanelSectionId } from '../../stores/sessionPanelStore';
import { scrollBehavior } from '../../utils/motion';

interface PanelSectionProps {
    id: PanelSectionId;
    title: string;
    /** A short fact shown beside the title, visible even when collapsed. */
    summary?: ReactNode;
    children: ReactNode;
}

/**
 * One collapsible section of the session panel. Its body mounts only while
 * expanded, so a collapsed section fetches nothing.
 */
export function PanelSection({ id, title, summary, children }: PanelSectionProps) {
    const open = useSessionPanelStore((s) => s.sections[id]);
    const setSection = useSessionPanelStore((s) => s.setSection);
    const focus = useSessionPanelStore((s) => s.focus);
    const ref = useRef<HTMLElement>(null);
    const bodyId = `sp-section-${id}`;

    useEffect(() => {
        if (focus?.section === id) ref.current?.scrollIntoView?.({ block: 'start', behavior: scrollBehavior() });
    }, [focus, id]);

    return (
        <section ref={ref} className="sp-section" aria-labelledby={`${bodyId}-title`}>
            <h3 className="sp-section-heading">
                <button
                    type="button"
                    id={`${bodyId}-title`}
                    className="sp-section-head"
                    aria-expanded={open}
                    aria-controls={bodyId}
                    onClick={() => setSection(id, !open)}
                >
                    <ChevronRight size={13} className={`sp-chevron${open ? ' is-open' : ''}`} aria-hidden="true" />
                    <span className="sp-section-title">{title}</span>
                    {summary != null && <span className="sp-section-summary">{summary}</span>}
                </button>
            </h3>
            {open && <div id={bodyId} className="sp-section-body">{children}</div>}
        </section>
    );
}
