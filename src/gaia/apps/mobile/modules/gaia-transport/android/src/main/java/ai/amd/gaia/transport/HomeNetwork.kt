// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package ai.amd.gaia.transport

/** The networks a pairing code may name: the PC's OFFERED_NETWORKS (gaia/ui/devices). Twins: homeNetwork.ts, HomeNetwork.swift. */
private val OFFERED_NETWORKS = listOf("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")

/**
 * `origin` as `https://<address>:<port>` when it is exactly that, with a dotted IPv4 address on a
 * home network: no name, IPv6, userinfo, path, query or fragment. Null for anything else.
 */
internal fun homeNetworkOrigin(origin: String): String? = ipv4Origin(origin, OFFERED_NETWORKS)

private fun ipv4Origin(origin: String, networks: List<String>): String? {
  val scheme = "https://"
  if (!origin.startsWith(scheme)) return null
  var rest = origin.substring(scheme.length)
  if (rest.endsWith("/")) rest = rest.dropLast(1)
  val parts = rest.split(":")
  if (parts.size != 2) return null
  val address = ipv4(parts[0]) ?: return null
  val port = decimal(parts[1], 5) ?: return null
  if (port < 1 || port > 65535) return null
  val onNetwork = networks.any { cidr ->
    val halves = cidr.split("/")
    val base = if (halves.size == 2) ipv4(halves[0]) else null
    val bits = if (halves.size == 2) halves[1].toIntOrNull() else null
    if (base == null || bits == null) {
      false
    } else {
      val mask = if (bits == 0) 0L else (0xFFFFFFFFL shl (32 - bits)) and 0xFFFFFFFFL
      (address and mask) == (base and mask)
    }
  }
  return if (onNetwork) "https://${parts[0]}:${parts[1]}" else null
}

private fun ipv4(text: String): Long? {
  val octets = text.split(".")
  if (octets.size != 4) return null
  var value = 0L
  for (octet in octets) {
    val n = decimal(octet, 3) ?: return null
    if (n > 255) return null
    value = (value shl 8) or n.toLong()
  }
  return value
}

/** ASCII digits only, no leading zero. */
private fun decimal(text: String, digits: Int): Int? {
  if (text.isEmpty() || text.length > digits || !text.all { it in '0'..'9' }) return null
  if (text != "0" && text.startsWith("0")) return null
  return text.toInt()
}
