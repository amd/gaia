// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// The steps are condensed from docs/connectors/<id>.mdx and keyed by catalog id.
// These checks fail when either side moves without the other.

import { readFileSync, readdirSync } from 'fs';
import { join, resolve } from 'path';
import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { CONNECTOR_GUIDES, ConnectorGuide, showsFieldHint } from '../ConnectorGuide';
import { enMessages } from '../../i18n/locales/en';
import { ar } from '../../i18n/locales/ar';
import { safeUrlTransform } from '../../utils/markdown';

const GAIA_SRC = resolve(__dirname, '..', '..', '..', '..', '..');
const DOCS = resolve(GAIA_SRC, '..', '..', 'docs', 'connectors');
const CATALOG = join(GAIA_SRC, 'connectors', 'catalog');

const catalogIds = new Set(
    readdirSync(CATALOG)
        .filter((f) => f.endsWith('.py'))
        .flatMap((f) => [...readFileSync(join(CATALOG, f), 'utf8').matchAll(/^\s+id="([^"]+)",$/gm)].map((m) => m[1])),
);

const allSteps = Object.values(CONNECTOR_GUIDES).flat();

describe('ConnectorGuide', () => {
    it('keys every guide by a real catalog connector id', () => {
        expect(catalogIds.size).toBeGreaterThan(5);
        for (const id of Object.keys(CONNECTOR_GUIDES)) expect(catalogIds, id).toContain(id);
    });

    it('has English and Arabic for every step string', () => {
        const en = enMessages as Record<string, string>;
        const arMsgs = ar.messages as Record<string, string>;
        for (const step of allSteps) {
            for (const key of [step.title, step.body, step.link?.label].filter(Boolean) as string[]) {
                expect(en[key], key).toBeTruthy();
                expect(arMsgs[key], key).toBeTruthy();
                expect(arMsgs[key], key).not.toBe(en[key]);
            }
        }
    });

    it('links only to https pages that survive the URL sanitizer', () => {
        for (const step of allSteps) {
            const href = step.link?.href;
            if (!href || href === 'setup_url') continue;
            expect(href).toMatch(/^https:\/\//);
            expect(safeUrlTransform(href)).toBe(href);
        }
    });

    it('asks Discord for every bot permission the setup doc lists', () => {
        const doc = readFileSync(join(DOCS, 'discord.mdx'), 'utf8');
        const step = doc.match(/URL Generator\*\*, tick([\s\S]*?)permissions/)?.[1] ?? '';
        const perms = [...step.matchAll(/\*\*([^*]+)\*\*/g)].map((m) => m[1]);
        expect(perms.length).toBeGreaterThanOrEqual(4);
        for (const p of perms) expect(enMessages['guide.discord.inviteBody']).toContain(p);
    });

    it('opens GitHub with the scopes the setup doc recommends', () => {
        const doc = readFileSync(join(DOCS, 'github.mdx'), 'utf8');
        expect(doc).toMatch(/include \*\*`repo`\*\* and \*\*`read:user`\*\*/);
        const href = CONNECTOR_GUIDES['mcp-github'][0].link!.href;
        expect(new URL(href).searchParams.get('scopes')?.split(',')).toEqual(['repo', 'read:user']);
    });

    it('tells both Microsoft guides to leave the client secret empty, since its hint is hidden', () => {
        for (const id of ['microsoft', 'microsoft_work']) {
            expect(showsFieldHint(id, 'client_secret')).toBe(false);
            const said = CONNECTOR_GUIDES[id].some((s) => s.body && enMessages[s.body as keyof typeof enMessages].includes('Leave Client Secret empty'));
            expect(said, id).toBe(true);
        }
        expect(showsFieldHint('microsoft_work', 'tenant_id')).toBe(true);
    });

    it('keeps field hints for connectors without a guide', () => {
        expect(showsFieldHint('mcp-git', 'anything')).toBe(true);
        expect(showsFieldHint('custom-server', 'API_KEY')).toBe(true);
    });

    it('drops a step button whose link would be unsafe or missing, keeping the step', () => {
        const unsafe = render(<ConnectorGuide connectorId="mcp-slack" setupUrl="javascript:alert(1)" />);
        expect(unsafe.container.querySelectorAll('li')).toHaveLength(4);
        expect(unsafe.container.querySelector('a')).toBeNull();
        unsafe.unmount();
        const missing = render(<ConnectorGuide connectorId="mcp-slack" setupUrl={null} />);
        expect(missing.container.querySelectorAll('li')).toHaveLength(4);
        expect(missing.container.querySelector('a')).toBeNull();
        missing.unmount();
        const none = render(<ConnectorGuide connectorId="mcp-git" />);
        expect(none.container.firstChild).toBeNull();
    });
});
