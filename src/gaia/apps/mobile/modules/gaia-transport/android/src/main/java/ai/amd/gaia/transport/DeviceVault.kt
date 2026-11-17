// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package ai.amd.gaia.transport

import java.io.File
import java.io.IOException
import java.security.GeneralSecurityException
import java.security.MessageDigest
import java.security.ProviderException
import javax.crypto.Cipher
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import org.json.JSONException
import org.json.JSONObject

private const val SERVICE = "ai.amd.gaia.paired-pc"
private const val TOKEN_PREFIX = "gaiad_"
private val TOKEN_CHARACTERS = Regex("^[A-Za-z0-9_-]+$")

/** Whether `token` is a device token as the PC issues them; anything else is refused before it is saved or sent. */
internal fun isDeviceToken(token: String): Boolean =
  token.length > TOKEN_PREFIX.length && token.startsWith(TOKEN_PREFIX) && TOKEN_CHARACTERS.matches(token)

/** What this phone keeps for one paired PC. It never crosses into JavaScript. */
internal data class DeviceSecret(val origin: String, val pin: String, val token: String) {
  fun encode(): ByteArray =
    JSONObject().put("origin", origin).put("pin", pin).put("token", token).toString().toByteArray(Charsets.UTF_8)

  companion object {
    @Throws(JSONException::class)
    fun decode(bytes: ByteArray): DeviceSecret {
      val json = JSONObject(String(bytes, Charsets.UTF_8))
      return DeviceSecret(json.getString("origin"), json.getString("pin"), json.getString("token"))
    }
  }
}

/** Seals a secret so only this install can open it; `aad` binds it to the PC it was saved for. */
internal interface SecretCipher {
  @Throws(GeneralSecurityException::class)
  fun seal(plain: ByteArray, aad: ByteArray): ByteArray

  @Throws(GeneralSecurityException::class)
  fun open(sealed: ByteArray, aad: ByteArray): ByteArray
}

/** AES-256-GCM with a fresh random IV per seal: a format byte, the 12-byte IV, then ciphertext and tag. */
internal class AesGcmCipher(private val key: () -> SecretKey) : SecretCipher {
  override fun seal(plain: ByteArray, aad: ByteArray): ByteArray {
    val cipher = Cipher.getInstance(TRANSFORMATION)
    cipher.init(Cipher.ENCRYPT_MODE, key())
    val iv = cipher.iv
    if (iv == null || iv.size != IV_BYTES) throw GeneralSecurityException("The cipher chose a ${iv?.size ?: 0}-byte IV, not $IV_BYTES.")
    cipher.updateAAD(aad)
    return byteArrayOf(FORMAT) + iv + cipher.doFinal(plain)
  }

  override fun open(sealed: ByteArray, aad: ByteArray): ByteArray {
    if (sealed.size < 1 + IV_BYTES + TAG_BITS / 8 || sealed[0] != FORMAT) {
      throw GeneralSecurityException("The saved secret isn't in a format this app wrote.")
    }
    val cipher = Cipher.getInstance(TRANSFORMATION)
    cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(TAG_BITS, sealed, 1, IV_BYTES))
    cipher.updateAAD(aad)
    return cipher.doFinal(sealed, 1 + IV_BYTES, sealed.size - 1 - IV_BYTES)
  }

  private companion object {
    const val TRANSFORMATION = "AES/GCM/NoPadding"
    const val FORMAT: Byte = 1
    const val IV_BYTES = 12
    const val TAG_BITS = 128
  }
}

/**
 * One sealed file per paired PC in `dir`, which must be outside every backup (noBackupFilesDir). The
 * file name is the SHA-256 of the PC id, and the id is the cipher's AAD, so a file can't stand in for another PC's.
 */
internal class DeviceVault(private val dir: File, private val cipher: SecretCipher) {
  @Synchronized
  fun save(pcId: String, secret: DeviceSecret) {
    guard("save") {
      if (!dir.isDirectory && !dir.mkdirs()) throw IOException("couldn't create ${dir.name}")
      val target = fileFor(pcId)
      val partial = File(dir, "${target.name}.partial")
      partial.writeBytes(cipher.seal(secret.encode(), aad(pcId)))
      if (!partial.renameTo(target)) {
        partial.delete()
        throw IOException("couldn't replace ${target.name}")
      }
    }
  }

  @Synchronized
  fun load(pcId: String): DeviceSecret? {
    val file = fileFor(pcId)
    if (!file.exists()) return null
    return guard("read") { DeviceSecret.decode(cipher.open(file.readBytes(), aad(pcId))) }
  }

  @Synchronized
  fun delete(pcId: String) {
    guard("delete") { remove(fileFor(pcId)) }
  }

  /** Every paired-PC secret this app holds, including ones no saved list names. */
  @Synchronized
  fun deleteAll() {
    guard("delete") { dir.listFiles()?.forEach(::remove) }
  }

  /** Makes the secret `pair` saved under a staging id the one for `pcId`, replacing what `pcId` had. */
  @Synchronized
  fun promote(stagedId: String, pcId: String) {
    val staged = load(stagedId)
      ?: throw transportError("not_paired", "The new pairing's key isn't on this phone anymore. Pair the phone again.")
    save(pcId, staged)
    delete(stagedId)
  }

  private fun fileFor(pcId: String): File = File(dir, "${hex(sha256(pcId.toByteArray(Charsets.UTF_8)))}.secret")

  private fun aad(pcId: String): ByteArray = "$SERVICE\u0000$pcId".toByteArray(Charsets.UTF_8)

  private fun remove(file: File) {
    if (file.exists() && !file.delete()) throw IOException("couldn't delete ${file.name}")
  }

  private inline fun <T> guard(action: String, block: () -> T): T {
    try {
      return block()
    } catch (e: GeneralSecurityException) {
      throw vaultError(action, e)
    } catch (e: ProviderException) {
      throw vaultError(action, e)
    } catch (e: IOException) {
      throw vaultError(action, e)
    } catch (e: JSONException) {
      throw vaultError(action, e)
    }
  }

  private fun vaultError(action: String, e: Exception) = transportError(
    "keychain",
    "Couldn't $action this phone's key for the paired PC in the Android Keystore (${e.javaClass.simpleName}). Pair the phone again.",
  )
}

private fun sha256(bytes: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(bytes)

private fun hex(bytes: ByteArray): String = bytes.joinToString("") { "%02x".format(it) }
