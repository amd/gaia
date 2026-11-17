// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package ai.amd.gaia.transport

import android.net.Uri
import android.util.Log
import expo.modules.kotlin.records.Field
import expo.modules.kotlin.records.Record
import expo.modules.kotlin.types.OptimizedRecord
import java.io.File
import java.io.FileFilter
import java.io.FileOutputStream
import java.io.IOException
import java.util.UUID

private const val COPY_CHUNK_BYTES = 256 * 1024
private const val TEMPORARY_PREFIX = "gaia-upload-"

/** A piece of a request body from JavaScript: text sent as UTF-8, or a file on this device sent from disk. */
@OptimizedRecord
class BodySegment : Record {
  @Field var text: String? = null

  @Field var file: String? = null
}

/**
 * Where a file to send may be: the app's cache (pickers and the camera copy there) and files directories.
 * `vault` is noBackupFilesDir, which holds the paired PCs' sealed secrets and is never sent.
 */
internal class UploadRoots(cache: File, files: File, vault: File) {
  val cache: File = cache.canonicalFile
  val allowed: List<File> = listOf(this.cache, files.canonicalFile)
  val vault: File = vault.canonicalFile
}

/** A request body read from disk: a picked file as it is, or the segments joined into a temporary file. */
internal class UploadBody(val file: File, val temporary: File?) {
  companion object {
    fun make(segments: List<BodySegment>, roots: UploadRoots): UploadBody {
      val only = segments.singleOrNull()?.file
      if (only != null) return UploadBody(readableFile(only, roots), null)
      val joined = File(roots.cache, "$TEMPORARY_PREFIX${UUID.randomUUID()}")
      try {
        if (!joined.createNewFile()) throw IOException("${joined.name} already exists")
      } catch (e: IOException) {
        throw transportError("upload_file", "Couldn't make room to send the file. Free some space on this phone.")
      }
      try {
        FileOutputStream(joined).use { out ->
          for (segment in segments) {
            val text = segment.text
            val file = segment.file
            when {
              text != null -> out.write(text.toByteArray(Charsets.UTF_8))
              file != null -> readableFile(file, roots).inputStream().use { input ->
                input.copyTo(out, COPY_CHUNK_BYTES)
              }
              else -> throw transportError("upload_file", "A piece of the request body was empty.")
            }
          }
        }
      } catch (e: TransportException) {
        removeTemporary(joined)
        throw e
      } catch (e: IOException) {
        removeTemporary(joined)
        throw transportError("upload_file", "Couldn't read the file to send: ${e.message}")
      }
      return UploadBody(joined, joined)
    }
  }
}

internal fun removeTemporary(file: File) {
  if (file.exists() && !file.delete()) {
    Log.w("GaiaTransport", "Couldn't delete an upload's temporary file: ${file.name}")
  }
}

/** Deletes the joined bodies a request that never ended left behind, such as when the app was closed mid-upload. */
internal fun sweepUploadTemporaries(dir: File) {
  dir.listFiles(FileFilter { it.isFile && it.name.startsWith(TEMPORARY_PREFIX) })?.forEach(::removeTemporary)
}

private fun readableFile(uri: String, roots: UploadRoots): File {
  val parsed = Uri.parse(uri)
  val path = parsed.path
  if (parsed.scheme != "file" || path.isNullOrEmpty()) {
    throw transportError("upload_file", "The file to send isn't on this phone.")
  }
  return sendableFile(path, roots)
}

/** `path` resolved (no `..`, no links), when it is a readable file under an allowed root and outside the vault. */
internal fun sendableFile(path: String, roots: UploadRoots): File {
  val file = try {
    File(path).canonicalFile
  } catch (e: IOException) {
    throw transportError("upload_file", "The file to send isn't on this phone.")
  }
  if (file.isUnder(roots.vault)) {
    throw transportError("upload_file", "That file holds this phone's pairing keys, so it is never sent.")
  }
  if (roots.allowed.none { file.isUnder(it) }) {
    throw transportError("upload_file", "The file to send isn't one this app picked, so it wasn't sent. Pick it again.")
  }
  if (!file.isFile || !file.canRead()) throw transportError("upload_file", "Couldn't read the file to send. Pick it again.")
  return file
}

private fun File.isUnder(root: File): Boolean = path.startsWith(root.path + File.separator)
