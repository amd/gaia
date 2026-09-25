// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect } from 'vitest';
import {
    formatBytes,
    isInstalling,
    displayVersion,
    mergeCatalogStatus,
    splitAvailable,
    countUpdates,
    installedTabLabel,
} from '../agentHub';
import type { AgentInfo } from '../../types';

function agent(partial: Partial<AgentInfo> & { id: string }): AgentInfo {
    return {
        name: partial.id,
        description: '',
        source: 'installed',
        conversation_starters: [],
        models: [],
        ...partial,
    };
}

describe('formatBytes', () => {
    it('returns an em dash for missing/zero/invalid sizes', () => {
        expect(formatBytes(undefined)).toBe('—');
        expect(formatBytes(null)).toBe('—');
        expect(formatBytes(0)).toBe('—');
        expect(formatBytes(-5)).toBe('—');
        expect(formatBytes(NaN)).toBe('—');
    });

    it('formats bytes and KB without decimals', () => {
        expect(formatBytes(512)).toBe('512 B');
        expect(formatBytes(2048)).toBe('2 KB');
    });

    it('formats MB and GB with one decimal', () => {
        expect(formatBytes(1.4 * 1024 * 1024 * 1024)).toBe('1.4 GB');
        expect(formatBytes(5 * 1024 * 1024)).toBe('5.0 MB');
    });
});

describe('isInstalling', () => {
    it('is true for in-flight wire states', () => {
        for (const state of ['downloading', 'verifying', 'installing'] as const) {
            expect(isInstalling({ agent_id: 'x', state, progress: 10 })).toBe(true);
        }
    });

    it('is false for terminal states and missing status', () => {
        expect(isInstalling({ agent_id: 'x', state: 'installed', progress: 100 })).toBe(false);
        expect(isInstalling({ agent_id: 'x', state: 'failed', progress: 0 })).toBe(false);
        expect(isInstalling(undefined)).toBe(false);
        expect(isInstalling(null)).toBe(false);
    });
});

describe('displayVersion', () => {
    // Fixtures use only the fields GET /api/agents/catalog actually sends.
    it('prefers the installed version straight off the wire', () => {
        expect(displayVersion(agent({
            id: 'email', installed_version: '0.6.0', latest_version: '0.7.0',
        }))).toBe('0.6.0');
    });

    it('falls back to the merged display version', () => {
        expect(displayVersion(agent({ id: 'email', version: '0.6.0' }))).toBe('0.6.0');
    });

    it('falls back to the offered version for a not-yet-installed agent', () => {
        expect(displayVersion(agent({ id: 'email', latest_version: '0.6.0' }))).toBe('0.6.0');
    });

    it('is undefined when the catalog sent no version at all', () => {
        expect(displayVersion(agent({ id: 'local-only' }))).toBeUndefined();
    });

    // Registry / entry-point / editable dev installs report no
    // installed_version (gaia.hub.catalog). Badging those with the catalog's
    // latest_version would show a version the user does not actually have.
    it('shows no version for an installed agent whose version is unknown', () => {
        expect(displayVersion(agent({
            id: 'dev-install', status: 'installed', latest_version: '0.7.0',
        }))).toBeUndefined();
    });

    it('shows no version for an update_available agent whose version is unknown', () => {
        expect(displayVersion(agent({
            id: 'dev-install', status: 'update_available', latest_version: '0.7.0',
        }))).toBeUndefined();
    });

    it('still offers latest_version on a not-yet-installed catalog entry', () => {
        expect(displayVersion(agent({
            id: 'new', status: 'available', latest_version: '0.7.0',
        }))).toBe('0.7.0');
    });
});

describe('mergeCatalogStatus', () => {
    // Regression test for #2970/#3784: GET /api/agents/catalog (see
    // gaia.hub.catalog.merge_with_registry) never sends a "version" key — only
    // "installed_version" and "latest_version". These fixtures deliberately
    // mirror that real wire shape (no `.version` set on the catalog side) so
    // this test fails loudly if the mapping is ever dropped again, instead of
    // passing against a hand-built shape the backend never produces.
    it('marks installed agents with a newer catalog version as update_available', () => {
        const installed = [agent({ id: 'chat', version: '0.1.0' })];
        const catalog = [
            agent({ id: 'chat', status: 'update_available', installed_version: '0.1.0', latest_version: '0.2.0' }),
        ];
        const merged = mergeCatalogStatus(installed, catalog);
        expect(merged[0].status).toBe('update_available');
        expect(merged[0].latest_version).toBe('0.2.0');
    });

    it('marks matched-version agents as installed', () => {
        const installed = [agent({ id: 'chat' })];
        const catalog = [
            agent({ id: 'chat', status: 'installed', installed_version: '0.2.0', latest_version: '0.2.0' }),
        ];
        const merged = mergeCatalogStatus(installed, catalog);
        expect(merged[0].status).toBe('installed');
        expect(merged[0].version).toBe('0.2.0');
    });

    it('wires the real installed_version wire field onto the display version', () => {
        // No `.version` anywhere in this fixture — only the real wire fields.
        // If mergeCatalogStatus ever goes back to reading `cat.version`, this
        // assertion fails instead of silently passing.
        const installed = [agent({ id: 'email' })];
        const catalog = [
            agent({ id: 'email', status: 'installed', installed_version: '0.6.0', latest_version: '0.6.0' }),
        ];
        const merged = mergeCatalogStatus(installed, catalog);
        expect(merged[0].version).toBe('0.6.0');
    });

    it('leaves agents absent from the catalog untouched', () => {
        const installed = [agent({ id: 'local-only' })];
        const merged = mergeCatalogStatus(installed, []);
        expect(merged[0]).toEqual(installed[0]);
    });
});

describe('splitAvailable', () => {
    it('returns available agents not already installed', () => {
        const catalog = [
            agent({ id: 'new', status: 'available' }),
            agent({ id: 'chat', status: 'available' }),
            agent({ id: 'installed-elsewhere', status: 'installed' }),
        ];
        const result = splitAvailable(catalog, new Set(['chat']));
        expect(result.map((a) => a.id)).toEqual(['new']);
    });

    it('excludes update_available agents (handled on the Installed tab)', () => {
        const catalog = [agent({ id: 'chat', status: 'update_available' })];
        const result = splitAvailable(catalog, new Set(['chat']));
        expect(result).toEqual([]);
    });
});

describe('countUpdates', () => {
    it('counts update_available agents', () => {
        const agents = [
            agent({ id: 'a', status: 'update_available' }),
            agent({ id: 'b', status: 'installed' }),
            agent({ id: 'c', status: 'update_available' }),
        ];
        expect(countUpdates(agents)).toBe(2);
    });
});

describe('installedTabLabel', () => {
    it('omits the update suffix when none pending', () => {
        expect(installedTabLabel(3, 0)).toBe('Installed (3)');
    });

    it('uses singular/plural for updates', () => {
        expect(installedTabLabel(3, 1)).toBe('Installed (3) · 1 update');
        expect(installedTabLabel(3, 2)).toBe('Installed (3) · 2 updates');
    });
});
