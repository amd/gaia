// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Pins the two renderer cleaners against over-deletion.
 *
 * Both used to mangle ordinary answers: any `{...}` whose first 50 chars
 * mentioned "tool"/"thought"/"answer" was deleted (and everything after an
 * unbalanced `{` with it), and any fence tagged with something outside a
 * hand-maintained language list was unwrapped into prose. What must still be
 * removed is a complete agent envelope — nothing else.
 */

import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { MessageBubble } from '../MessageBubble';
import type { Message } from '../../types';

const baseMessage: Message = {
    id: 1,
    session_id: 'sess-1',
    role: 'assistant',
    content: '',
    created_at: '2026-07-16T00:00:00.000Z',
    rag_sources: null,
};

function renderContent(content: string, role: Message['role'] = 'assistant') {
    return render(<MessageBubble message={{ ...baseMessage, role, content }} />);
}

describe('code fences — only a stray 1-2 char tag is unwrapped', () => {
    it.each(['shell', 'console', 'cmd', 'bat', 'jsonc', 'mermaid', 'env', 'http', 'hcl', 'latex'])(
        'keeps a ```%s block as a code block',
        (lang) => {
            const { container } = renderContent(
                '```' + lang + '\n# a comment\n* not a bullet\n<tag>\n```',
            );

            const block = container.querySelector('.code-block');
            expect(block).not.toBeNull();
            expect(container.querySelector('.code-lang')?.textContent).toBe(lang);
            expect(block?.querySelector('pre code')?.textContent).toContain('# a comment');
            expect(block?.querySelector('pre code')?.textContent).toContain('<tag>');
            // Prose rendering would have promoted the comment to a heading.
            expect(container.querySelector('h2')).toBeNull();
        },
    );

    it('keeps an untagged ``` fence as a code block', () => {
        const { container } = renderContent('```\nplain fenced text\n```');

        expect(container.querySelector('.code-block')).not.toBeNull();
        expect(container.querySelector('pre code')?.textContent).toContain('plain fenced text');
    });

    it('still unwraps a bogus 1-char tag (the Qwen-Coder ```i case)', () => {
        const { container } = renderContent('```i\nJust prose, not code.\n```');

        expect(container.querySelector('.code-block')).toBeNull();
        expect(container.textContent).toContain('Just prose, not code.');
    });

    it('keeps a real 1-char language tag', () => {
        const { container } = renderContent('```c\nint main(void) { return 0; }\n```');

        expect(container.querySelector('.code-block')).not.toBeNull();
        expect(container.querySelector('.code-lang')?.textContent).toBe('c');
    });
});

describe('JSON in answers — only a complete envelope is removed', () => {
    it('renders a JSON example that merely has a "tool" key', () => {
        const { container } = renderContent(
            'The record looks like this: {"tool": "hammer", "price": 10} — note the price.',
        );

        expect(container.textContent).toContain('"tool": "hammer"');
        expect(container.textContent).toContain('"price": 10');
        expect(container.textContent).toContain('note the price');
    });

    it('renders a JSON example with a "thought" key', () => {
        const { container } = renderContent('Sample: {"thought": "a diary entry", "author": "Ada"}');

        expect(container.textContent).toContain('a diary entry');
        expect(container.textContent).toContain('Ada');
    });

    it('keeps everything after an unbalanced brace', () => {
        const { container } = renderContent(
            'Schema so far:\n\n{"tool": "search_web"\n\nThe closing brace is missing on purpose.',
        );

        expect(container.textContent).toContain('Schema so far');
        expect(container.textContent).toContain('search_web');
        expect(container.textContent).toContain('The closing brace is missing on purpose.');
    });

    it('holds back an unclosed envelope only while the message is streaming', () => {
        const content = 'Let me check.\n\n{"thought": "look it up", "tool": "sea';

        const streaming = render(
            <MessageBubble message={{ ...baseMessage, content }} isStreaming />,
        );
        expect(streaming.container.textContent).toContain('Let me check.');
        expect(streaming.container.textContent).not.toContain('look it up');
        streaming.unmount();

        const { container } = renderContent(content);
        expect(container.textContent).toContain('look it up');
    });

    it('never holds back a brace that does not open an envelope, even while streaming', () => {
        const { container } = render(
            <MessageBubble message={{ ...baseMessage, content: "Try awk '{print $1' next" }} isStreaming />,
        );

        expect(container.textContent).toContain("awk '{print $1' next");
    });

    it('keeps a shell snippet whose braces do not balance as JSON', () => {
        const { container } = renderContent(
            "Run:\n\n```bash\nfor f in *.txt; do echo ${f}; done\nawk '{print $1}' out.log\n```\n\nThen check out.log.",
        );

        const code = container.querySelector('pre code')?.textContent ?? '';
        expect(code).toContain('for f in *.txt');
        expect(code).toContain("awk '{print $1}'");
        expect(container.textContent).toContain('Then check out.log.');
    });

    it('strips a complete tool-call envelope', () => {
        const { container } = renderContent(
            '{"thought": "I should look this up", "tool": "search_web", "tool_args": {"q": "x"}}',
        );

        expect(container.textContent).not.toContain('search_web');
        expect(container.textContent).not.toContain('tool_args');
    });

    it('extracts the answer from an answer envelope', () => {
        const { container } = renderContent(
            '{"thought": "greet the user", "answer": "Hello there!"}',
        );

        expect(container.textContent).toContain('Hello there!');
        expect(container.textContent).not.toContain('greet the user');
    });

    it('extracts the answer when literal newlines make the envelope unparseable', () => {
        const { container } = renderContent(
            '{"thought": "explain",\n "answer": "line one\nline two"}',
        );

        expect(container.textContent).toContain('line one');
        expect(container.textContent).toContain('line two');
        expect(container.textContent).not.toContain('"thought"');
    });
});

describe('user messages are never cleaned', () => {
    it('renders a pasted tool schema verbatim', () => {
        const content =
            'Why does this fail?\n\n{"thought": "t", "tool": "search_web", "tool_args": {"q": "x"}}';

        const { container } = renderContent(content, 'user');

        expect(container.textContent).toContain('search_web');
        expect(container.textContent).toContain('tool_args');
    });

    it('keeps a pasted ```shell block fenced', () => {
        const { container } = renderContent('```shell\nnpm run build\n```', 'user');

        expect(container.querySelector('.code-block')).not.toBeNull();
        expect(container.querySelector('.code-lang')?.textContent).toBe('shell');
    });
});
