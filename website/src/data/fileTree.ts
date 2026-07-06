// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Build a nested, collapsible tree from the flat package file listing
// (catalog `package.files`), used by the hub agent page's Files tab.

export interface FileTreeNode {
  /** Path segment (file or folder name at this level). */
  name: string;
  isFile: boolean;
  /** File size in bytes, or the aggregate size of everything under a folder. */
  size: number;
  /** File count under a folder (1 for a file). */
  count: number;
  children: FileTreeNode[];
  /** Set on text files that can be previewed — the URL to fetch their content. */
  previewUrl?: string;
}

// Extensions / names we treat as previewable text. Binaries (no text extension,
// e.g. `email-agent-linux-x64`, `.exe`) fail this and stay non-clickable.
const TEXT_EXTS = new Set([
  'md', 'markdown', 'txt', 'json', 'js', 'mjs', 'cjs', 'ts', 'tsx', 'jsx',
  'yaml', 'yml', 'lock', 'map', 'css', 'html', 'htm', 'sh', 'toml', 'xml',
  'ini', 'cfg', 'env',
]);
const TEXT_NAMES = new Set(['license', 'readme', 'changelog', 'authors', 'notice']);

export function isTextFile(name: string): boolean {
  const lower = name.toLowerCase();
  if (TEXT_NAMES.has(lower)) return true;
  const dot = lower.lastIndexOf('.');
  return dot !== -1 && TEXT_EXTS.has(lower.slice(dot + 1));
}

/**
 * Build a sorted (folders-first, then alphabetical) tree from a flat list of
 * `{ name, size_bytes }`. `previewBase` (e.g. a jsdelivr npm URL prefix) is
 * prepended to a text file's full path to make its `previewUrl`; pass null to
 * disable previews (no CDN for non-npm agents).
 */
export function buildFileTree(
  files: { name: string; size_bytes: number }[],
  previewBase: string | null
): FileTreeNode {
  const root: FileTreeNode = { name: '', isFile: false, size: 0, count: 0, children: [] };
  for (const f of files) {
    const parts = f.name.split('/').filter(Boolean);
    let node = root;
    parts.forEach((seg, i) => {
      const isLeaf = i === parts.length - 1;
      let child = node.children.find((c) => c.name === seg && c.isFile === isLeaf);
      if (!child) {
        child = {
          name: seg,
          isFile: isLeaf,
          size: isLeaf ? f.size_bytes : 0,
          count: 0,
          children: [],
        };
        if (isLeaf && previewBase && isTextFile(seg)) {
          child.previewUrl = previewBase + f.name;
        }
        node.children.push(child);
      }
      node = child;
    });
  }
  finalize(root);
  return root;
}

function finalize(n: FileTreeNode): void {
  if (n.isFile) {
    n.count = 1;
    return;
  }
  for (const c of n.children) finalize(c);
  n.size = n.children.reduce((s, c) => s + c.size, 0);
  n.count = n.children.reduce((s, c) => s + c.count, 0);
  n.children.sort((a, b) =>
    a.isFile === b.isFile ? a.name.localeCompare(b.name) : a.isFile ? 1 : -1
  );
}
