// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package ai.amd.gaia.transport

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class UploadBodyTest {
  @get:Rule
  val folder = TemporaryFolder()

  private lateinit var cache: File
  private lateinit var files: File
  private lateinit var vault: File
  private lateinit var roots: UploadRoots

  @Before
  fun makeAppDirs() {
    cache = folder.newFolder("cache")
    files = folder.newFolder("files")
    vault = folder.newFolder("no_backup")
    roots = UploadRoots(cache, vault)
  }

  @Test
  fun sendsOnlyReadableFilesUnderTheCache() {
    val picked = File(cache, "ImagePicker/photo.jpg").apply { parentFile!!.mkdirs(); writeText("jpg") }
    val saved = File(files, "notes.txt").apply { writeText("notes") }
    assertRefused(saved.path, "isn't one this app picked")
    assertEquals(picked.canonicalFile, sendableFile(picked.path, roots))

    val outside = folder.newFile("elsewhere.txt")
    assertRefused(outside.path, "isn't one this app picked")
    assertRefused("${cache.path}/ImagePicker/../../elsewhere.txt", "isn't one this app picked")
    assertRefused("${cache.path}/missing.jpg", "Couldn't read")
    assertRefused(cache.path, "isn't one this app picked")
  }

  @Test
  fun stripsTheTokenFromAnyPairingAnswer() {
    assertEquals("{\"error\":\"x\"}", withoutToken("{\"token\":\"gaiad_abc\",\"error\":\"x\"}"))
    assertEquals("not json gaiad_abc", withoutToken("not json gaiad_abc"))
  }

  @Test
  fun neverSendsTheVault() {
    val secret = File(vault, "gaia-paired-pcs/abc.secret").apply { parentFile!!.mkdirs(); writeBytes(byteArrayOf(1, 2, 3)) }
    assertRefused(secret.path, "pairing keys")
    assertRefused("${cache.path}/../no_backup/gaia-paired-pcs/abc.secret", "pairing keys")
  }

  @Test
  fun sweepsOnlyLeftoverJoinedBodies() {
    val leftover = File(cache, "gaia-upload-1234").apply { writeText("part") }
    val picked = File(cache, "photo.jpg").apply { writeText("jpg") }
    sweepUploadTemporaries(cache)
    assertFalse(leftover.exists())
    assertTrue(picked.exists())
  }

  @Test
  fun deviceTokensAreThePrefixAndBase64Url() {
    assertTrue(isDeviceToken("gaiad_AbC-12_xyz"))
    for (token in listOf("", "gaiad_", "AbC12", "gaiad_abc def", "gaiad_abc\r\nX-Injected: 1", "gaiad_abc=", "gaiad_é")) {
      assertFalse(token, isDeviceToken(token))
    }
  }

  private fun assertRefused(path: String, why: String) {
    try {
      sendableFile(path, roots)
      fail("sent $path")
    } catch (e: TransportException) {
      assertEquals("upload_file", e.code)
      assertTrue("$path: ${e.message}", e.message!!.contains(why))
    }
  }
}
