// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useLayoutEffect, useRef, useState } from 'react';
import type { SkillCommands } from '../hooks/useSkillCommands';
import { useT } from '../i18n/useT';

export const SKILL_MENU_ID = 'composer-skill-menu';

export function skillOptionId(index: number): string {
    return `${SKILL_MENU_ID}-${index}`;
}

/** A skill description's first sentence: they are written for models and run long. */
function firstSentence(text: string): string {
    const flat = text.replace(/\s+/g, ' ').trim();
    const end = flat.indexOf('. ');
    return end >= 0 ? flat.slice(0, end + 1) : flat;
}

const MAX_HEIGHT = 320;
/** Between the menu and the composer; matches the offset in Composer.css. */
const GAP = 6;
/** Kept clear at the window's edge. */
const EDGE = 8;

export interface SkillMenuPlacement {
    below: boolean;
    maxHeight: number;
}

/**
 * Where the menu goes so all of it stays in the window: above the composer when
 * the list fits there, else on the side with more room, never taller than that room.
 */
export function placeSkillMenu(
    composer: { top: number; bottom: number },
    viewportHeight: number,
    contentHeight: number,
): SkillMenuPlacement {
    const above = Math.max(0, composer.top - GAP - EDGE);
    const below = Math.max(0, viewportHeight - composer.bottom - GAP - EDGE);
    const wanted = Math.min(MAX_HEIGHT, contentHeight);
    const goBelow = above < wanted && below > above;
    return { below: goBelow, maxHeight: Math.floor(Math.min(MAX_HEIGHT, goBelow ? below : above)) };
}

interface SkillCommandMenuProps {
    menu: SkillCommands;
    onPick: (name: string) => void;
}

/** The skills a typed `/` can run, shown above the composer's text, or below it when there is no room above. */
export function SkillCommandMenu({ menu, onPick }: SkillCommandMenuProps) {
    const t = useT();
    const ref = useRef<HTMLDivElement>(null);
    const [placement, setPlacement] = useState<SkillMenuPlacement | null>(null);
    const rows = menu.open && menu.status === 'ready' ? menu.matches.length : -1;

    useLayoutEffect(() => {
        const el = ref.current;
        const composer = el?.parentElement;
        if (!el || !composer) {
            setPlacement(null);
            return undefined;
        }
        const measure = () => {
            const next = placeSkillMenu(composer.getBoundingClientRect(), window.innerHeight, el.scrollHeight);
            setPlacement((was) => (was && was.below === next.below && was.maxHeight === next.maxHeight ? was : next));
        };
        measure();
        window.addEventListener('resize', measure);
        // The message box grows with what is typed or attached, with no window resize.
        const grown = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
        grown?.observe(composer);
        return () => {
            window.removeEventListener('resize', measure);
            grown?.disconnect();
        };
    }, [menu.open, menu.status, rows]);

    // Before paint, so the highlighted row is never drawn scrolled out of the list.
    useLayoutEffect(() => {
        if (rows > 0) document.getElementById(skillOptionId(menu.selected))?.scrollIntoView?.({ block: 'nearest' });
    }, [menu.selected, rows, placement]);

    if (!menu.open) return null;
    const className = `skill-menu${placement?.below ? ' is-below' : ''}`;
    const style = placement ? { maxHeight: placement.maxHeight } : undefined;
    if (menu.status !== 'ready') {
        return (
            <div ref={ref} className={className} style={style} role={menu.status === 'error' ? 'alert' : 'status'}>
                <p className="skill-menu-note">
                    {menu.status === 'error'
                        ? t('composer.skillsFailed', { error: menu.error ?? '' })
                        : t('composer.skillsLoading')}
                </p>
            </div>
        );
    }
    return (
        <div ref={ref} className={className} style={style}>
            <p className="skill-menu-title">{t('composer.skillsTitle')}</p>
            <ul id={SKILL_MENU_ID} role="listbox" aria-label={t('composer.skillsTitle')}>
                {menu.matches.map((skill, i) => (
                    <li
                        key={skill.name}
                        id={skillOptionId(i)}
                        role="option"
                        aria-selected={i === menu.selected}
                        className={`skill-menu-row${i === menu.selected ? ' is-selected' : ''}`}
                        // Keep focus in the composer: a click picks, it doesn't blur.
                        onMouseDown={(e) => e.preventDefault()}
                        onMouseEnter={() => menu.select(i)}
                        onClick={() => onPick(skill.name)}
                    >
                        <span className="skill-menu-name" dir="ltr">/{skill.name}</span>
                        <span className="skill-menu-desc" dir="auto">{firstSentence(skill.description)}</span>
                    </li>
                ))}
            </ul>
        </div>
    );
}
