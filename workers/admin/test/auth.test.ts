// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { beforeAll, describe, expect, it } from "vitest";
import { authenticate, AuthError, type JwksFetcher } from "../src/auth";

const TEAM = "gaia.cloudflareaccess.com";
const AUD = "aud-123";
const b64u = (b: ArrayBuffer | string): string => {
  const bytes = typeof b === "string" ? new TextEncoder().encode(b) : new Uint8Array(b);
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
};

let pair: CryptoKeyPair;
let jwks: JwksFetcher;
let otherPair: CryptoKeyPair;

async function sign(claims: Record<string, unknown>, key: CryptoKey, kid = "k1", alg = "RS256"): Promise<string> {
  const h = b64u(JSON.stringify({ alg, kid }));
  const p = b64u(JSON.stringify(claims));
  const sig = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, new TextEncoder().encode(`${h}.${p}`));
  return `${h}.${p}.${b64u(sig)}`;
}

const good = (over: Record<string, unknown> = {}) => ({
  aud: [AUD],
  iss: `https://${TEAM}`,
  exp: Math.floor(Date.now() / 1000) + 600,
  email: "dev@amd.com",
  ...over,
});
const req = (token?: string, url = "https://amd-gaia.ai/admin") =>
  new Request(url, { headers: token ? { "Cf-Access-Jwt-Assertion": token } : {} });
const env = { ACCESS_TEAM_DOMAIN: TEAM, ACCESS_AUD: AUD };
const status = async (p: Promise<unknown>): Promise<number | string> =>
  p.then(
    () => "ok",
    (e: unknown) => (e instanceof AuthError ? e.status : "other"),
  );

beforeAll(async () => {
  const gen = () =>
    crypto.subtle.generateKey(
      { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
      true,
      ["sign", "verify"],
    ) as Promise<CryptoKeyPair>;
  pair = await gen();
  otherPair = await gen();
  const jwk = (await crypto.subtle.exportKey("jwk", pair.publicKey)) as { n: string; e: string; kty: string };
  jwks = async () => [{ kid: "k1", kty: jwk.kty, n: jwk.n, e: jwk.e }];
});

describe("authenticate", () => {
  it("accepts a valid Access assertion and returns the email", async () => {
    expect(await authenticate(req(await sign(good(), pair.privateKey)), env, jwks)).toBe("dev@amd.com");
  });

  it("rejects a request with no assertion", async () => {
    expect(await status(authenticate(req(), env, jwks))).toBe(401);
  });

  it("rejects a token signed by a different key", async () => {
    expect(await status(authenticate(req(await sign(good(), otherPair.privateKey)), env, jwks))).toBe(401);
  });

  it("rejects a tampered payload", async () => {
    const t = await sign(good(), pair.privateKey);
    const [h, , s] = t.split(".");
    expect(await status(authenticate(req(`${h}.${b64u(JSON.stringify(good({ email: "evil@x.com" })))}.${s}`), env, jwks))).toBe(401);
  });

  it("rejects the wrong audience, issuer, expired tokens and unknown kids", async () => {
    for (const over of [{ aud: ["other"] }, { iss: "https://evil.example" }, { exp: 1 }]) {
      expect(await status(authenticate(req(await sign(good(over), pair.privateKey)), env, jwks))).toBe(401);
    }
    expect(await status(authenticate(req(await sign(good(), pair.privateKey, "nope")), env, jwks))).toBe(401);
  });

  it("rejects non-RS256 algorithms (alg confusion)", async () => {
    expect(await status(authenticate(req(await sign(good(), pair.privateKey, "k1", "none")), env, jwks))).toBe(401);
  });

  it("answers 401, not 500, for a token that is not base64url JSON", async () => {
    expect(await status(authenticate(req("%%%.@@@.***"), env, jwks))).toBe(401);
    expect(await status(authenticate(req(`${b64u('{"alg":"RS256","kid":"k1"}')}.${b64u("not json")}.!!`), env, jwks))).toBe(401);
  });

  it("rejects a token that is not valid yet", async () => {
    expect(await status(authenticate(req(await sign(good({ nbf: Math.floor(Date.now() / 1000) + 3600 }), pair.privateKey)), env, jwks))).toBe(401);
  });

  it("fails loudly (500) when Access is not configured", async () => {
    expect(await status(authenticate(req("x.y.z"), { ACCESS_TEAM_DOMAIN: "", ACCESS_AUD: "" }, jwks))).toBe(500);
  });

  it("allows the dev bypass only for localhost", async () => {
    const dev = { ACCESS_TEAM_DOMAIN: "", ACCESS_AUD: "", ADMIN_DEV_NO_AUTH: "1" };
    expect(await authenticate(req(undefined, "http://localhost:8787/admin"), dev, jwks)).toBe("dev@localhost");
    expect(await status(authenticate(req(undefined, "https://amd-gaia.ai/admin"), dev, jwks))).toBe(500);
  });
});
