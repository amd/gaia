// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * Minimal incremental Server-Sent-Events parser for the `/query` stream.
 *
 * Feed it decoded text chunks in whatever framing the transport delivers
 * (an event may arrive split across chunks, or many events in one chunk);
 * it returns each completed event's `data` payload as one string. Handles:
 *   - events separated by a blank line (LF or CRLF line endings)
 *   - multi-line `data:` fields (joined with "\n", per the SSE spec)
 *   - the optional single space after "data:"
 *   - comment lines (":...") and non-data fields (event:/id:/retry:) — SSE
 *     framing the /query contract doesn't use; skipped as transport chrome.
 *     (The no-silent-fallback rule applies to the JSON `type` vocabulary,
 *     which the client surfaces as QueryUnknownEvent — not to SSE framing.)
 *
 * The parser never interprets the payload — JSON parsing (and the fail-loud
 * behavior on malformed payloads) lives with the caller in client.ts.
 */

export class SseDataParser {
  private buffer = "";
  private dataLines: string[] = [];

  /** Feed one decoded chunk; returns the data payloads completed by it. */
  push(chunk: string): string[] {
    this.buffer += chunk;
    const out: string[] = [];
    let idx: number;
    // Consume only COMPLETE lines; a partial line stays buffered for the next chunk.
    while ((idx = this.buffer.indexOf("\n")) !== -1) {
      let line = this.buffer.slice(0, idx);
      this.buffer = this.buffer.slice(idx + 1);
      if (line.endsWith("\r")) line = line.slice(0, -1);
      const payload = this.consumeLine(line);
      if (payload !== undefined) out.push(payload);
    }
    return out;
  }

  /**
   * Signal end-of-stream; returns the final payload if the stream closed with
   * a pending event (no trailing blank line), else an empty array.
   */
  end(): string[] {
    // A trailing partial line without "\n" still counts at EOF.
    if (this.buffer) {
      let line = this.buffer;
      this.buffer = "";
      if (line.endsWith("\r")) line = line.slice(0, -1);
      this.consumeLine(line);
    }
    if (this.dataLines.length === 0) return [];
    const payload = this.dataLines.join("\n");
    this.dataLines = [];
    return [payload];
  }

  /** Process one complete line; returns a payload when a blank line dispatches. */
  private consumeLine(line: string): string | undefined {
    if (line === "") {
      if (this.dataLines.length === 0) return undefined; // stray blank line
      const payload = this.dataLines.join("\n");
      this.dataLines = [];
      return payload;
    }
    if (line.startsWith(":")) return undefined; // SSE comment
    if (line.startsWith("data:")) {
      let value = line.slice("data:".length);
      if (value.startsWith(" ")) value = value.slice(1);
      this.dataLines.push(value);
    }
    // Other SSE fields (event:/id:/retry:) are transport framing — skipped.
    return undefined;
  }
}
