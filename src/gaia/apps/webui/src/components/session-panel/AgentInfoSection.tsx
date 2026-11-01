// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Agent info (#440): the tools this chat's agent can use, grouped by where
 * they come from, so the user knows what to ask for. Read from the running
 * agent; before it starts, the server says so and that is what is shown.
 */

import { useCallback, useEffect, useState } from 'react';
import * as api from '../../services/api';
import { expectShape } from '../../utils/responseShape';
import { log } from '../../utils/logger';
import { useTurnEnd } from '../../hooks/useTurnEnd';
import { PanelSection } from './PanelSection';
import type { AgentTool } from '../../types/sessionPanel';
import type { MessageKey } from '../../i18n/registry';
import { useT, type Translate } from '../../i18n/useT';

const NAMED_CATEGORIES: Record<string, MessageKey> = {
    mcp: 'tools.cat.mcp',
    skill: 'tools.cat.skill',
    agent: 'tools.cat.agent',
};

/** A category's heading: a mixin name (`file_io`) reads as `file io`. */
export function categoryLabel(category: string, t: Translate): string {
    const key = NAMED_CATEGORIES[category];
    return key ? t(key) : category.replace(/_/g, ' ');
}

/** Tools grouped by category, groups in the order their first tool appears. */
export function groupTools(tools: AgentTool[]): [string, AgentTool[]][] {
    const groups = new Map<string, AgentTool[]>();
    for (const tool of tools) {
        const group = groups.get(tool.category);
        if (group) group.push(tool);
        else groups.set(tool.category, [tool]);
    }
    return [...groups.entries()];
}

function firstLine(text: string): string {
    return text.split('\n', 1)[0].trim();
}

interface AgentInfoSectionProps {
    sessionId: string;
    busy: boolean;
}

export function AgentInfoSection({ sessionId, busy }: AgentInfoSectionProps) {
    const t = useT();
    return (
        <PanelSection id="tools" title={t('tools.title')}>
            <ToolList sessionId={sessionId} busy={busy} />
        </PanelSection>
    );
}

type Failure = { notRunning: boolean; text: string };

function ToolList({ sessionId, busy }: AgentInfoSectionProps) {
    const t = useT();
    const [tools, setTools] = useState<AgentTool[] | null>(null);
    const [failure, setFailure] = useState<Failure | null>(null);

    const load = useCallback(() => {
        let live = true;
        Promise.resolve()
            .then(() => api.getCapabilities(sessionId))
            .then((res) => {
                if (!live) return;
                setTools(expectShape(res, (r) => Array.isArray(r.tools), 'list of tools').tools);
                setFailure(null);
            })
            .catch((err) => {
                if (!live) return;
                const status = (err as { httpStatus?: number }).httpStatus;
                const detail = (err as { detail?: string }).detail;
                log.ui.warn(`Could not list the tools of session=${sessionId}`, err);
                setTools(null);
                setFailure({
                    notRunning: status === 409,
                    text: detail || (err instanceof Error ? err.message : String(err)),
                });
            });
        return () => { live = false; };
    }, [sessionId]);
    useEffect(load, [load]);
    // The agent starts with the first reply, and a reply can load a skill's tools.
    useTurnEnd(busy, load);

    if (failure?.notRunning) return <p className="sp-meta" role="status">{failure.text}</p>;
    if (failure) return <p className="sp-error" role="alert">{t('tools.listError', { error: failure.text })}</p>;
    if (!tools) return <p className="sp-meta">{t('sp.checking')}</p>;
    if (tools.length === 0) return <p className="sp-meta">{t('tools.empty')}</p>;

    return (
        <div className="sp-tools" aria-label={t('tools.listLabel')} role="group">
            {groupTools(tools).map(([category, group]) => {
                const label = categoryLabel(category, t);
                return (
                    <section key={category} className="sp-tool-group" aria-label={label}>
                        <h4 className="sp-tool-group-title">
                            {label} <span className="sp-meta">{group.length}</span>
                        </h4>
                        <ul className="sp-list">
                            {group.map((tool) => (
                                <li key={tool.name} className="sp-tool">
                                    <code className="sp-item-name" dir="ltr">{tool.name}</code>
                                    {tool.description && (
                                        <span className="sp-meta" title={tool.description}>{firstLine(tool.description)}</span>
                                    )}
                                </li>
                            ))}
                        </ul>
                    </section>
                );
            })}
        </div>
    );
}
