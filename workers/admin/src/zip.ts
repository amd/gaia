// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Minimal ZIP reader for GitHub Actions artifacts: reads the central directory
 * and inflates only the entries asked for, using the runtime's
 * DecompressionStream. Supports "stored" and "deflate" entries, which is all
 * actions/upload-artifact produces; anything else is an error, not a skip.
 */

export interface ZipEntry {
  name: string;
  method: number;
  compressedSize: number;
  size: number;
  localOffset: number;
}

const u16 = (b: Uint8Array, o: number) => b[o]! | (b[o + 1]! << 8);
const u32 = (b: Uint8Array, o: number) => (b[o]! | (b[o + 1]! << 8) | (b[o + 2]! << 16) | (b[o + 3]! << 24)) >>> 0;

export function listZip(buf: Uint8Array): ZipEntry[] {
  // End of central directory: signature 0x06054b50, searched from the end (comment can follow).
  let eocd = -1;
  for (let i = buf.length - 22; i >= Math.max(0, buf.length - 22 - 0xffff); i--) {
    if (u32(buf, i) === 0x06054b50) {
      eocd = i;
      break;
    }
  }
  if (eocd < 0) throw new Error("Not a zip file: no end-of-central-directory record.");
  const count = u16(buf, eocd + 10);
  let p = u32(buf, eocd + 16);
  const out: ZipEntry[] = [];
  const dec = new TextDecoder();
  for (let i = 0; i < count; i++) {
    if (u32(buf, p) !== 0x02014b50) throw new Error(`Corrupt zip: bad central directory entry ${i}.`);
    const method = u16(buf, p + 10);
    const compressedSize = u32(buf, p + 20);
    const size = u32(buf, p + 24);
    const nameLen = u16(buf, p + 28);
    const extraLen = u16(buf, p + 30);
    const commentLen = u16(buf, p + 32);
    const localOffset = u32(buf, p + 42);
    const name = dec.decode(buf.subarray(p + 46, p + 46 + nameLen));
    out.push({ name, method, compressedSize, size, localOffset });
    p += 46 + nameLen + extraLen + commentLen;
  }
  return out;
}

export async function readEntry(buf: Uint8Array, e: ZipEntry): Promise<Uint8Array> {
  const lo = e.localOffset;
  if (u32(buf, lo) !== 0x04034b50) throw new Error(`Corrupt zip: bad local header for ${e.name}.`);
  const start = lo + 30 + u16(buf, lo + 26) + u16(buf, lo + 28);
  const data = buf.subarray(start, start + e.compressedSize);
  if (e.method === 0) return data;
  if (e.method !== 8) throw new Error(`Unsupported zip compression method ${e.method} for ${e.name}.`);
  const stream = new Blob([data]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

export async function readText(buf: Uint8Array, e: ZipEntry): Promise<string> {
  return new TextDecoder().decode(await readEntry(buf, e));
}
