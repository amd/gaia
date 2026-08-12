// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Multipart-body and artifact helpers shared by every publish lane.
 *
 * The agent lane (`publish.ts`) and the skills lane (`skill-publish.ts`) upload
 * different manifests but store artifacts identically — same size ceiling, same
 * filename rule, same server-side hashing. They live here so the two lanes
 * cannot drift apart (a second copy of the 250 MiB limit is a bug waiting to
 * happen).
 */

import { HttpError } from "./http";
import type { Env } from "./types";

const DEFAULT_MAX_BYTES = 262_144_000; // 250 MiB

/** Artifact filename: a single safe path segment (no traversal, no separators). */
export const ARTIFACT_FILENAME_RE = /^[A-Za-z0-9][A-Za-z0-9._+-]*$/;

/** Lowercase hex SHA-256 of the given bytes, computed in the Worker. */
export async function sha256Hex(bytes: ArrayBuffer | Uint8Array): Promise<string> {
  const data = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Configured artifact size ceiling; a non-numeric override is a config error. */
export function maxBytes(env: Env): number {
  if (!env.MAX_ARTIFACT_BYTES) return DEFAULT_MAX_BYTES;
  const n = Number(env.MAX_ARTIFACT_BYTES);
  if (!Number.isFinite(n) || n <= 0) {
    throw new HttpError(
      500,
      "server_misconfigured",
      `MAX_ARTIFACT_BYTES is not a positive number: ${env.MAX_ARTIFACT_BYTES}.`
    );
  }
  return n;
}

/**
 * Read an optional text form part (readme/changelog/audit…). Returns null when
 * the part is absent (the documented "" catalog default downstream), the LF-
 * normalized text when present, and fails loudly on a present-but-empty part —
 * an empty file is a mistake, so reject it rather than store a blank doc.
 */
export async function optionalTextPart(
  form: FormData,
  field: string,
  label: string
): Promise<string | null> {
  const part = form.get(field);
  if (part == null) return null;
  // Multipart string fields are CRLF-normalized by the form encoding —
  // canonicalize to LF so stored text is byte-stable either way.
  const text = (typeof part === "string" ? part : await (part as Blob).text()).replace(
    /\r\n/g,
    "\n"
  );
  if (text.trim() === "") {
    throw new HttpError(
      400,
      "invalid_request",
      `The '${field}' part is empty. Send the ${label} text, or omit the ` +
        `part entirely if there is none.`
    );
  }
  return text;
}

/** Read a required text form part, failing loudly when absent or blank. */
export async function requiredTextPart(
  form: FormData,
  field: string,
  hint: string
): Promise<string> {
  const part = form.get(field);
  if (part == null) {
    throw new HttpError(400, "invalid_request", `Missing '${field}' part (${hint}).`);
  }
  const text = (typeof part === "string" ? part : await (part as Blob).text()).replace(
    /\r\n/g,
    "\n"
  );
  if (text.trim() === "") {
    throw new HttpError(400, "invalid_request", `The '${field}' part is empty (${hint}).`);
  }
  return text;
}
