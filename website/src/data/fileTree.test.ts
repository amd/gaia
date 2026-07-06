// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { buildFileTree, isTextFile, type FileTreeNode } from './fileTree';

const CDN = 'https://cdn.jsdelivr.net/npm/@amd-gaia/agent-email@0.2.4/';

function child(node: FileTreeNode, name: string): FileTreeNode {
  const c = node.children.find((x) => x.name === name);
  if (!c) throw new Error(`no child '${name}' in [${node.children.map((x) => x.name)}]`);
  return c;
}

describe('isTextFile', () => {
  it('accepts text extensions and known doc names', () => {
    for (const n of ['README.md', 'cli.js', 'types.d.ts', 'package.json', 'gaia-agent.yaml', 'LICENSE', 'cli.js.map']) {
      expect(isTextFile(n), n).toBe(true);
    }
  });
  it('rejects binaries (no text extension)', () => {
    for (const n of ['email-agent-linux-x64', 'email-agent-win32-x64.exe', 'model.gguf', 'logo.png']) {
      expect(isTextFile(n), n).toBe(false);
    }
  });
});

describe('buildFileTree', () => {
  const files = [
    { name: 'binaries/email-agent-linux-x64', size_bytes: 100 },
    { name: 'binaries/email-agent-win32-x64.exe', size_bytes: 200 },
    { name: 'dist/cli.js', size_bytes: 10 },
    { name: 'README.md', size_bytes: 5 },
  ];

  it('groups into folders with aggregated size + count', () => {
    const root = buildFileTree(files, CDN);
    const bin = child(root, 'binaries');
    expect(bin.isFile).toBe(false);
    expect(bin.count).toBe(2);
    expect(bin.size).toBe(300);
    expect(child(root, 'dist').size).toBe(10);
  });

  it('sorts folders before files, each alphabetical', () => {
    const root = buildFileTree(files, CDN);
    expect(root.children.map((c) => c.name)).toEqual(['binaries', 'dist', 'README.md']);
  });

  it('gives text files a CDN previewUrl but binaries none', () => {
    const root = buildFileTree(files, CDN);
    expect(child(root, 'README.md').previewUrl).toBe(`${CDN}README.md`);
    expect(child(child(root, 'dist'), 'cli.js').previewUrl).toBe(`${CDN}dist/cli.js`);
    // binaries are not text → not previewable
    expect(child(child(root, 'binaries'), 'email-agent-linux-x64').previewUrl).toBeUndefined();
    expect(child(child(root, 'binaries'), 'email-agent-win32-x64.exe').previewUrl).toBeUndefined();
  });

  it('disables previews entirely when previewBase is null (non-npm agent)', () => {
    const root = buildFileTree(files, null);
    expect(child(root, 'README.md').previewUrl).toBeUndefined();
    expect(child(child(root, 'dist'), 'cli.js').previewUrl).toBeUndefined();
  });

  it('handles an empty listing', () => {
    const root = buildFileTree([], CDN);
    expect(root.children).toEqual([]);
    expect(root.count).toBe(0);
  });
});
