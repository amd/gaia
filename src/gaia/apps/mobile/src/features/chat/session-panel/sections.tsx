// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ReactNode } from 'react';

import type { MessageKey } from '@webui/i18n/registry';

import { AgentInfoSection } from './AgentInfoSection';
import { AnswerSection } from './AnswerSection';
import { ChangesSection } from './ChangesSection';
import { ContextSection } from './ContextSection';
import { EnvironmentSection } from './EnvironmentSection';
import { OutputsSection } from './OutputsSection';
import type { PanelSectionId } from './panelStore';
import { SkillsSection } from './SkillsSection';

export interface SectionProps {
    sessionId: string;
    /** A reply is running or waiting on a prompt: sections re-read what the agent reports. */
    live: boolean;
    /** Closes the panel, so what a section jumps to is in view. */
    close?: () => void;
}

export interface SectionEntry {
    id: PanelSectionId | 'environment';
    /** 'details' sections sit under the "Details" heading, shown above the first one. */
    group?: 'details';
    render(props: SectionProps): ReactNode;
}

export const DETAILS_HEADING: MessageKey = 'sp.details';

/**
 * The panel's sections in the web's order: This answer, Outputs, Changes, Folders and access,
 * Skills, then the Details group (Context & performance, Agent info). A later task adds
 * its entry at its place in this list; an unbuilt section has no entry, so it shows nothing.
 */
export const SECTIONS: readonly SectionEntry[] = [
    { id: 'answer', render: (p) => <AnswerSection {...p} /> },
    { id: 'outputs', render: (p) => <OutputsSection {...p} /> },
    { id: 'changes', render: (p) => <ChangesSection {...p} /> },
    { id: 'environment', render: (p) => <EnvironmentSection {...p} /> },
    { id: 'skills', render: (p) => <SkillsSection {...p} /> },
    { id: 'context', group: 'details', render: (p) => <ContextSection {...p} /> },
    { id: 'tools', group: 'details', render: (p) => <AgentInfoSection {...p} /> },
];
