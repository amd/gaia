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

/**
 * Website role → the Agent UI role it must equal.
 *
 * Only status hues belong here. The syntax-only colours (purple, cyan) have no
 * Agent UI counterpart by design, and the surface/text roles differ on purpose:
 * the panels are near-black but not the same near-black.
 */
const SHARED: Record<string, string> = {
  '--g-code-accent': '--code-accent',
  '--g-code-green': '--code-success',
  '--g-code-blue': '--code-info',
  '--g-code-amber': '--code-warning',
  '--g-code-danger': '--code-danger',
};

/** `--name: 12 34 56;` → `#0c2238`. Space-separated triplets, Tailwind style. */
function readTriplet(source: string, token: string): string | null {
  const m = new RegExp(`${token}\\s*:\\s*(\\d+)\\s+(\\d+)\\s+(\\d+)\\s*;`).exec(source);
  if (!m) return null;
  return `#${[m[1], m[2], m[3]].map((n) => Number(n).toString(16).padStart(2, '0')).join('')}`;
}

/** `--name: #AABBCC;` → `#aabbcc`. */
function readHex(source: string, token: string): string | null {
  const m = new RegExp(`${token}\\s*:\\s*(#[0-9a-fA-F]{6})\\s*;`).exec(source);
  return m ? m[1].toLowerCase() : null;
}

describe('status hues are one set of literals across both surfaces', () => {
  const website = readFileSync(WEBSITE_TOKENS, 'utf8');
  const agentUi = readFileSync(AGENT_UI_TOKENS, 'utf8');

  it('can still find the Agent UI stylesheet', () => {
    // A rename upstream must fail here rather than silently skip every pair.
    expect(agentUi).toContain('--code-accent');
  });

  it.each(Object.entries(SHARED))('%s equals %s', (siteToken, appToken) => {
    const siteValue = readTriplet(website, siteToken);
    const appValue = readHex(agentUi, appToken);

    expect(siteValue, `${siteToken} is not declared as an RGB triplet`).not.toBeNull();
    expect(appValue, `${appToken} is not declared as a hex literal`).not.toBeNull();
    expect(siteValue).toBe(appValue);
  });
});
