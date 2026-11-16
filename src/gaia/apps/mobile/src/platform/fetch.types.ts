// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

export interface FetchRequestInit {
    method?: string;
    headers?: Record<string, string>;
    body?: string | Blob | FormData;
    signal?: AbortSignal;
}

export interface FetchResponseLike {
    readonly ok: boolean;
    readonly status: number;
    readonly headers: { get(name: string): string | null };
    readonly body: ReadableStream<Uint8Array> | null;
    text(): Promise<string>;
}

export type FetchLike = (url: string, init?: FetchRequestInit) => Promise<FetchResponseLike>;
