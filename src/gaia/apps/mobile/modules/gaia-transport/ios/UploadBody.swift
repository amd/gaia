// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import ExpoModulesCore
import Foundation

private let copyChunkBytes = 256 * 1024

/// A piece of a request body from JavaScript: text sent as UTF-8, or a file on this device sent from disk.
struct BodySegment: Record {
  @Field var text: String?
  @Field var file: String?
}

/// A request body read from disk: a picked file as it is, or the segments joined into a temporary file.
struct UploadBody {
  let file: URL
  /// Deleted once the request ends; nil when `file` is the person's own.
  let temporary: URL?

  static func make(_ segments: [BodySegment]) throws -> UploadBody {
    if segments.count == 1, let only = segments[0].file {
      return UploadBody(file: try readableFile(only), temporary: nil)
    }
    let joined = FileManager.default.temporaryDirectory
      .appendingPathComponent("gaia-upload-\(UUID().uuidString)", isDirectory: false)
    guard
      FileManager.default.createFile(
        atPath: joined.path, contents: nil,
        attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication])
    else {
      throw transportError("upload_file", "Couldn't make room to send the file. Free some space on this phone.")
    }
    do {
      let out = try FileHandle(forWritingTo: joined)
      for segment in segments {
        if let text = segment.text {
          try out.write(contentsOf: Data(text.utf8))
        } else if let file = segment.file {
          try copy(try readableFile(file), to: out)
        } else {
          throw transportError("upload_file", "A piece of the request body was empty.")
        }
      }
      try out.close()
    } catch {
      removeTemporary(joined)
      if let error = error as? Exception { throw error }
      throw transportError("upload_file", "Couldn't read the file to send: \(error.localizedDescription)")
    }
    return UploadBody(file: joined, temporary: joined)
  }
}

func removeTemporary(_ url: URL) {
  do {
    try FileManager.default.removeItem(at: url)
  } catch {
    NSLog("GaiaTransport: couldn't delete an upload's temporary file: %@", error.localizedDescription)
  }
}

private func readableFile(_ uri: String) throws -> URL {
  guard let url = URL(string: uri), url.isFileURL else {
    throw transportError("upload_file", "The file to send isn't on this phone.")
  }
  switch checkUploadFile(url, roots: appUploadRoots()) {
  case .sendable(let file):
    return file
  case .notPicked:
    throw transportError("upload_file", "The file to send isn't one this app picked, so it wasn't sent. Pick it again.")
  case .unreadable:
    throw transportError("upload_file", "Couldn't read the file to send. Pick it again.")
  }
}

private func copy(_ source: URL, to out: FileHandle) throws {
  let input = try FileHandle(forReadingFrom: source)
  while let chunk = try input.read(upToCount: copyChunkBytes), !chunk.isEmpty {
    try out.write(contentsOf: chunk)
  }
  try input.close()
}
