// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * SseDataParser unit tests — the incremental SSE framing under `query()`.
 * The hard cases: events split across arbitrary chunk boundaries, multi-line
 * data fields, CRLF endings, and end-of-stream flushing.
 */

import { describe, expect, it } from "vitest";

import { SseDataParser } from "../src/sse.js";

describe("SseDataParser", () => {
  it("parses one complete event", () => {
    const p = new SseDataParser();
    expect(p.push('data: {"type":"status","message":"hi"}\n\n')).toEqual([
      '{"type":"status","message":"hi"}',
    ]);
  });

  it("parses many events in a single chunk", () => {
    const p = new SseDataParser();
    const out = p.push('data: {"a":1}\n\ndata: {"b":2}\n\ndata: {"c":3}\n\n');
    expect(out).toEqual(['{"a":1}', '{"b":2}', '{"c":3}']);
  });

  it("reassembles an event split across arbitrary chunk boundaries", () => {
    const wire = 'data: {"type":"final","answer":"done"}\n\n';
    // Split at EVERY possible boundary — the parser must be framing-agnostic.
    for (let split = 1; split < wire.length; split++) {
      const p = new SseDataParser();
      const first = p.push(wire.slice(0, split));
      const second = p.push(wire.slice(split));
      expect([...first, ...second]).toEqual(['{"type":"final","answer":"done"}']);
    }
  });

  it("reassembles an event arriving one byte at a time", () => {
    const p = new SseDataParser();
    const wire = 'data: {"type":"token","delta":"x"}\n\n';
    const out: string[] = [];
    for (const ch of wire) out.push(...p.push(ch));
    expect(out).toEqual(['{"type":"token","delta":"x"}']);
  });

  it("joins multi-line data fields with newlines (SSE spec)", () => {
    const p = new SseDataParser();
    const out = p.push("data: line one\ndata: line two\ndata: line three\n\n");
    expect(out).toEqual(["line one\nline two\nline three"]);
  });

  it("handles CRLF line endings", () => {
    const p = new SseDataParser();
    const out = p.push('data: {"a":1}\r\n\r\ndata: {"b":2}\r\n\r\n');
    expect(out).toEqual(['{"a":1}', '{"b":2}']);
  });

  it("tolerates a missing space after the data: prefix", () => {
    const p = new SseDataParser();
    expect(p.push('data:{"a":1}\n\n')).toEqual(['{"a":1}']);
  });

  it("skips SSE comments and non-data framing fields", () => {
    const p = new SseDataParser();
    const out = p.push(
      ': keepalive\nevent: message\nid: 7\nretry: 100\ndata: {"a":1}\n\n',
    );
    expect(out).toEqual(['{"a":1}']);
  });

  it("ignores stray blank lines between events", () => {
    const p = new SseDataParser();
    expect(p.push('\n\n\ndata: {"a":1}\n\n\n\n')).toEqual(['{"a":1}']);
  });

  it("end() flushes a final event that had no trailing blank line", () => {
    const p = new SseDataParser();
    expect(p.push('data: {"type":"final","answer":"eof"}\n')).toEqual([]);
    expect(p.end()).toEqual(['{"type":"final","answer":"eof"}']);
  });

  it("end() flushes even a final event missing its trailing newline entirely", () => {
    const p = new SseDataParser();
    expect(p.push('data: {"a":1}')).toEqual([]);
    expect(p.end()).toEqual(['{"a":1}']);
  });

  it("end() returns nothing after a cleanly terminated stream", () => {
    const p = new SseDataParser();
    p.push('data: {"a":1}\n\n');
    expect(p.end()).toEqual([]);
  });
});
