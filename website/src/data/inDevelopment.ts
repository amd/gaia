// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// The "in development" half of the Agent Hub list.
//
// Only agents in the live catalog are published; everything else lives in the
// repo under hub/agents/<id>/ and is not installable yet. Rather than curate a
// list that rots, these rows are derived from the agent manifests themselves —
// but via a COMMITTED SNAPSHOT (in-development.json), not a build-time read of
// ../hub.
//
// The reason is the deploy: `railway up` runs from website/ and uploads only
// that directory, so hub/ does not exist in the production build context.
// Reading it at build time passes locally and in CI (full checkout) and then
// fails the deploy — the worst failure shape. The snapshot ships inside
// website/; `npm run check:agents` in CI fails if it drifts from the manifests,
// so it cannot go stale silently.
//
// Regenerate with: cd website && npm run generate:agents
//
// The snapshot carries no version and no size — nothing trustworthy exists
// before a release, which is exactly why the design shows neither.

import snapshot from './in-development.json';

export interface InDevelopmentAgent {
  /** Directory name under hub/agents/. */
  id: string;
  /** The manifest's own `id` — what the catalog serves once published. */
  manifestId: string;
  name: string;
  description: string;
  category: string;
  icon: string;
}

const ALL = snapshot.agents as InDevelopmentAgent[];

if (ALL.length === 0) {
  throw new Error(
    `[inDevelopment] src/data/in-development.json is empty. Shipping the Agent Hub ` +
      `with no "in development" section would silently claim GAIA has nothing coming. ` +
      `Regenerate it from a full checkout: cd website && npm run generate:agents`
  );
}

/**
 * Agents that exist in `hub/agents/` but are not in the published catalog,
 * sorted by name. Pass the ids of every published agent so a freshly published
 * one leaves this list the moment the catalog picks it up.
 *
 * Matched on BOTH ids: the catalog keys on the manifest `id`, this snapshot on
 * the directory, and they disagree for some agents — matching only one would
 * render a just-published agent in the published grid and here at once.
 */
export function getInDevelopmentAgents(publishedIds: Iterable<string>): InDevelopmentAgent[] {
  const published = new Set(publishedIds);
  return ALL.filter((a) => !published.has(a.id) && !published.has(a.manifestId));
}
