// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The spoken-reply contract: split `<spoken>…</spoken>` off a streamed answer.
 *
 * The same rules as `gaia.voice.spoken` (Python) and the TUI's parser; all three
 * run `tests/fixtures/voice/spoken_cases.json`. Never holds back more detail than
 * a tag's length.
 */

const OPEN = '<spoken>';
const CLOSE = '</spoken>';

export type SpokenChannel = 'spoken' | 'detail';
export type SpokenPiece = [SpokenChannel, string];

/** Length of the longest suffix of `text` that is a prefix of `tag`. */
function partialSuffix(text: string, tag: string): number {
    for (let n = Math.min(tag.length - 1, text.length); n > 0; n--) {
        if (text.endsWith(tag.slice(0, n))) return n;
    }
    return 0;
}

export class SpokenSplitter {
    private state: 'start' | 'spoken' | 'gap' | 'detail' = 'start';
    private pending = '';
    private said: string[] = [];
    /** True once the answer is known to have no spoken block. */
    missing = false;

    feed(text: string): SpokenPiece[] {
        const out: SpokenPiece[] = [];
        this.pending += text;
        while (this.pending) {
            if (this.state === 'start') {
                const stripped = this.pending.trimStart();
                if (!stripped) return out;
                if (stripped.startsWith(OPEN)) {
                    this.pending = stripped.slice(OPEN.length);
                    this.state = 'spoken';
                    continue;
                }
                if (OPEN.startsWith(stripped)) return out; // might still become the tag
                this.missing = true;
                this.state = 'detail';
                this.pending = stripped;
                continue;
            }
            if (this.state === 'spoken') {
                const idx = this.pending.indexOf(CLOSE);
                if (idx >= 0) {
                    const piece = this.pending.slice(0, idx);
                    this.pending = this.pending.slice(idx + CLOSE.length);
                    this.state = 'gap';
                    if (piece) {
                        this.said.push(piece);
                        out.push(['spoken', piece]);
                    }
                    continue;
                }
                const keep = partialSuffix(this.pending, CLOSE);
                const piece = this.pending.slice(0, this.pending.length - keep);
                this.pending = this.pending.slice(piece.length);
                if (piece) {
                    this.said.push(piece);
                    out.push(['spoken', piece]);
                }
                return out;
            }
            if (this.state === 'gap') {
                // Whitespace between the block and the detail is dropped, however the stream splits it.
                this.pending = this.pending.trimStart();
                if (!this.pending) return out;
                this.state = 'detail';
            }
            out.push(['detail', this.pending]);
            this.pending = '';
        }
        return out;
    }

    /** A tool step started: what came before was narration, not the answer. */
    reset(): void {
        this.state = 'start';
        this.pending = '';
        this.said = [];
        this.missing = false;
    }

    /** Flush what is left when the answer ends. */
    finish(): SpokenPiece[] {
        const out: SpokenPiece[] = [];
        if (this.state === 'start') {
            if (this.pending.trim()) out.push(['detail', this.pending.trim()]);
            this.missing = true;
        } else if (this.state === 'spoken') {
            // An unclosed block: what was said stands.
            if (this.pending) {
                this.said.push(this.pending);
                out.push(['spoken', this.pending]);
            }
            this.missing = !this.spokenText;
        } else if (this.pending.trim() || (this.state === 'detail' && this.pending)) {
            out.push(['detail', this.pending]);
        }
        this.pending = '';
        this.state = 'detail';
        return out;
    }

    get spokenText(): string {
        return this.said.join('').replace(/\s+/g, ' ').trim();
    }

    /** Still inside the spoken block, or not yet sure there is one. */
    get open(): boolean {
        return this.state === 'start' || this.state === 'spoken';
    }
}

export interface SplitAnswer {
    spoken: string;
    detail: string;
    missing: boolean;
}

/** Split a whole answer. */
export function splitSpoken(answer: string): SplitAnswer {
    const splitter = new SpokenSplitter();
    const pieces = [...splitter.feed(answer), ...splitter.finish()];
    const detail = pieces.filter(([c]) => c === 'detail').map(([, t]) => t).join('');
    return { spoken: splitter.spokenText, detail: detail.trim(), missing: splitter.missing };
}

/**
 * A stored or streaming message as shown: the spoken lead line, if it opens with one,
 * and the rest. Text that is only the start of an open tag shows as nothing yet.
 */
export function leadLine(content: string): { lead: string; body: string } {
    const stripped = content.trimStart();
    if (!stripped.startsWith(OPEN)) {
        if (stripped && OPEN.startsWith(stripped)) return { lead: '', body: '' };
        return { lead: '', body: content };
    }
    const { spoken, detail } = splitSpoken(content);
    return { lead: spoken, body: detail };
}
