// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import ReactMarkdown from 'react-markdown';

// Question text can carry tool-derived content: formatting only. A link,
// image or heading is unwrapped to its text, and raw HTML stays literal.
const ALLOWED = ['p', 'strong', 'em', 'code', 'ul', 'ol', 'li', 'br'];

/** A question's text with the model's Markdown shown as formatting. */
export function QuestionText({ text }: { text: string }) {
    return (
        <div className="render-needs-input__question" dir="auto">
            <ReactMarkdown allowedElements={ALLOWED} unwrapDisallowed>
                {text}
            </ReactMarkdown>
        </div>
    );
}
