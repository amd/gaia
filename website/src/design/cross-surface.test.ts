// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// The website and the Agent UI both paint a dark code panel, and the status
// hues inside it are claimed to be one shared set of literals. Nothing enforced
// that claim, so the two ambers drifted apart (#e3b341 here vs #E5B567 there)
// while every contrast test on both sides stayed green — each value clears its
// floor, they just are not the same colour.
//
// This reads the Agent UI's stylesheet from the repo and compares the literals
// directly. It is the only cross-tree test in the site's suite; it exists
// because "same assistant" is a claim about equality, not about contrast.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

/** Agent UI token home, relative to this file: website/src/design → repo root. */
const AGENT_UI_TOKENS = fileURLToPath(
  new URL('../../../src/gaia/apps/webui/src/styles/index.css', import.meta.url),
);

const WEBSITE_TOKENS = fileURLToPath(new URL('./tokens.css', import.meta.url));

/** Repo-relative, forward slashes — the spelling a workflow path filter uses. */
const AGENT_UI_TOKENS_REL = 'src/gaia/apps/webui/src/styles/index.css';

const WEBSITE_CI = fileURLToPath(
  new URL('../../../.github/workflows/website-ci.yml', import.meta.url),
);

/**
 * Website role → the Agent UI role it must equal.
 *
 * The status hues inside the code panel, plus the copper the primary button is
 * painted with. The syntax-only colours (purple, cyan) have no Agent UI
 * counterpart by design, and the surface/text roles differ on purpose: the
 * panels are near-black but not the same near-black.
 */
const SHARED: Record<string, string> = {
  '--g-code-accent': '--code-accent',
  '--g-code-green': '--code-success',
  '--g-code-blue': '--code-info',
  '--g-code-amber': '--code-warning',
  '--g-code-danger': '--code-danger',
  // The primary button. Its resting fill, its hover step and the text on top
  // are the same three literals on both surfaces — a reader who clicks Download
  // on the site and Send in the app is looking at one control, not two.
  '--g-accent': '--accent',
  '--g-accent-fill': '--accent-fill',
  '--g-accent-fill2': '--accent-fill-hover',
  '--g-on-accent': '--accent-fill-text',
};

/**
 * Every declaration of `token`, in file order, deduped — `#aabbcc` whichever
 * syntax it was written in (`12 34 56` triplet here, `#AABBCC` there).
 *
 * Reading *all* of them matters because a themed role is declared twice, once
 * per `:root` block. Matching only the first would compare the light values and
 * let a dark-theme drift through, which is the whole class of bug this file
 * exists to catch.
 */
function readValues(source: string, token: string): string[] {
  const pattern = new RegExp(
    `${token}\\s*:\\s*(?:(#[0-9a-fA-F]{6})|(\\d+)\\s+(\\d+)\\s+(\\d+))\\s*;`,
    'g',
  );
  const seen: string[] = [];
  for (const m of source.matchAll(pattern)) {
    const hex = m[1]
      ? m[1].toLowerCase()
      : `#${[m[2], m[3], m[4]].map((n) => Number(n).toString(16).padStart(2, '0')).join('')}`;
    if (!seen.includes(hex)) seen.push(hex);
  }
  return seen;
}

describe('status hues are one set of literals across both surfaces', () => {
  const website = readFileSync(WEBSITE_TOKENS, 'utf8');
  const agentUi = readFileSync(AGENT_UI_TOKENS, 'utf8');

  it('can still find the Agent UI stylesheet', () => {
    // A rename upstream must fail here rather than silently skip every pair.
    expect(agentUi).toContain('--code-accent');
  });

  it.each(Object.entries(SHARED))('%s equals %s', (siteToken, appToken) => {
    const siteValues = readValues(website, siteToken);
    const appValues = readValues(agentUi, appToken);

    expect(siteValues, `${siteToken} is declared nowhere in tokens.css`).not.toEqual([]);
    expect(appValues, `${appToken} is declared nowhere in index.css`).not.toEqual([]);
    // Order is light-then-dark in both files, so this also catches a swap.
    expect(appValues).toEqual(siteValues);
  });
});

// A drift guard that CI never runs is not a guard. This suite only executes in
// Website CI, which is path-filtered — so retuning a hue in the Agent UI alone,
// the exact change the pairs above exist to catch, skipped this file entirely.
describe('the drift guard runs on the tree it reads', () => {
  const workflow = readFileSync(WEBSITE_CI, 'utf8');

  it('reads the file the path filter names', () => {
    // Both sides of the same path: a rename that misses one is the defect.
    expect(AGENT_UI_TOKENS.replace(/\\/g, '/')).toContain(AGENT_UI_TOKENS_REL);
  });

  it('triggers on both pull_request and push', () => {
    const triggers = workflow.split(/^\s*(?:pull_request|push):\s*$/m).slice(1);
    expect(triggers, 'website-ci.yml no longer has two trigger blocks').toHaveLength(2);
    for (const block of triggers)
      expect(block, `add '${AGENT_UI_TOKENS_REL}' to this trigger's paths`)
        .toContain(AGENT_UI_TOKENS_REL);
  });
});
