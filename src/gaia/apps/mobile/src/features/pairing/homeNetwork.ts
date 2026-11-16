// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The networks a pairing code may name: the PC's OFFERED_NETWORKS (gaia/ui/devices). Twin: HomeNetwork.swift. */
export const OFFERED_NETWORKS = ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'] as const;

const OCTET = '(?:0|[1-9]\\d{0,2})';
const ORIGIN = new RegExp(`^https://(${OCTET}(?:\\.${OCTET}){3}):([1-9]\\d{0,4})/?$`);

function ipv4(text: string): number | null {
    const parts = text.split('.').map(Number);
    if (parts.length !== 4 || parts.some((p) => !Number.isInteger(p) || p > 255)) return null;
    return parts.reduce((n, p) => n * 256 + p, 0);
}

const NETWORKS = OFFERED_NETWORKS.map((cidr) => {
    const [base, bits] = cidr.split('/');
    const size = 2 ** (32 - Number(bits));
    return { start: ipv4(base)!, size };
});

/**
 * `origin` as `https://<address>:<port>` when it is exactly that, with a dotted IPv4 address on a home
 * network: no name, IPv6, userinfo, path, query or fragment. Null for anything else.
 */
export function homeNetworkOrigin(origin: string): string | null {
    const match = ORIGIN.exec(origin);
    if (!match) return null;
    const [, host, port] = match;
    const address = ipv4(host);
    if (address === null || Number(port) > 65535) return null;
    if (!NETWORKS.some((n) => address >= n.start && address < n.start + n.size)) return null;
    return `https://${host}:${port}`;
}
