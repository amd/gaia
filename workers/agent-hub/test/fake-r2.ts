// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * An in-memory R2 bucket faithful to the subset of the R2Bucket API the Worker
 * uses (get/put/head/delete/list with prefix+delimiter). Lets the full request
 * handlers run under plain Vitest without Miniflare or a real bucket.
 */

interface StoredObject {
  key: string;
  bytes: Uint8Array;
  contentType: string;
  uploaded: Date;
}

function toBytes(value: string | ArrayBuffer | ArrayBufferView | Uint8Array): Uint8Array {
  if (typeof value === "string") return new TextEncoder().encode(value);
  if (value instanceof Uint8Array) return new Uint8Array(value);
  if (value instanceof ArrayBuffer) return new Uint8Array(value);
  if (ArrayBuffer.isView(value)) {
    return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  }
  throw new TypeError("Unsupported R2 put value type in fake-r2.");
}

function makeBody(obj: StoredObject) {
  const bytes = obj.bytes;
  return {
    key: obj.key,
    size: bytes.byteLength,
    httpEtag: `"${obj.key}:${bytes.byteLength}"`,
    httpMetadata: { contentType: obj.contentType },
    uploaded: obj.uploaded,
    async arrayBuffer(): Promise<ArrayBuffer> {
      return bytes.slice().buffer as ArrayBuffer;
    },
    async text(): Promise<string> {
      return new TextDecoder().decode(bytes);
    },
    async json<T = unknown>(): Promise<T> {
      return JSON.parse(new TextDecoder().decode(bytes)) as T;
    },
    get body() {
      return new Response(bytes).body;
    },
    writeHttpMetadata(headers: Headers): void {
      headers.set("content-type", obj.contentType);
    },
  };
}

export class FakeR2 {
  private store = new Map<string, StoredObject>();

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  async put(key: string, value: any, options?: any): Promise<any> {
    // The streaming publish path passes `request.body` (a ReadableStream) — read
    // it to completion, mirroring R2 consuming the stream as it stores.
    const bytes =
      value && typeof value.getReader === "function"
        ? new Uint8Array(await new Response(value).arrayBuffer())
        : toBytes(value);
    const contentType = options?.httpMetadata?.contentType ?? "application/octet-stream";
    // Honour R2's optional sha256 integrity check so tests catch mismatches.
    if (options?.sha256) {
      const digest = await crypto.subtle.digest("SHA-256", bytes);
      const hex = [...new Uint8Array(digest)]
        .map((b) => b.toString(16).padStart(2, "0"))
        .join("");
      if (hex !== options.sha256) {
        throw new Error(`put sha256 mismatch: expected ${options.sha256}, got ${hex}`);
      }
    }
    const obj: StoredObject = { key, bytes, contentType, uploaded: new Date() };
    this.store.set(key, obj);
    return makeBody(obj);
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  async get(key: string): Promise<any> {
    const obj = this.store.get(key);
    return obj ? makeBody(obj) : null;
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  async head(key: string): Promise<any> {
    const obj = this.store.get(key);
    if (!obj) return null;
    const body = makeBody(obj);
    return { key: body.key, size: body.size, httpEtag: body.httpEtag, uploaded: body.uploaded };
  }

  async delete(key: string): Promise<void> {
    this.store.delete(key);
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  async list(options?: any): Promise<any> {
    const prefix: string = options?.prefix ?? "";
    const delimiter: string | undefined = options?.delimiter;
    const objects: Array<{ key: string; size: number }> = [];
    const prefixSet = new Set<string>();

    for (const [key, obj] of this.store) {
      if (!key.startsWith(prefix)) continue;
      if (delimiter) {
        const rest = key.slice(prefix.length);
        const idx = rest.indexOf(delimiter);
        if (idx !== -1) {
          prefixSet.add(prefix + rest.slice(0, idx + delimiter.length));
          continue;
        }
      }
      objects.push({ key, size: obj.bytes.byteLength });
    }

    return {
      objects,
      delimitedPrefixes: [...prefixSet].sort(),
      truncated: false,
      cursor: undefined,
    };
  }

  /** Test helper: list all stored keys. */
  keys(): string[] {
    return [...this.store.keys()].sort();
  }
}

/** Build a typed Env for tests backed by a fresh FakeR2. */
export function makeEnv(overrides?: { tokens?: unknown; maxBytes?: string }): {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  BUCKET: any;
  PUBLISH_TOKENS?: string;
  MAX_ARTIFACT_BYTES?: string;
  bucket: FakeR2;
} {
  const bucket = new FakeR2();
  const tokens =
    overrides?.tokens ??
    {
      "tok_amd": { publisher: "AMD", authors: ["AMD"] },
      "tok_admin": { publisher: "Hub Admin", authors: ["*"] },
      "tok_indie": { publisher: "Indie Dev", authors: ["Indie Dev"] },
    };
  return {
    BUCKET: bucket,
    bucket,
    PUBLISH_TOKENS: JSON.stringify(tokens),
    MAX_ARTIFACT_BYTES: overrides?.maxBytes,
  };
}

/** Build a POST /publish multipart request. */
export function publishRequest(opts: {
  token?: string;
  manifestYaml: string;
  artifact: Uint8Array | string;
  filename: string;
  contentType?: string;
  readme?: string;
  changelog?: string;
  packageFiles?: string;
}): Request {
  const form = new FormData();
  form.set("manifest", opts.manifestYaml);
  if (opts.readme !== undefined) form.set("readme", opts.readme);
  if (opts.changelog !== undefined) form.set("changelog", opts.changelog);
  if (opts.packageFiles !== undefined) form.set("package_files", opts.packageFiles);
  const bytes = typeof opts.artifact === "string" ? new TextEncoder().encode(opts.artifact) : opts.artifact;
  form.set(
    "artifact",
    new Blob([bytes], { type: opts.contentType ?? "application/octet-stream" }),
    opts.filename
  );
  const headers = new Headers();
  if (opts.token) headers.set("authorization", `Bearer ${opts.token}`);
  return new Request("https://hub.amd-gaia.ai/publish", {
    method: "POST",
    headers,
    body: form,
  });
}

/**
 * Build a POST /publish streaming request (application/octet-stream): the raw
 * artifact as the body, with metadata in `x-gaia-*` headers. The SHA-256 header
 * defaults to the correct digest of the body; pass `sha256` to force a mismatch.
 */
export async function streamingPublishRequest(opts: {
  token?: string;
  manifestYaml: string;
  artifact: Uint8Array | string;
  filename: string;
  sha256?: string;
  packageFiles?: string;
  contentLength?: string;
  omitManifest?: boolean;
  omitSha?: boolean;
  omitContentLength?: boolean;
}): Promise<Request> {
  const bytes =
    typeof opts.artifact === "string" ? new TextEncoder().encode(opts.artifact) : opts.artifact;
  const headers = new Headers();
  headers.set("content-type", "application/octet-stream");
  if (opts.token) headers.set("authorization", `Bearer ${opts.token}`);
  if (!opts.omitManifest) headers.set("x-gaia-manifest-b64", b64Utf8(opts.manifestYaml));
  headers.set("x-gaia-artifact-filename", opts.filename);
  if (!opts.omitSha) {
    headers.set("x-gaia-artifact-sha256", opts.sha256 ?? (await sha256Hex(bytes)));
  }
  if (opts.packageFiles !== undefined) {
    headers.set("x-gaia-package-files-b64", b64Utf8(opts.packageFiles));
  }
  // Content-Length is provided by the HTTP client over the wire in production;
  // set it explicitly here (undici doesn't auto-populate it on a stream body).
  if (!opts.omitContentLength) {
    headers.set("content-length", opts.contentLength ?? String(bytes.byteLength));
  }
  return new Request("https://hub.amd-gaia.ai/publish", {
    method: "POST",
    headers,
    body: bytes,
  });
}

/** base64 of UTF-8 text, using only Web globals (matches the Worker decoder). */
function b64Utf8(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** A valid sample gaia-agent.yaml for tests. */
export function sampleManifest(overrides: Partial<Record<string, string>> = {}): string {
  const id = overrides.id ?? "chat";
  const version = overrides.version ?? "0.1.0";
  const author = overrides.author ?? "AMD";
  return [
    `id: ${id}`,
    `name: ${overrides.name ?? "Chat"}`,
    `version: ${version}`,
    `description: ${overrides.description ?? '"General conversation agent"'}`,
    `author: ${author}`,
    `license: ${overrides.license ?? "MIT"}`,
    `language: ${overrides.language ?? "python"}`,
    `category: ${overrides.category ?? "conversation"}`,
    `icon: ${overrides.icon ?? "message-circle"}`,
    `security_tier: ${overrides.security_tier ?? "verified"}`,
    `min_gaia_version: "${overrides.min_gaia_version ?? "0.18.0"}"`,
    "tags: [chat, general]",
    "models: [Qwen3.5-35B-A3B-GGUF]",
    `tools_count: ${overrides.tools_count ?? "6"}`,
    "requirements:",
    "  min_memory_gb: 8",
    "  platforms: [win-x64, linux-x64, darwin-arm64]",
    "interfaces:",
    "  cli: true",
    "  api_server: true",
    "",
  ].join("\n");
}
