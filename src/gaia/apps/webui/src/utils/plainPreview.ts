// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** An answer's markdown as one line of plain text, for previews that are not rendered as markdown. */
export function plainPreview(markdown: string): string {
    return markdown
        .replace(/^\s*(```|~~~).*$/gm, '')
        .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
        .replace(/`([^`]+)`/g, '$1')
        .replace(/\*\*(.+?)\*\*/g, '$1')
        .replace(/(^|[^\w])__(?=\S)(.+?\S)__(?![\w.])/g, '$1$2')
        .replace(/(^|[^\w*])\*(?!\s)(.*?\S)\*(?!\w)/g, '$1$2')
        .replace(/(^|[^\w])_(?![\s_])(.+?)_(?!\w)/g, '$1$2')
        .replace(/^\s{0,3}(#{1,6}\s+|>\s?|[-*+]\s+)/gm, '')
        .replace(/\s+/g, ' ')
        .trim();
}
