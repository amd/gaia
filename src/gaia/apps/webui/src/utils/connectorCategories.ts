// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The Connectors page's groups, in display order. `other` catches a category GAIA has no name for. */
export const CONNECTOR_CATEGORIES = [
    'workspace',
    'communication',
    'productivity',
    'dev-tools',
    'search',
    'data',
    'business',
    'design',
    'other',
] as const;

export type ConnectorCategory = (typeof CONNECTOR_CATEGORIES)[number];

const KNOWN = new Set<string>(CONNECTOR_CATEGORIES);

/** The page group for a connector's catalog `category`. */
export function connectorCategory(category: string | null | undefined): ConnectorCategory {
    return category && KNOWN.has(category) ? (category as ConnectorCategory) : 'other';
}
