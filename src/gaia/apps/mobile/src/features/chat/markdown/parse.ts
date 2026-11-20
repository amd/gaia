// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { PhrasingContent, Root, RootContent } from 'mdast';
import remarkGfm from 'remark-gfm';
import remarkParse from 'remark-parse';
import { unified } from 'unified';

import { cleanToolCallContent } from '@webui/utils/toolCallContent';
import remarkWindowsPaths from '@webui/utils/remarkWindowsPaths';
import { remarkSoftBreaks } from '@webui/utils/remarkSoftBreaks';

// The same plugins, in the same order, as the web UI's answer renderer.
const processor = unified().use(remarkParse).use(remarkGfm).use(remarkWindowsPaths).use(remarkSoftBreaks);

/** The answer's Markdown tree, after the tool-call JSON and think tags are cleaned out. */
export function parseAnswer(text: string, streaming: boolean): Root {
    const cleaned = cleanToolCallContent(text, streaming);
    return processor.runSync(processor.parse(cleaned), { value: cleaned }) as Root;
}

function formattingInline(nodes: PhrasingContent[]): PhrasingContent[] {
    return nodes.flatMap((node): PhrasingContent[] => {
        switch (node.type) {
            case 'link':
            case 'linkReference':
            case 'delete':
                return formattingInline(node.children);
            case 'image':
            case 'imageReference':
            case 'footnoteReference':
                return [];
            case 'html':
                return [{ type: 'text', value: node.value }];
            case 'strong':
            case 'emphasis':
                return [{ ...node, children: formattingInline(node.children) }];
            case 'text':
            case 'inlineCode':
            case 'break':
                return [node];
            default:
                // Only allowed types pass, so a new mdast type can't slip a link through.
                return [];
        }
    });
}

function formattingBlocks(nodes: RootContent[]): RootContent[] {
    return nodes.flatMap((node): RootContent[] => {
        switch (node.type) {
            case 'paragraph':
                return [{ ...node, children: formattingInline(node.children) }];
            case 'heading':
                return [{ type: 'paragraph', children: formattingInline(node.children) }];
            case 'html':
                return [{ type: 'paragraph', children: [{ type: 'text', value: node.value }] }];
            case 'blockquote':
                return formattingBlocks(node.children);
            case 'table':
                return node.children.map((row) => ({
                    type: 'paragraph' as const,
                    children: formattingInline(row.children.flatMap((cell, i) => [...(i > 0 ? [{ type: 'text' as const, value: ' | ' }] : []), ...cell.children])),
                }));
            case 'list':
                return [
                    {
                        ...node,
                        children: node.children.map((item) => ({ ...item, checked: null, children: formattingBlocks(item.children) as typeof item.children })),
                    },
                ];
            case 'code':
                return [node];
            default:
                return [];
        }
    });
}

/** The formatting-only tree: paragraphs, emphasis, strong, inline code, lists and code blocks. Links, images and headings are unwrapped here, and HTML becomes literal text. */
export function parseFormatting(text: string): Root {
    const tree = parseAnswer(text, false);
    return { ...tree, children: formattingBlocks(tree.children) };
}
