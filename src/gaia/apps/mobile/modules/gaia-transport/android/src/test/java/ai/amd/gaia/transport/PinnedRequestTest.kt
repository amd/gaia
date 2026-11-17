// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package ai.amd.gaia.transport

import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import okhttp3.HttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

class PinnedRequestTest {
  private val root = HeldCertificate.Builder().commonName("Trusted CA").certificateAuthority(0).build()
  private val leaf = leafSignedByRoot("GAIA PC")
  private val otherLeaf = leafSignedByRoot("Another PC")
  private lateinit var server: MockWebServer

  @Before
  fun startServer() {
    server = MockWebServer()
    val certificates = HandshakeCertificates.Builder().heldCertificate(leaf, root.certificate).build()
    server.useHttps(certificates.sslSocketFactory(), false)
    server.start()
  }

  @After
  fun stopServer() {
    server.shutdown()
  }

  @Test
  fun pinIsTheSha256OfTheLeafAs64HexCharacters() {
    val hex = sha256(leaf.certificate).joinToString("") { "%02x".format(it) }
    assertArrayEquals(sha256(leaf.certificate), pinBytes(hex))
    assertArrayEquals(sha256(leaf.certificate), pinBytes(hex.uppercase()))
    assertNull(pinBytes(""))
    assertNull(pinBytes(hex.dropLast(2)))
    assertNull(pinBytes(hex.dropLast(1) + "g"))
  }

  @Test
  fun trustManagerAcceptsOnlyThePinnedLeaf() {
    var mismatches = 0
    val trust = PinTrustManager(sha256(leaf.certificate)) { mismatches++ }
    trust.checkServerTrusted(arrayOf(leaf.certificate, root.certificate), "ECDHE_ECDSA")
    assertEquals(0, mismatches)
    assertRejected(trust, arrayOf(otherLeaf.certificate, root.certificate))
    assertRejected(trust, arrayOf(root.certificate, leaf.certificate))
    assertRejected(trust, arrayOf())
    assertRejected(PinTrustManager(ByteArray(0)) { mismatches++ }, arrayOf(leaf.certificate))
    assertEquals(0, trust.getAcceptedIssuers().size)
  }

  @Test
  fun matchingPinReachesThePcAndStreamsTheBody() {
    server.enqueue(MockResponse().setResponseCode(200).setHeader("X-Gaia", "yes").setBody("hello pc"))
    val request = PinnedRequest(sha256(leaf.certificate))
    val head = startAndWait(request)
    assertTrue("expected a response, got $head", head is Outcome.Ok)
    val ok = head as Outcome.Ok
    assertEquals(200, ok.value.status)
    assertEquals("yes", ok.value.headers["x-gaia"])
    assertEquals("hello pc", String(readAll(request), Charsets.UTF_8))
  }

  @Test
  fun aDifferentLeafFromTheSameTrustedCaIsRefused() {
    // A client that trusts the CA accepts this server, so only the pin refuses it.
    val trustsCa = HandshakeCertificates.Builder().addTrustedCertificate(root.certificate).build()
    val trusting = OkHttpClient.Builder()
      .sslSocketFactory(trustsCa.sslSocketFactory(), trustsCa.trustManager)
      .hostnameVerifier { _, _ -> true }
      .build()
    server.enqueue(MockResponse().setBody("ok"))
    trusting.newCall(Request.Builder().url(server.url("/")).build()).execute().use { assertEquals(200, it.code) }

    val head = startAndWait(PinnedRequest(sha256(otherLeaf.certificate)))
    assertEquals("pin_mismatch", (head as Outcome.Failed).error.code)
  }

  @Test
  fun noPinRefusesEveryServer() {
    val head = startAndWait(PinnedRequest(ByteArray(0)))
    assertEquals("pin_mismatch", (head as Outcome.Failed).error.code)
  }

  @Test
  fun anAnswerOkHttpWouldFollowIsNotSentAgain() {
    server.enqueue(MockResponse().setResponseCode(503).setHeader("Retry-After", "0"))
    server.enqueue(MockResponse().setBody("sent twice"))
    val request = PinnedRequest(sha256(leaf.certificate))
    val head = startAndWait(request, server.url("/api/devices/pair"), Outgoing(server.url("/api/devices/pair"), "POST", body = "{}".toByteArray()))
    assertEquals("network", (head as Outcome.Failed).error.code)
    assertEquals(1, server.requestCount)
  }

  @Test
  fun plainHttpIsNeverSent() {
    val plain = MockWebServer()
    plain.start()
    try {
      val head = startAndWait(PinnedRequest(sha256(leaf.certificate)), plain.url("/api/health"))
      assertEquals("network", (head as Outcome.Failed).error.code)
      assertEquals(0, plain.requestCount)
    } finally {
      plain.shutdown()
    }
  }

  @Test
  fun aPinnedCertificateWithNoNameOrCaFlagIsTrustedByItsPinAlone() {
    // A bare self-signed leaf: no subject alternative name, not a CA.
    val bare = HeldCertificate.Builder().commonName("GAIA PC").build()
    val bareServer = MockWebServer()
    bareServer.useHttps(HandshakeCertificates.Builder().heldCertificate(bare).build().sslSocketFactory(), false)
    bareServer.start()
    try {
      bareServer.enqueue(MockResponse().setBody("hello pc"))
      val head = startAndWait(PinnedRequest(sha256(bare.certificate)), bareServer.url("/api/health"))
      assertEquals(200, (head as Outcome.Ok).value.status)
    } finally {
      bareServer.shutdown()
    }

    // The same name with a valid SAN, from a CA, still isn't the pinned PC.
    val wellFormed = leafSignedByRoot("GAIA PC")
    val wellFormedServer = MockWebServer()
    wellFormedServer.useHttps(
      HandshakeCertificates.Builder().heldCertificate(wellFormed, root.certificate).build().sslSocketFactory(),
      false,
    )
    wellFormedServer.start()
    try {
      wellFormedServer.enqueue(MockResponse().setBody("not the pc"))
      val head = startAndWait(PinnedRequest(sha256(bare.certificate)), wellFormedServer.url("/api/health"))
      assertEquals("pin_mismatch", (head as Outcome.Failed).error.code)
      assertEquals(0, wellFormedServer.requestCount)
    } finally {
      wellFormedServer.shutdown()
    }
  }

  @Test
  fun aHandshakeThatNeverFinishesIsStoppedAsANetworkFailureAndReported() {
    val listener = ServerSocket(0, 1, InetAddress.getLoopbackAddress())
    val accepted = CopyOnWriteArrayList<Socket>()
    // Accepts TCP and then says nothing, so the TLS handshake waits.
    val acceptor = Thread { runCatching { accepted.add(listener.accept()) } }.apply {
      isDaemon = true
      start()
    }
    val log = RecordingLog()
    try {
      val url = HttpUrl.Builder().scheme("https").host("127.0.0.1").port(listener.localPort).encodedPath("/api/health").build()
      val request = PinnedRequest(sha256(leaf.certificate), label = RequestLabel("pinned-7", "home"), log = log)
      val done = CountDownLatch(1)
      var outcome: Outcome<ResponseHead>? = null
      val startedAt = System.nanoTime()
      request.start(Outgoing(url), idleSeconds = 600, setupSeconds = 2) {
        outcome = it
        done.countDown()
      }
      assertTrue("still waiting 8 s after a 2 s budget", done.await(8, TimeUnit.SECONDS))
      val waitedMs = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - startedAt)
      assertTrue("stopped after ${waitedMs} ms, before its budget", waitedMs >= 1_800)
      assertEquals("network", (outcome as Outcome.Failed).error.code)

      val origin = "https://127.0.0.1:${listener.localPort}"
      assertEquals(1, log.warnings.size)
      val line = log.warnings[0]
      assertTrue(line, line.startsWith("setup_failed id=pinned-7 route=home origin=$origin phase=secureConnectStart reused=false elapsed_ms="))
      assertTrue(line, line.endsWith(" error=watchdog"))
      assertTrue(log.debugs.toString(), log.debugs.any { it.startsWith("connect_start id=pinned-7 ") })
    } finally {
      accepted.forEach { it.close() }
      listener.close()
      acceptor.join(1_000)
    }
  }

  private class RecordingLog : TransportLog {
    val warnings = CopyOnWriteArrayList<String>()
    val debugs = CopyOnWriteArrayList<String>()

    override fun warn(line: String) {
      warnings.add(line)
    }

    override fun debug(line: String) {
      debugs.add(line)
    }
  }

  private fun leafSignedByRoot(name: String) = HeldCertificate.Builder()
    .commonName(name)
    .addSubjectAlternativeName("localhost")
    .signedBy(root)
    .build()

  private fun startAndWait(
    request: PinnedRequest,
    url: HttpUrl = server.url("/api/health"),
    outgoing: Outgoing = Outgoing(url),
  ): Outcome<ResponseHead> {
    val done = CountDownLatch(1)
    var outcome: Outcome<ResponseHead>? = null
    request.start(outgoing, 5) {
      outcome = it
      done.countDown()
    }
    assertTrue("no answer within 10 s", done.await(10, TimeUnit.SECONDS))
    return outcome!!
  }

  private fun readAll(request: PinnedRequest): ByteArray {
    val done = CountDownLatch(1)
    var body: Outcome<ByteArray>? = null
    request.readAll {
      body = it
      done.countDown()
    }
    assertTrue("body not read within 10 s", done.await(10, TimeUnit.SECONDS))
    return (body as Outcome.Ok).value
  }

  private fun assertRejected(trust: PinTrustManager, chain: Array<X509Certificate>) {
    try {
      trust.checkServerTrusted(chain, "ECDHE_ECDSA")
      fail("accepted a chain whose leaf isn't pinned")
    } catch (expected: CertificateException) {
    }
  }

  private fun sha256(certificate: X509Certificate): ByteArray =
    MessageDigest.getInstance("SHA-256").digest(certificate.encoded)
}
