// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package ai.amd.gaia.transport

import android.content.Context
import expo.modules.kotlin.Promise
import expo.modules.kotlin.exception.CodedException
import expo.modules.kotlin.exception.Exceptions
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import java.io.File
import java.io.IOException
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap
import okhttp3.Headers
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import org.json.JSONException
import org.json.JSONObject

private const val PAIR_ROUTE = "/api/devices/pair"
private const val PROBE_ROUTE = "/api/health"
private const val PROBE_SECONDS = 5L
private const val REDEEM_SECONDS = 30L
// Longer than the app's own longest wait for a quiet stream (a turn queued for the model).
private const val REQUEST_IDLE_SECONDS = 15 * 60L
private const val VAULT_DIR = "gaia-paired-pcs"

private fun coded(error: TransportException) = CodedException(error.code, error.message, null)

private fun Promise.reject(error: TransportException) = reject(coded(error))

private inline fun <T> rethrowCoded(block: () -> T): T = try {
  block()
} catch (e: TransportException) {
  throw coded(e)
}

/**
 * Talks to a paired PC over TLS pinned to its certificate. The device token stays in the vault:
 * native code adds it to each request, and JavaScript never sees it.
 */
class GaiaTransportModule : Module() {
  private val requests = OpenRequests()

  private val context: Context
    get() = appContext.reactContext ?: throw Exceptions.ReactContextLost()

  private val vault: DeviceVault by lazy {
    DeviceVault(File(context.noBackupFilesDir, VAULT_DIR), AesGcmCipher(KeystoreKey::get))
  }

  override fun definition() = ModuleDefinition {
    Name("GaiaTransport")

    OnCreate {
      sweepUploadTemporaries(context.cacheDir)
    }

    // Redeems a pairing code; the PC's answer without the token, which is kept here.
    AsyncFunction("pair") { pcId: String, origins: List<String>, pin: String, headers: Map<String, String>, body: String, promise: Promise ->
      Pairing(pcId, pin, headers, body, vault, promise).run(origins)
    }

    AsyncFunction("start") {
        id: String, pcId: String, method: String, url: String, headers: Map<String, String>, body: String?, promise: Promise ->
      start(id, pcId, method, url, headers, body, promise)
    }

    // As start, with a body from disk: one picked file, or a multipart form JavaScript laid out.
    AsyncFunction("upload") {
        id: String, pcId: String, method: String, url: String, headers: Map<String, String>, segments: List<BodySegment>, promise: Promise ->
      upload(id, pcId, method, url, headers, segments, promise)
    }

    AsyncFunction("read") { id: String, promise: Promise ->
      val request = requests.get(id)
      if (request == null) {
        promise.reject(transportError("unknown_request", "No request $id is open."))
      } else {
        request.read { result ->
          when (result) {
            is Outcome.Ok -> {
              val chunk = result.value
              if (chunk == null) requests.remove(id)
              promise.resolve(chunk)
            }
            is Outcome.Failed -> {
              requests.remove(id)
              promise.reject(result.error)
            }
          }
        }
      }
    }

    Function("cancel") { id: String ->
      requests.remove(id)?.cancel()
      Unit
    }

    Function("isPaired") { pcId: String ->
      rethrowCoded { vault.load(pcId) } != null
    }

    AsyncFunction("drop") { pcId: String ->
      rethrowCoded { vault.delete(pcId) }
    }

    AsyncFunction("dropAll") {
      rethrowCoded { vault.deleteAll() }
    }

    // Makes a secret saved by `pair` under a staging id the one for that PC, once the PC allows it.
    AsyncFunction("promote") { stagedId: String, pcId: String ->
      rethrowCoded { vault.promote(stagedId, pcId) }
    }

  }

  private fun start(
    id: String, pcId: String, method: String, url: String, headers: Map<String, String>, body: String?, promise: Promise,
  ) {
    val authorized = authorized(pcId, url, headers, promise) ?: return
    val pinned = PinnedRequest(authorized.pin, label = RequestLabel(id, "home"))
    requests.add(id, pinned)
    val outgoing = Outgoing(authorized.url, method, authorized.headers, body?.toByteArray(Charsets.UTF_8))
    send(id, pinned, outgoing, null, promise)
  }

  private fun upload(
    id: String, pcId: String, method: String, url: String, headers: Map<String, String>, segments: List<BodySegment>,
    promise: Promise,
  ) {
    val authorized = authorized(pcId, url, headers, promise) ?: return
    // Open before the body is written, so a cancel while a large file is copied still lands.
    val pinned = PinnedRequest(authorized.pin, label = RequestLabel(id, "home"))
    requests.add(id, pinned)
    val body = try {
      UploadBody.make(segments, UploadRoots(context.cacheDir, context.noBackupFilesDir))
    } catch (e: TransportException) {
      requests.remove(id)
      promise.reject(e)
      return
    } catch (e: IOException) {
      requests.remove(id)
      promise.reject(transportError("upload_file", "Couldn't read the file to send: ${e.message}"))
      return
    }
    send(id, pinned, Outgoing(authorized.url, method, authorized.headers), body, promise)
  }

  private class Authorized(val url: HttpUrl, val headers: Headers, val pin: ByteArray)

  /** The request to the paired PC with its device token, or null once the promise is rejected. */
  private fun authorized(pcId: String, url: String, headers: Map<String, String>, promise: Promise): Authorized? {
    val secret = try {
      vault.load(pcId)
    } catch (e: TransportException) {
      promise.reject(e)
      return null
    }
    if (secret == null) {
      promise.reject(transportError("not_paired", "This phone isn't paired with that PC. Pair it again from the PC."))
      return null
    }
    val target = url.toHttpUrlOrNull()
    if (target == null || !sameOrigin(target, secret.origin)) {
      promise.reject(transportError("origin_mismatch", "That address isn't the paired PC's, so the request wasn't sent."))
      return null
    }
    val pin = pinBytes(secret.pin)
    if (pin == null) {
      promise.reject(transportError("keychain", "The paired PC's saved pin is damaged. Pair the phone again."))
      return null
    }
    if (!isDeviceToken(secret.token)) {
      promise.reject(transportError("keychain", "The paired PC's saved token is damaged. Pair the phone again."))
      return null
    }
    val sent = try {
      requestHeaders(
        headers.filterKeys { it.lowercase(Locale.ROOT) != "authorization" } + ("Authorization" to "Bearer ${secret.token}"),
      ).build()
    } catch (e: TransportException) {
      promise.reject(e)
      return null
    }
    return Authorized(target, sent, pin)
  }

  private fun send(id: String, pinned: PinnedRequest, outgoing: Outgoing, upload: UploadBody?, promise: Promise) {
    pinned.start(outgoing, REQUEST_IDLE_SECONDS, upload) { result ->
      when (result) {
        is Outcome.Ok -> promise.resolve(describe(result.value))
        is Outcome.Failed -> {
          requests.remove(id)
          promise.reject(result.error)
        }
      }
    }
  }
}

private fun describe(head: ResponseHead): Map<String, Any?> = mapOf("status" to head.status, "headers" to head.headers)

/** The JavaScript headers as OkHttp's; a name or value HTTP can't carry refuses the request. */
private fun requestHeaders(headers: Map<String, String>): Headers.Builder {
  val builder = Headers.Builder()
  for ((name, value) in headers) {
    try {
      builder.set(name, value)
    } catch (e: IllegalArgumentException) {
      throw transportError("network", "The request to the PC couldn't be made: ${e.message}")
    }
  }
  return builder
}

/** Same scheme (https only), host and port, the port defaulted to 443 when the URL names none. */
private fun sameOrigin(url: HttpUrl, origin: String): Boolean {
  val expected = origin.toHttpUrlOrNull() ?: return false
  return url.isHttps && expected.isHttps && url.host == expected.host && url.port == expected.port
}

private class OpenRequests {
  private val byId = ConcurrentHashMap<String, PinnedRequest>()

  fun add(id: String, request: PinnedRequest) {
    byId[id] = request
  }

  fun get(id: String): PinnedRequest? = byId[id]

  fun remove(id: String): PinnedRequest? = byId.remove(id)
}

/**
 * Every offered address is tried at once; the first to finish the pinned handshake gets the
 * pairing request. The invitation is single use, so it goes to one address only.
 */
private class Pairing(
  private val pcId: String,
  private val pinHex: String,
  private val headers: Map<String, String>,
  private val body: String,
  private val vault: DeviceVault,
  private val promise: Promise,
) {
  private val lock = Any()
  private val probes = ArrayList<PinnedRequest>()
  private var pending = 0
  private var decided = false
  private var sawPinMismatch = false

  fun run(origins: List<String>) {
    val pin = pinBytes(pinHex)
    if (pin == null) {
      promise.reject(transportError("invalid_pin", "The pairing code's pin isn't 64 hex characters."))
      return
    }
    val home = origins.map(::homeNetworkOrigin)
    if (home.any { it == null }) {
      promise.reject(
        transportError(
          "not_home_origin",
          "This code isn't from a GAIA PC on your home network, so this phone won't pair with it. On your PC, open Settings, then Phones and tablets, and show a new code.",
        ),
      )
      return
    }
    val targets = home.filterNotNull().mapNotNull { origin -> (origin + PROBE_ROUTE).toHttpUrlOrNull()?.let { origin to it } }
    if (targets.isEmpty()) {
      promise.reject(transportError("unreachable", "The pairing code offers no address this phone can use."))
      return
    }
    synchronized(lock) { pending = targets.size }
    for ((origin, url) in targets) {
      val probe = PinnedRequest(pin, label = RequestLabel("pair-probe", "home"))
      synchronized(lock) { probes.add(probe) }
      probe.start(Outgoing(url), PROBE_SECONDS) { result -> probed(origin, pin, result) }
    }
  }

  private fun probed(origin: String, pin: ByteArray, result: Outcome<ResponseHead>) {
    when (result) {
      is Outcome.Ok -> {
        val toCancel = synchronized(lock) {
          if (decided) return
          pending -= 1
          decided = true
          probes.toList()
        }
        toCancel.forEach { it.cancel() }
        if (result.value.status == 403) {
          promise.reject(transportError("not_home_network", "The PC answers devices on its home network only."))
          return
        }
        redeem(origin, pin)
      }
      is Outcome.Failed -> {
        val mismatch = synchronized(lock) {
          if (decided) return
          pending -= 1
          if (result.error.code == "pin_mismatch") sawPinMismatch = true
          if (pending != 0) return
          decided = true
          sawPinMismatch
        }
        promise.reject(
          if (mismatch) {
            transportError(
              "pin_mismatch",
              "The PC's certificate doesn't match its pairing code. Show a new code on the PC and scan it again.",
            )
          } else {
            transportError(
              "unreachable",
              "Couldn't reach the PC at any address in its pairing code. Connect this phone to the same Wi-Fi as the PC.",
            )
          },
        )
      }
    }
  }

  private fun redeem(origin: String, pin: ByteArray) {
    val url = (origin + PAIR_ROUTE).toHttpUrlOrNull()
    if (url == null) {
      promise.reject(transportError("unreachable", "The pairing code's address $origin isn't valid."))
      return
    }
    val sent = try {
      requestHeaders(headers).build()
    } catch (e: TransportException) {
      promise.reject(e)
      return
    }
    val pinned = PinnedRequest(pin, label = RequestLabel("pair-redeem", "home"))
    pinned.start(Outgoing(url, "POST", sent, body.toByteArray(Charsets.UTF_8)), REDEEM_SECONDS) { result ->
      when (result) {
        is Outcome.Failed -> promise.reject(result.error)
        is Outcome.Ok -> pinned.readAll { read ->
          when (read) {
            is Outcome.Failed -> promise.reject(read.error)
            is Outcome.Ok -> finish(origin, result.value.status, read.value)
          }
        }
      }
    }
  }

  private fun finish(origin: String, status: Int, data: ByteArray) {
    val text = String(data, Charsets.UTF_8)
    if (status != 200) {
      promise.resolve(mapOf("status" to status, "origin" to origin, "body" to withoutToken(text)))
      return
    }
    val answer = try {
      JSONObject(text)
    } catch (e: JSONException) {
      null
    }
    val token = answer?.opt("token") as? String
    if (answer == null || token == null || !isDeviceToken(token)) {
      promise.reject(transportError("bad_response", "The PC's pairing answer had no device token."))
      return
    }
    answer.remove("token")
    try {
      vault.save(pcId, DeviceSecret(origin, pinHex.lowercase(Locale.ROOT), token))
    } catch (e: TransportException) {
      promise.reject(e)
      return
    }
    promise.resolve(mapOf("status" to status, "origin" to origin, "body" to answer.toString()))
  }
}

/** A pairing answer's JSON object without its `token`, so the device token never reaches JavaScript; anything else is dropped. */
internal fun withoutToken(text: String): String {
  val answer = try {
    JSONObject(text)
  } catch (e: JSONException) {
    return ""
  }
  answer.remove("token")
  return answer.toString()
}
