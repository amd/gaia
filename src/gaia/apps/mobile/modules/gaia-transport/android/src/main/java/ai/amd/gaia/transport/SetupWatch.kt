// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package ai.amd.gaia.transport

import android.util.Log
import java.io.IOException
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Proxy
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.ScheduledThreadPoolExecutor
import java.util.concurrent.TimeUnit
import okhttp3.Call
import okhttp3.Connection
import okhttp3.EventListener
import okhttp3.Handshake
import okhttp3.HttpUrl
import okhttp3.Protocol
import okhttp3.Request

/** How long a request may take to reach a pinned TLS session; the PC's listener allows a handshake 10 s too. */
internal const val SETUP_SECONDS = 10L

private const val LOG_TAG = "GaiaTransport"

/** Where the transport's diagnostic lines go; never headers, tokens or bodies. */
internal interface TransportLog {
  fun warn(line: String)
  fun debug(line: String)
}

internal object AndroidTransportLog : TransportLog {
  override fun warn(line: String) {
    Log.w(LOG_TAG, line)
  }

  override fun debug(line: String) {
    Log.d(LOG_TAG, line)
  }
}

/** Names a request in diagnostic lines: the JavaScript request id and the kind of address it dialed. */
internal class RequestLabel(val id: String, val route: String)

/** `url`'s scheme, host and port; never its path or query. */
internal fun originOf(url: HttpUrl): String {
  val host = if (':' in url.host) "[${url.host}]" else url.host
  return "${url.scheme}://$host:${url.port}"
}


private val watchdogTimer = ScheduledThreadPoolExecutor(1) { runnable ->
  Thread(runnable, "GaiaTransportWatchdog").apply { isDaemon = true }
}.apply { removeOnCancelPolicy = true }

// In the order OkHttp reaches them; a line reports the furthest one.
private val PHASES = listOf(
  "queued",
  "callStart",
  "dnsEnd",
  "connectStart",
  "secureConnectStart",
  "secureConnectEnd",
  "connectEnd",
  "connectionAcquired",
  "requestHeadersEnd",
  "responseHeadersStart",
)

/**
 * Gives a request `budgetMillis` from `arm` to get a connection, and reports a request that fails
 * before the PC answers. OkHttp's connect timeout covers TCP only; the TLS handshake would otherwise
 * wait as long as a stream may idle.
 */
internal class SetupWatch(
  private val label: RequestLabel,
  private val origin: String,
  private val budgetMillis: Long,
  private val log: TransportLog,
  private val canceledByApp: () -> Boolean,
  private val onStall: () -> Unit,
) : EventListener() {
  private val lock = Any()
  private var armedAt = 0L
  private var phase = PHASES.first()
  private var dialed = false
  private var acquired = false
  private var reused = false
  private var answered = false
  private var reported = false
  private var timer: ScheduledFuture<*>? = null

  fun arm() {
    synchronized(lock) {
      armedAt = System.nanoTime()
      timer = watchdogTimer.schedule(Runnable { stalled() }, budgetMillis, TimeUnit.MILLISECONDS)
    }
  }

  override fun callStart(call: Call) = reach("callStart")

  override fun dnsEnd(call: Call, domainName: String, inetAddressList: List<InetAddress>) = reach("dnsEnd")

  override fun connectStart(call: Call, inetSocketAddress: InetSocketAddress, proxy: Proxy) {
    synchronized(lock) { dialed = true }
    reach("connectStart")
    log.debug(line("connect_start"))
  }

  override fun secureConnectStart(call: Call) = reach("secureConnectStart")

  override fun secureConnectEnd(call: Call, handshake: Handshake?) {
    reach("secureConnectEnd")
    log.debug(line("secure_connect_end"))
  }

  override fun connectEnd(call: Call, inetSocketAddress: InetSocketAddress, proxy: Proxy, protocol: Protocol?) =
    reach("connectEnd")

  override fun connectionAcquired(call: Call, connection: Connection) {
    synchronized(lock) {
      acquired = true
      reused = !dialed
      disarm()
    }
    reach("connectionAcquired")
  }

  override fun requestHeadersEnd(call: Call, request: Request) = reach("requestHeadersEnd")

  override fun responseHeadersStart(call: Call) {
    synchronized(lock) { answered = true }
    reach("responseHeadersStart")
  }

  override fun callEnd(call: Call) {
    synchronized(lock) { disarm() }
  }

  override fun callFailed(call: Call, ioe: IOException) {
    // A request the app canceled isn't a connection failure.
    val canceled = canceledByApp()
    val report = synchronized(lock) {
      disarm()
      if (answered || reported || canceled) return
      reported = true
      line("setup_failed", ioe.javaClass.simpleName)
    }
    log.warn(report)
  }

  private fun stalled() {
    val canceled = canceledByApp()
    val report = synchronized(lock) {
      if (acquired || reported || canceled) return
      reported = true
      line("setup_failed", "watchdog")
    }
    log.warn(report)
    onStall()
  }

  private fun reach(next: String) {
    synchronized(lock) {
      if (PHASES.indexOf(next) > PHASES.indexOf(phase)) phase = next
    }
  }

  private fun disarm() {
    timer?.cancel(false)
    timer = null
  }

  private fun line(event: String, error: String? = null): String = synchronized(lock) {
    val elapsed = if (armedAt == 0L) 0L else TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - armedAt)
    val base = "$event id=${label.id} route=${label.route} origin=$origin phase=$phase reused=$reused elapsed_ms=$elapsed"
    if (error == null) base else "$base error=$error"
  }
}
