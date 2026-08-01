// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Regenerates website/src/data/in-development.json from hub/agents/*/python/gaia-agent.yaml.
//
// Why a committed snapshot instead of reading the manifests at build time:
// the Railway deploy runs `railway up` from website/, which uploads ONLY that
// directory as the build context — hub/ is not there. Reading ../hub at build
// time therefore works locally and in CI (full checkout) but fails the deploy.
// The snapshot ships inside website/, and `npm run check:agents` in CI fails if
// it drifts from the manifests, so it cannot rot silently.
//
//   npm run generate:agents    # rewrite the snapshot
//   npm run check:agents       # verify it matches the manifests (CI)

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse as parseYaml } from 'yaml';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(HERE, '..', 'src', 'data', 'in-development.json');
const MANIFEST = path.join('python', 'gaia-agent.yaml');

// Reference material, not products. Agents that self-declare `category: examples`
// (hello-world, word-count, doc-search) are excluded automatically, so a new
// example drops out of the hub list without anyone editing this file.
const EXAMPLE_CATEGORY = 'examples';

// EDITORIAL: the one demo that miscategorises itself — connectors-demo declares
// `productivity` but exists only to demonstrate per-agent connector activation.
// Fix the manifest and this entry can go.
const NOT_PRODUCTS = ['connectors-demo'];

function findHubAgentsDir() {
  let dir = path.resolve(HERE, '..');
  for (;;) {
    const candidate = path.join(dir, 'hub', 'agents');
    if (fs.existsSync(candidate) && fs.statSync(candidate).isDirectory()) return candidate;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new Error(
    `[generate-in-development] Could not find 'hub/agents' walking up from ${HERE}. ` +
      `Run this from a full amd/gaia checkout.`
  );
}

function readManifest(dir, id) {
  const file = path.join(dir, id, MANIFEST);
  const raw = parseYaml(fs.readFileSync(file, 'utf8'));
  const field = (key) => {
    const value = raw?.[key];
    if (typeof value !== 'string' || value.trim() === '') {
      throw new Error(
        `[generate-in-development] ${file} has no usable '${key}'. Every agent manifest ` +
          `must declare id/name/description/category/icon — the hub list renders them ` +
          `verbatim and will not invent a placeholder.`
      );
    }
    return value.trim();
  };
  // Two ids, deliberately. `id` is the DIRECTORY, which manifests disagree with
  // (hub/agents/analyst declares `id: data`). `manifestId` is what the live
  // catalog serves once the agent publishes, so the "in development" filter can
  // drop the row under either name.
  return {
    id,
    manifestId: field('id'),
    name: field('name'),
    description: field('description'),
    category: field('category'),
    icon: field('icon'),
  };
}

export function collectAgents() {
  const dir = findHubAgentsDir();
  const agents = fs
    .readdirSync(dir, { withFileTypes: true })
    .filter((e) => e.isDirectory() && !NOT_PRODUCTS.includes(e.name))
    // hub/agents holds non-agent directories too (e.g. python/); no manifest, no row.
    .filter((e) => fs.existsSync(path.join(dir, e.name, MANIFEST)))
    .map((e) => readManifest(dir, e.name))
    .filter((a) => a.category !== EXAMPLE_CATEGORY)
    .sort((a, b) => a.name.localeCompare(b.name));

  if (agents.length === 0) {
    throw new Error(
      `[generate-in-development] Found ${dir} but no agent manifests inside it (*/${MANIFEST}).`
    );
  }
  return agents;
}

function serialize(agents) {
  return `${JSON.stringify({ agents }, null, 2)}\n`;
}

// Run the CLI only when invoked directly — the test suite imports collectAgents()
// and must not rewrite the snapshot as a side effect of importing this module.
const invokedDirectly =
  process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (invokedDirectly) {
  const next = serialize(collectAgents());
  if (process.argv.includes('--check')) {
    const current = fs.existsSync(OUT) ? fs.readFileSync(OUT, 'utf8') : '';
    if (current !== next) {
      console.error(
        `[generate-in-development] ${path.relative(process.cwd(), OUT)} is STALE.\n` +
          `An agent was added, removed, or renamed under hub/agents/ without regenerating\n` +
          `the Agent Hub's "in development" snapshot. Run:\n\n` +
          `    cd website && npm run generate:agents\n\n` +
          `and commit the result.`
      );
      process.exit(1);
    }
    console.log('[generate-in-development] snapshot is up to date.');
  } else {
    fs.writeFileSync(OUT, next);
    console.log(`[generate-in-development] wrote ${path.relative(process.cwd(), OUT)}`);
  }
}
