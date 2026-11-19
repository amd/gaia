// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { ExternalLink } from 'lucide-react';
import { useT } from '../i18n/useT';
import type { MessageKey } from '../i18n/types';
import { safeUrlTransform } from '../utils/markdown';

interface GuideStep {
    title: MessageKey;
    body?: MessageKey;
    link?: { href: string | 'setup_url'; label: MessageKey };
}

/**
 * A novice's way through each connector's outside setup: one short line per
 * step and the one page it needs. The form below the guide is its last step.
 * Condensed from docs/connectors/<id>.mdx, which keeps the edge cases.
 */
export const CONNECTOR_GUIDES: Record<string, GuideStep[]> = {
    google: [
        { title: 'guide.google.project', body: 'guide.google.projectBody', link: { href: 'https://console.cloud.google.com/projectcreate', label: 'guide.google.projectLink' } },
        { title: 'guide.google.apis', body: 'guide.google.apisBody', link: { href: 'https://console.cloud.google.com/flows/enableapi?apiid=gmail.googleapis.com,calendar-json.googleapis.com,drive.googleapis.com', label: 'guide.google.apisLink' } },
        { title: 'guide.google.consent', body: 'guide.google.consentBody', link: { href: 'https://console.cloud.google.com/auth/overview', label: 'guide.google.consentLink' } },
        { title: 'guide.google.client', body: 'guide.google.clientBody', link: { href: 'https://console.cloud.google.com/auth/clients/create', label: 'guide.google.clientLink' } },
        { title: 'guide.pasteBoth' },
    ],
    microsoft: [
        { title: 'guide.microsoft.register', body: 'guide.microsoft.registerBody', link: { href: 'https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade', label: 'guide.microsoft.registerLink' } },
        { title: 'guide.microsoft.redirect', body: 'guide.microsoft.redirectBody' },
        { title: 'guide.microsoft.paste', body: 'guide.microsoft.pasteBody' },
    ],
    microsoft_work: [
        { title: 'guide.microsoft.register', body: 'guide.microsoftWork.registerBody', link: { href: 'https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade', label: 'guide.microsoft.registerLink' } },
        { title: 'guide.microsoft.redirect', body: 'guide.microsoft.redirectBody' },
        { title: 'guide.microsoftWork.paste', body: 'guide.microsoftWork.pasteBody' },
    ],
    'mcp-github': [
        { title: 'guide.github.token', body: 'guide.github.tokenBody', link: { href: 'https://github.com/settings/tokens/new?description=GAIA&scopes=repo,read:user', label: 'guide.github.tokenLink' } },
        { title: 'guide.pasteIt' },
    ],
    'mcp-slack': [
        { title: 'guide.slack.app', body: 'guide.slack.appBody', link: { href: 'setup_url', label: 'guide.slack.appLink' } },
        { title: 'guide.slack.token', body: 'guide.slack.tokenBody' },
        { title: 'guide.slack.invite', body: 'guide.slack.inviteBody' },
        { title: 'guide.pasteIt' },
    ],
    'mcp-discord': [
        { title: 'guide.discord.bot', body: 'guide.discord.botBody', link: { href: 'https://discord.com/developers/applications', label: 'guide.discord.botLink' } },
        { title: 'guide.discord.intent', body: 'guide.discord.intentBody' },
        { title: 'guide.discord.invite', body: 'guide.discord.inviteBody' },
        { title: 'guide.pasteIt' },
    ],
    'mcp-notion': [
        { title: 'guide.notion.integration', body: 'guide.notion.integrationBody', link: { href: 'https://www.notion.so/profile/integrations', label: 'guide.notion.integrationLink' } },
        { title: 'guide.notion.share', body: 'guide.notion.shareBody' },
        { title: 'guide.pasteIt' },
    ],
    'mcp-brave-search': [
        { title: 'guide.brave.key', body: 'guide.brave.keyBody', link: { href: 'https://api-dashboard.search.brave.com/', label: 'guide.brave.keyLink' } },
        { title: 'guide.pasteIt' },
    ],
    'mcp-tavily': [
        { title: 'guide.tavily.key', body: 'guide.tavily.keyBody', link: { href: 'https://app.tavily.com/', label: 'guide.tavily.keyLink' } },
        { title: 'guide.pasteIt' },
    ],
};

/** Field hints a guide doesn't cover, so they stay beside their field. */
const KEPT_HINTS: Record<string, string[]> = {
    microsoft_work: ['tenant_id'],
};

export function hasConnectorGuide(id: string): boolean {
    return id in CONNECTOR_GUIDES;
}

/** Whether a field keeps its own hint: always without a guide, and when the guide leaves it out. */
export function showsFieldHint(connectorId: string, fieldKey: string): boolean {
    return !hasConnectorGuide(connectorId) || (KEPT_HINTS[connectorId] ?? []).includes(fieldKey);
}

/** The numbered steps above a connector's form. Its card's own link opens the full guide. */
export function ConnectorGuide({ connectorId, setupUrl }: { connectorId: string; setupUrl?: string | null }) {
    const t = useT();
    const steps = CONNECTOR_GUIDES[connectorId];
    if (!steps) return null;
    return (
        <div className="connector-guide">
            <ol className="setup-steps">
                {steps.map((step, i) => {
                    const href = step.link && (step.link.href === 'setup_url' ? setupUrl : step.link.href);
                    const safe = href ? safeUrlTransform(href) : '';
                    return (
                        <li key={step.title + i} className="setup-step">
                            <span className="setup-step-number" aria-hidden="true"><bdi dir="ltr">{i + 1}</bdi></span>
                            <div className="setup-step-body">
                                <h4 className="settings-label">{t(step.title)}</h4>
                                {step.body && <p className="settings-hint">{t(step.body)}</p>}
                                {step.link && safe && (
                                    <div>
                                        <a className="btn-secondary setup-step-link" href={safe} target="_blank" rel="noopener noreferrer">
                                            {t(step.link.label)}
                                            <ExternalLink size={12} aria-hidden="true" className="setup-step-link-icon" />
                                        </a>
                                    </div>
                                )}
                            </div>
                        </li>
                    );
                })}
            </ol>
        </div>
    );
}
