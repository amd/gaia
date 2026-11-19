// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

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

interface SkillCommandMenuProps {
    menu: SkillCommands;
    onPick: (name: string) => void;
}

/** The skills a typed `/` can run, shown above the composer's text. */
export function SkillCommandMenu({ menu, onPick }: SkillCommandMenuProps) {
    const t = useT();
    if (!menu.open) return null;
    if (menu.status !== 'ready') {
        return (
            <div className="skill-menu" role={menu.status === 'error' ? 'alert' : 'status'}>
                <p className="skill-menu-note">
                    {menu.status === 'error'
                        ? t('composer.skillsFailed', { error: menu.error ?? '' })
                        : t('composer.skillsLoading')}
                </p>
            </div>
        );
    }
    return (
        <div className="skill-menu">
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
