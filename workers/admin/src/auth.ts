// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Cloudflare Access JWT validation. Access blocks unauthenticated requests at
 * the edge; this re-checks the signed assertion so the data API is still
 * protected if the Access policy is ever misconfigured or bypassed.
 */

export interface AuthEnv {
  ACCESS_TEAM_DOMAIN: string;
  ACCESS_AUD: string;
  // Local dev only; honoured solely for localhost requests.
  ADMIN_DEV_NO_AUTH?: string;
}

export class AuthError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

interface Jwk {
  kid: string;
  kty: string;
  n: string;
  e: string;
  alg?: string;
}

export type JwksFetcher = (teamDomain: string) => Promise<Jwk[]>;

// Signing keys rotate rarely; caching them per isolate avoids a fetch on every request.
const JWKS_TTL_MS = 10 * 60_000;
let jwksCache: { domain: string; keys: Jwk[]; at: number } | null = null;

const defaultJwks: JwksFetcher = async (teamDomain) => {
  if (jwksCache && jwksCache.domain === teamDomain && Date.now() - jwksCache.at < JWKS_TTL_MS) return jwksCache.keys;
  const url = `https://${teamDomain}/cdn-cgi/access/certs`;
  const res = await fetch(url);
  if (!res.ok) throw new AuthError(`Could not fetch Access signing keys from ${url}: HTTP ${res.status}.`, 503);
  const keys = ((await res.json()) as { keys: Jwk[] }).keys;
  jwksCache = { domain: teamDomain, keys, at: Date.now() };
  return keys;
};

function b64urlToBytes(s: string): Uint8Array {
  const pad = "=".repeat((4 - (s.length % 4)) % 4);
  const bin = atob(s.replace(/-/g, "+").replace(/_/g, "/") + pad);
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

/** A token part that is not valid base64url JSON is a bad token (401), not a server error. */
function b64urlToJson<T>(s: string): T {
  try {
    return JSON.parse(new TextDecoder().decode(b64urlToBytes(s))) as T;
  } catch {
    throw new AuthError("Malformed Access assertion.", 401);
  }
}

function isLocalhost(req: Request): boolean {
  const host = new URL(req.url).hostname;
  return host === "localhost" || host === "127.0.0.1" || host === "[::1]";
}

/** Returns the authenticated email, or throws AuthError. */
export async function authenticate(
  req: Request,
  env: AuthEnv,
  getJwks: JwksFetcher = defaultJwks,
  now: number = Date.now(),
): Promise<string> {
  if (env.ADMIN_DEV_NO_AUTH === "1") {
    if (!isLocalhost(req)) {
      throw new AuthError("ADMIN_DEV_NO_AUTH is set but the request is not to localhost; refusing.", 500);
    }
    return "dev@localhost";
  }
  if (!env.ACCESS_TEAM_DOMAIN || !env.ACCESS_AUD) {
    throw new AuthError(
      "ACCESS_TEAM_DOMAIN and ACCESS_AUD are not set, so the admin dashboard cannot verify who is calling. " +
        "Set both in workers/admin/wrangler.toml (see README.md).",
      500,
    );
  }
  const token = req.headers.get("Cf-Access-Jwt-Assertion");
  if (!token) throw new AuthError("Missing Cloudflare Access assertion.", 401);
  const parts = token.split(".");
  if (parts.length !== 3) throw new AuthError("Malformed Access assertion.", 401);
  const [h, p, sig] = parts as [string, string, string];

  const header = b64urlToJson<{ kid?: string; alg?: string }>(h);
  if (header.alg !== "RS256") throw new AuthError("Unexpected Access token algorithm.", 401);
  let jwk = (await getJwks(env.ACCESS_TEAM_DOMAIN)).find((k) => k.kid === header.kid);
  if (!jwk && getJwks === defaultJwks) {
    // Access rotated its keys since they were cached: fetch them once more before refusing.
    jwksCache = null;
    jwk = (await getJwks(env.ACCESS_TEAM_DOMAIN)).find((k) => k.kid === header.kid);
  }
  if (!jwk) throw new AuthError("Access token signed by an unknown key.", 401);

  const key = await crypto.subtle.importKey(
    "jwk",
    { kty: jwk.kty, n: jwk.n, e: jwk.e, alg: "RS256", ext: true },
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
    false,
    ["verify"],
  );
  let sigBytes: Uint8Array;
  try {
    sigBytes = b64urlToBytes(sig);
  } catch {
    throw new AuthError("Malformed Access assertion.", 401);
  }
  const ok = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, sigBytes, new TextEncoder().encode(`${h}.${p}`));
  if (!ok) throw new AuthError("Access token signature is invalid.", 401);

  const claims = b64urlToJson<{ aud?: string | string[]; exp?: number; nbf?: number; iss?: string; email?: string }>(p);
  const aud = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
  if (!aud.includes(env.ACCESS_AUD)) throw new AuthError("Access token is for a different application.", 401);
  if (claims.iss !== `https://${env.ACCESS_TEAM_DOMAIN}`) throw new AuthError("Access token issuer mismatch.", 401);
  if (!claims.exp || claims.exp * 1000 < now) throw new AuthError("Access token has expired.", 401);
  if (claims.nbf && claims.nbf * 1000 > now + 60_000) throw new AuthError("Access token is not valid yet.", 401);
  return claims.email ?? "unknown";
}
