// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import ReactMarkdown, { type Components } from 'react-markdown';

// Question text can carry tool-derived content: formatting only. A link,
// image or heading is unwrapped to its text, and raw HTML stays literal.
const ALLOWED = ['p', 'strong', 'em', 'code', 'ul', 'ol', 'li', 'br'];

// Each block follows the page's direction; its text is isolated, so an English
// question in an Arabic UI still starts at the reading edge.
const ISOLATED: Components = {
    p: ({ children }) => <p><bdi>{children}</bdi></p>,
    // A list item holding paragraphs or a nested list leaves the isolating to them.
    li: ({ node, children }) => (node?.children.some((c) => c.type === 'element' && ['p', 'ul', 'ol'].includes(c.tagName))
        ? <li>{children}</li>
        : <li><bdi>{children}</bdi></li>),
};

/** A question's text with the model's Markdown shown as formatting. */
export function QuestionText({ text }: { text: string }) {
    return (
        <div className="render-needs-input__question">
            <ReactMarkdown allowedElements={ALLOWED} unwrapDisallowed components={ISOLATED}>
                {text}
            </ReactMarkdown>
        </div>
    );
}
