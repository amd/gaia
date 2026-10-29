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

// R2's single-part PUT ceiling. A by-reference object must be single-part (R2
// records no whole-object SHA-256 otherwise), so nothing larger can be verified.
const DEFAULT_MAX_REFERENCED_BYTES = 5 * 1024 ** 3; // 5 GiB

/** Artifact filename: a single safe path segment (no traversal, no separators). */
export const ARTIFACT_FILENAME_RE = /^[A-Za-z0-9][A-Za-z0-9._+-]*$/;

/** Lowercase hex SHA-256 of the given bytes, computed in the Worker. */
export async function sha256Hex(bytes: ArrayBuffer | Uint8Array): Promise<string> {
  const data = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function parseByteLimit(raw: string | undefined, name: string, fallback: number): number {
  if (!raw) return fallback;
  const n = Number(raw);
  if (!Number.isFinite(n) || n <= 0) {
    throw new HttpError(500, "server_misconfigured", `${name} is not a positive number: ${raw}.`);
  }
  return n;
}

/** Size ceiling for an artifact sent through the Worker; a bad override is a config error. */
export function maxBytes(env: Env): number {
  return parseByteLimit(env.MAX_ARTIFACT_BYTES, "MAX_ARTIFACT_BYTES", DEFAULT_MAX_BYTES);
}

/**
 * Size ceiling for an artifact CI put straight into R2 and publishes by
 * reference. Separate from maxBytes: these bytes never pass through the Worker,
 * so the inline cap protects nothing here and only blocks large installers.
 */
export function maxReferencedBytes(env: Env): number {
  return parseByteLimit(
    env.MAX_REFERENCED_ARTIFACT_BYTES,
    "MAX_REFERENCED_ARTIFACT_BYTES",
    DEFAULT_MAX_REFERENCED_BYTES
  );
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
