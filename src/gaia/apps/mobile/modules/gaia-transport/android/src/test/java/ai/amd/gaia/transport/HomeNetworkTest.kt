// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package ai.amd.gaia.transport

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

// The same rules as homeNetwork.ts and HomeNetwork.swift, its twins.
class HomeNetworkTest {
  @Test
  fun acceptsHomeNetworkAddresses() {
    val accepted = listOf(
      "https://192.168.1.20:4243" to "https://192.168.1.20:4243",
      "https://10.0.0.5:4243/" to "https://10.0.0.5:4243",
      "https://10.0.2.2:4243" to "https://10.0.2.2:4243",
      "https://172.16.0.1:4243" to "https://172.16.0.1:4243",
      "https://172.31.255.254:65535" to "https://172.31.255.254:65535",
      "https://192.168.0.1:1" to "https://192.168.0.1:1",
    )
    for ((origin, want) in accepted) assertEquals(origin, want, homeNetworkOrigin(origin))
  }

  @Test
  fun refusesEverythingElse() {
    // A dotted IPv4 literal on a private network, with a port, and nothing else.
    val refused = listOf(
      "https://100.64.0.1:4243",
      "https://172.32.0.1:4243",
      "https://172.15.255.255:4243",
      "https://8.8.8.8:4243",
      "https://127.0.0.1:4243",
      "https://169.254.1.1:4243",
      "https://11.0.0.1:4243",
      "https://192.169.0.1:4243",
      "http://192.168.1.20:4243",
      "https://192.168.1.20",
      "https://192.168.1.20:0",
      "https://192.168.1.20:65536",
      "https://192.168.1.20:04243",
      "https://192.168.01.20:4243",
      "https://192.168.1.256:4243",
      "https://192.168.1:4243",
      "https://192.168.1.20.5:4243",
      "https://192.168.1.20:4243/api",
      "https://192.168.1.20:4243//",
      "https://user@192.168.1.20:4243",
      "https://192.168.1.20:4243?x=1",
      "https://pc.local:4243",
      "https://[fd00::1]:4243",
      "https://192.168.1.20:+4243",
      "https://192.168.1.٢٠:4243",
    )
    for (origin in refused) assertNull(origin, homeNetworkOrigin(origin))
  }
}
