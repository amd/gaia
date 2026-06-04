// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * HTTP helpers and the Worker's error type.
 *
 * Following the project's fail-loudly rule (CLAUDE.md): every failure raises an
 * {@link HttpError} carrying an actionable message and is translated to a
 * structured JSON error at the request boundary — no silent degradation.
 */

/** An error that maps to a specific HTTP status at the request boundary. */
export class HttpError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "HttpError";
    this.status = status;
    this.code = code;
  }
}

const JSON_HEADERS = { "content-type": "application/json; charset=utf-8" };

/** Serialize a value as a JSON response. */
export function json(data: unknown, status = 200, extraHeaders?: HeadersInit): Response {
  const headers = new Headers(JSON_HEADERS);
  if (extraHeaders) {
    new Headers(extraHeaders).forEach((value, key) => headers.set(key, value));
  }
  return new Response(JSON.stringify(data, null, 2), { status, headers });
}

/** Structured JSON error body: { error: { code, message } }. */
export function errorResponse(err: HttpError): Response {
  return json({ error: { code: err.code, message: err.message } }, err.status);
}
