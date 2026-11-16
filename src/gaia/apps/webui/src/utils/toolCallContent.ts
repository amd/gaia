// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Cleans an assistant answer of tool-call JSON, think tags and bogus fences before it is rendered. */

/**
 * Safety-net regex to strip raw tool-call JSON from rendered message content.
 *
 * Primary filtering happens server-side in sse_handler.py (see _TOOL_CALL_JSON_RE
 * and _TOOL_CALL_JSON_SUB_RE). This frontend regex is a secondary safety net for
 * messages that were persisted before the backend filter was in place, or in case
 * any tool-call JSON leaks through. Keep in sync with the server-side pattern.
 */
const TOOL_CALL_JSON_RE = /\s*\{\s*"?tool"?\s*:\s*"[^"]+"\s*,\s*"?tool_args"?\s*:\s*\{[^}]*\}\s*\}/g;

/**
 * Keys that belong to the agent's JSON envelope. An object made up only of
 * these is machinery; an object that mixes them with anything else is content.
 */
const ENVELOPE_KEYS = new Set(['thought', 'answer', 'tool', 'tool_args', 'goal']);

/** An envelope always opens with one of its own keys — cheap gate before parsing. */
const ENVELOPE_OPENER_RE = /^\{\s*"(thought|answer|tool|tool_args|goal)"\s*:/;

/** Index of the `}` matching the `{` at `start`, or -1 if unbalanced. Braces inside JSON strings don't count. */
function findMatchingBrace(text: string, start: number): number {
    let depth = 0;
    let inString = false;
    let escaped = false;
    for (let i = start; i < text.length; i++) {
        const ch = text[i];
        if (inString) {
            if (escaped) escaped = false;
            else if (ch === '\\') escaped = true;
            else if (ch === '"') inString = false;
            continue;
        }
        if (ch === '"') inString = true;
        else if (ch === '{') depth++;
        else if (ch === '}') {
            depth--;
            if (depth === 0) return i;
        }
    }
    return -1;
}

/**
 * Pull the `answer` string out of a block JSON.parse rejected — models emit
 * literal newlines inside string values, which is not valid JSON.
 * Returns null when the block carries no `answer` field.
 */
function extractAnswerField(block: string): string | null {
    const answerKeyIdx = block.indexOf('"answer"');
    if (answerKeyIdx === -1) return null;
    const colonIdx = block.indexOf(':', answerKeyIdx + 8);
    if (colonIdx === -1) return null;
    let start = colonIdx + 1;
    while (start < block.length && /\s/.test(block[start])) start++;
    if (start >= block.length || block[start] !== '"') return null;
    let content = block.slice(start + 1);
    if (content.endsWith('"}')) content = content.slice(0, -2);
    else if (content.endsWith('"')) content = content.slice(0, -1);
    return content.replace(/\\"/g, '"').replace(/\\n/g, '\n').replace(/\\\\/g, '\\');
}

type EnvelopeVerdict =
    | { action: 'drop' }
    | { action: 'answer'; text: string }
    | { action: 'keep' };

/**
 * Decide what a `{...}` region is. Only a complete agent envelope is removed or
 * unwrapped; anything a user or model could have written as a JSON example —
 * including one with a `tool` key — is kept verbatim.
 */
function classifyEnvelope(block: string): EnvelopeVerdict {
    let parsed: unknown;
    try {
        parsed = JSON.parse(block);
    } catch {
        const answer = extractAnswerField(block);
        if (answer !== null) return { action: 'answer', text: answer };
        // A malformed tool call is still machinery; anything else stays visible.
        if (block.includes('"tool_args"')) return { action: 'drop' };
        return { action: 'keep' };
    }

    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        return { action: 'keep' };
    }
    const obj = parsed as Record<string, unknown>;
    const keys = Object.keys(obj);
    const envelopeOnly = keys.length > 0 && keys.every((k) => ENVELOPE_KEYS.has(k));

    if (typeof obj.tool === 'string' && obj.tool_args !== undefined) return { action: 'drop' };
    if (typeof obj.answer === 'string' && envelopeOnly) return { action: 'answer', text: obj.answer };
    if (typeof obj.thought === 'string' && obj.answer === undefined && envelopeOnly) {
        return { action: 'drop' };
    }
    return { action: 'keep' };
}

/**
 * Remove agent JSON envelopes from assistant output, keeping their `answer` text.
 *
 * Primary filtering is server-side (sse_handler.py); this is the safety net for
 * history persisted before that filter existed. A finished message is never
 * truncated: an unbalanced `{` (a shell one-liner, a code snippet) emits the
 * remaining text verbatim. Only while streaming is an unclosed envelope held
 * back, so a tool call does not flash on screen before its closing brace lands.
 */
function cleanLLMJsonBlocks(text: string, streaming: boolean): string {
    let result = '';
    let i = 0;

    while (i < text.length) {
        const braceIdx = text.indexOf('{', i);
        if (braceIdx === -1) {
            result += text.slice(i);
            break;
        }
        if (!ENVELOPE_OPENER_RE.test(text.slice(braceIdx, braceIdx + 40))) {
            result += text.slice(i, braceIdx + 1);
            i = braceIdx + 1;
            continue;
        }
        const closeIdx = findMatchingBrace(text, braceIdx);
        if (closeIdx === -1) {
            result += streaming ? text.slice(i, braceIdx) : text.slice(i);
            break;
        }
        result += text.slice(i, braceIdx);

        const block = text.slice(braceIdx, closeIdx + 1);
        const verdict = classifyEnvelope(block);
        if (verdict.action === 'answer') result += verdict.text;
        else if (verdict.action === 'keep') result += block;
        i = closeIdx + 1;
    }
    return result;
}

/**
 * Real language tags. Only consulted for 1-2 character tags, so genuine short
 * languages (c, r, go, js) keep their fence while a stray ```i does not.
 */
const KNOWN_CODE_LANGS = new Set([
    'python', 'py', 'javascript', 'js', 'typescript', 'ts', 'java', 'c', 'cpp',
    'csharp', 'cs', 'go', 'rust', 'ruby', 'rb', 'php', 'swift', 'kotlin',
    'scala', 'r', 'perl', 'lua', 'bash', 'sh', 'zsh', 'powershell', 'ps1',
    'sql', 'html', 'css', 'scss', 'sass', 'less', 'xml', 'json', 'yaml',
    'yml', 'toml', 'ini', 'csv', 'markdown', 'md', 'dockerfile', 'docker',
    'makefile', 'cmake', 'nginx', 'apache', 'graphql', 'proto', 'protobuf',
    'jsx', 'tsx', 'vue', 'svelte', 'dart', 'elixir', 'ex', 'erlang',
    'haskell', 'hs', 'ocaml', 'ml', 'fsharp', 'fs', 'clojure', 'clj',
    'lisp', 'scheme', 'racket', 'zig', 'nim', 'crystal', 'julia',
    'matlab', 'octave', 'fortran', 'cobol', 'pascal', 'delphi', 'ada',
    'assembly', 'asm', 'nasm', 'wasm', 'solidity', 'sol', 'verilog', 'vhdl',
    'text', 'txt', 'plaintext', 'diff', 'patch', 'log',
    // Retired fence-payload tags (#2109 cutover): cards now arrive via
    // tool_result.render, never fences. Kept ONLY so pre-cutover session
    // history shows those payloads as fenced JSON code blocks, not bare prose.
    'email_pre_scan', 'email-pre-scan',
]);

/**
 * Unwrap fences whose language tag is obvious garbage.
 *
 * Local LLMs (especially Qwen-Coder) sometimes wrap prose in a fence tagged
 * with a single stray letter (```i, ```a). Only those are unwrapped: a tag of
 * 1-2 characters that is not a real language. Every other tag keeps its fence,
 * including an empty one — a tag list can never keep up with what people write
 * (shell, console, mermaid, jsonc, env, hcl), and unwrapping a real block turns
 * `# comment` into a heading and swallows `<tag>`.
 */
function stripBogusCodeFences(text: string): string {
    return text.replace(
        /```(\w*)[ \t]*\n([\s\S]*?)```/g,
        (match, lang: string, inner: string) => {
            const langLower = lang.toLowerCase();
            const isStrayLetter =
                langLower.length > 0 && langLower.length <= 2 && !KNOWN_CODE_LANGS.has(langLower);
            return isStrayLetter ? inner.trim() : match;
        },
    );
}

/**
 * A trailing "Verification: unverified ..." line is the agent's no-op scope
 * note on a turn that ran no checks — the same disclaimer under every chat
 * answer. "verified" / "partially verified" lines report real check results
 * and stay. Mirrors the TUI's StripVerificationScope.
 */
const UNVERIFIED_SCOPE_RE = /\n{1,2}Verification: unverified[^\n]*\s*$/;

function stripUnverifiedScope(content: string): string {
    let text = content;
    for (;;) {
        const stripped = text.replace(UNVERIFIED_SCOPE_RE, '');
        if (stripped === text) return text;
        text = stripped;
    }
}

export function cleanToolCallContent(content: string, streaming = false): string {
    if (!content) return content;
    content = stripUnverifiedScope(content);
    let cleaned = content;

    // Remove all tool-call JSON blocks from the content
    cleaned = cleaned.replace(TOOL_CALL_JSON_RE, '');

    // Remove/extract LLM JSON blocks (thought, answer, tool) from output.
    // These have nested braces so we use a brace-depth parser instead of regex.
    // Blocks with "answer" have their answer text extracted; others are removed.
    cleaned = cleanLLMJsonBlocks(cleaned, streaming);

    // Remove <think>...</think> tags that some models output
    cleaned = cleaned.replace(/<think>[\s\S]*?<\/think>/g, '');

    // Fix double-escaped newlines/tabs from LLM output.
    // Some models output literal "\n" (two chars) instead of actual newlines,
    // which breaks markdown rendering. Only unescape when there are many
    // literal \n sequences compared to real newlines (avoids breaking code blocks).
    const literalNewlines = (cleaned.match(/\\n/g) || []).length;
    const realNewlines = (cleaned.match(/\n/g) || []).length;
    if (literalNewlines > 2 && literalNewlines > realNewlines * 2) {
        cleaned = cleaned.replace(/\\n/g, '\n');
        cleaned = cleaned.replace(/\\"/g, '"');
    }

    // Strip bogus code fences AFTER all other cleaning — catches fences
    // that were inside JSON answer blocks or other wrappers. Only removes
    // fences with unknown/fake language tags; preserves real code blocks.
    cleaned = stripBogusCodeFences(cleaned).trim();

    return escapeBareNumberLines(cleaned);
}

/**
 * A line that is only a number and a period ("391.") parses as an empty ordered
 * list item, which shows the marker and hides the number. Escaping the period
 * keeps it as text; fenced code is left alone.
 */
export function escapeBareNumberLines(text: string): string {
    let inFence = false;
    return text.split('\n').map((line) => {
        if (/^\s*(```|~~~)/.test(line)) inFence = !inFence;
        if (inFence) return line;
        return line.replace(/^(\s*\d+)\.(\s*)$/, '$1\\.$2');
    }).join('\n');
}
