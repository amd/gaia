// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Bearer-token authentication and publisher-scope enforcement.
 *
 * Tokens are configured via the `PUBLISH_TOKENS` secret (a JSON map). The token
 * itself is never logged or echoed; failures raise an HttpError with a generic
 * message so a caller can't probe which half (token vs scope) failed.
 */

import { HttpError } from "./http";
import type { Env, Publisher } from "./types";

interface RawPublisher {
  publisher?: unknown;
  authors?: unknown;
}

/**
 * Resolve the bearer token on a request to a {@link Publisher}, or throw.
 *
 * @throws HttpError 500 if PUBLISH_TOKENS is unset/malformed (server config bug)
 * @throws HttpError 401 if the Authorization header is missing/not Bearer/unknown
 */
export function authenticate(request: Request, env: Env): Publisher {
  if (!env.PUBLISH_TOKENS) {
    // Fail loudly: a publish endpoint with no token store is a deploy error,
    // not something to silently allow-all or deny-all.
    throw new HttpError(
      500,
      "server_misconfigured",
      "PUBLISH_TOKENS secret is not set. The maintainer must run " +
        "`wrangler secret put PUBLISH_TOKENS` before publishing works."
    );
  }

  let tokens: Record<string, RawPublisher>;
  try {
    tokens = JSON.parse(env.PUBLISH_TOKENS) as Record<string, RawPublisher>;
  } catch (e) {
    throw new HttpError(
      500,
      "server_misconfigured",
      `PUBLISH_TOKENS is not valid JSON: ${(e as Error).message}.`
    );
  }

  const header = request.headers.get("authorization") ?? "";
  const match = /^Bearer\s+(.+)$/i.exec(header.trim());
  if (!match) {
    throw new HttpError(
      401,
      "unauthorized",
      "Missing or malformed Authorization header. Send 'Authorization: Bearer <token>'."
    );
  }

  const record = tokens[match[1]];
  if (!record) {
    throw new HttpError(401, "unauthorized", "Invalid publish token.");
  }

  if (typeof record.publisher !== "string" || !record.publisher) {
    throw new HttpError(
      500,
      "server_misconfigured",
      "PUBLISH_TOKENS entry is missing a 'publisher' string."
    );
  }
  const authors =
    Array.isArray(record.authors) && record.authors.every((a) => typeof a === "string")
      ? (record.authors as string[])
      : [];

  return { publisher: record.publisher, authors };
}

/**
 * Enforce that this token may publish under the manifest's `author` value.
 *
 * @throws HttpError 403 if the author is outside the token's allowed scope.
 */
export function assertAuthorAllowed(publisher: Publisher, author: string): void {
  if (publisher.authors.includes("*") || publisher.authors.includes(author)) {
    return;
  }
  throw new HttpError(
    403,
    "forbidden_scope",
    `Token for publisher '${publisher.publisher}' is not authorized to publish ` +
      `agents with author '${author}'. Allowed authors: ` +
      `${publisher.authors.length ? publisher.authors.join(", ") : "(none)"}.`
  );
}
