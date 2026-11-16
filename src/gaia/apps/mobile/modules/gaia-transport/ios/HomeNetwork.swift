// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import Foundation

/// The networks a pairing code may name: the PC's OFFERED_NETWORKS (gaia/ui/devices). Twin: homeNetwork.ts.
private let offeredNetworks = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]

/// `origin` as `https://<address>:<port>` when it is exactly that, with a dotted IPv4 address on a
/// home network: no name, IPv6, userinfo, path, query or fragment. Nil for anything else.
func homeNetworkOrigin(_ origin: String) -> String? {
  let scheme = "https://"
  guard origin.hasPrefix(scheme) else { return nil }
  var rest = Substring(origin.dropFirst(scheme.count))
  if rest.hasSuffix("/") { rest = rest.dropLast() }
  let parts = rest.split(separator: ":", omittingEmptySubsequences: false)
  guard parts.count == 2, let address = ipv4(parts[0]),
    let port = decimal(parts[1], digits: 5), port >= 1, port <= 65535
  else { return nil }
  let onHomeNetwork = offeredNetworks.contains { cidr in
    let halves = cidr.split(separator: "/")
    guard halves.count == 2, let base = ipv4(halves[0]), let bits = Int(halves[1]) else { return false }
    let mask: UInt32 = bits == 0 ? 0 : UInt32.max << UInt32(32 - bits)
    return address & mask == base & mask
  }
  return onHomeNetwork ? "https://\(parts[0]):\(parts[1])" : nil
}

private func ipv4(_ text: Substring) -> UInt32? {
  let octets = text.split(separator: ".", omittingEmptySubsequences: false)
  guard octets.count == 4 else { return nil }
  var value: UInt32 = 0
  for octet in octets {
    guard let n = decimal(octet, digits: 3), n <= 255 else { return nil }
    value = value << 8 | UInt32(n)
  }
  return value
}

/// ASCII digits only, no leading zero.
private func decimal(_ text: Substring, digits: Int) -> Int? {
  guard !text.isEmpty, text.count <= digits, text.utf8.allSatisfy({ $0 >= 48 && $0 <= 57 }),
    text == "0" || !text.hasPrefix("0")
  else { return nil }
  return Int(text)
}
