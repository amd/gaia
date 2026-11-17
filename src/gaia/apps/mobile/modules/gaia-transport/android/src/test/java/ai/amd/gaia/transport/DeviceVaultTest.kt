// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package ai.amd.gaia.transport

import java.io.File
import javax.crypto.KeyGenerator
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

// A software AES key stands in for the Keystore's, which a JVM test can't reach; the sealing is the same AesGcmCipher.
class DeviceVaultTest {
  @get:Rule
  val folder = TemporaryFolder()

  private val key = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
  private val secret = DeviceSecret("https://192.168.1.20:4243", "ab".repeat(32), "gaiad_secret-token")

  private fun vault(dir: File = File(folder.root, "vault")) = DeviceVault(dir, AesGcmCipher { key })

  @Test
  fun savesAndReadsBackASecretWithoutKeepingItInTheClear() {
    val vault = vault()
    assertNull(vault.load("pc-1"))
    vault.save("pc-1", secret)
    assertEquals(secret, vault.load("pc-1"))
    val files = File(folder.root, "vault").listFiles()!!.toList()
    assertEquals(1, files.size)
    for (file in files) {
      val stored = String(file.readBytes(), Charsets.ISO_8859_1)
      assertFalse("the token is on disk in the clear", stored.contains("gaiad_"))
      assertFalse("the PC id names the file", file.name.contains("pc-1"))
    }
  }

  @Test
  fun aChangedFileOrAnotherPcsFileDoesNotOpen() {
    val vault = vault()
    vault.save("pc-1", secret)
    vault.save("pc-2", secret.copy(token = "gaiad_other"))
    val files = File(folder.root, "vault").listFiles()!!.sortedBy { it.name }
    // Each PC's file is bound to its id, so swapping files leaves neither readable.
    val first = files[0].readBytes()
    files[0].writeBytes(files[1].readBytes())
    files[1].writeBytes(first)
    assertKeychainError { vault.load("pc-1") }
    assertKeychainError { vault.load("pc-2") }

    vault.save("pc-3", secret)
    val third = File(folder.root, "vault").listFiles()!!.first { it !in files }
    val bytes = third.readBytes()
    bytes[bytes.size - 1] = (bytes[bytes.size - 1].toInt() xor 1).toByte()
    third.writeBytes(bytes)
    assertKeychainError { vault.load("pc-3") }
  }

  @Test
  fun aSecretFromAnotherInstallsKeyDoesNotOpen() {
    vault().save("pc-1", secret)
    val otherKey = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
    assertKeychainError { DeviceVault(File(folder.root, "vault"), AesGcmCipher { otherKey }).load("pc-1") }
  }

  @Test
  fun promoteMovesTheStagedSecretAndReplacesTheOldOne() {
    val vault = vault()
    vault.save("pc-1", secret.copy(token = "gaiad_old"))
    vault.save("pc-1.pending", secret)
    vault.promote("pc-1.pending", "pc-1")
    assertEquals(secret, vault.load("pc-1"))
    assertNull(vault.load("pc-1.pending"))
    try {
      vault.promote("pc-1.pending", "pc-1")
      fail("promoted a staged secret that is gone")
    } catch (e: TransportException) {
      assertEquals("not_paired", e.code)
    }
    assertEquals(secret, vault.load("pc-1"))
  }

  @Test
  fun dropAndDropAll() {
    val vault = vault()
    vault.delete("never-paired")
    vault.deleteAll()
    vault.save("pc-1", secret)
    vault.save("pc-2", secret)
    vault.delete("pc-1")
    assertNull(vault.load("pc-1"))
    assertEquals(secret, vault.load("pc-2"))
    vault.deleteAll()
    assertNull(vault.load("pc-2"))
    assertTrue(File(folder.root, "vault").listFiles()!!.isEmpty())
  }

  private fun assertKeychainError(block: () -> Unit) {
    try {
      block()
      fail("opened a secret it should have refused")
    } catch (e: TransportException) {
      assertEquals("keychain", e.code)
    }
  }
}
