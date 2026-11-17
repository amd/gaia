// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import Foundation

/// Where a file to send may be: the app's caches, where pickers, the camera and a paste copy files.
func appUploadRoots() -> [URL] {
  FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)
}

enum UploadFileCheck: Equatable {
  /// The file with its links and `..` resolved; send this one, not the path that was asked for.
  case sendable(URL)
  case notPicked
  case unreadable
}

/// Whether `file`, once its links and `..` are resolved, is a readable file inside one of `roots`.
func checkUploadFile(_ file: URL, roots: [URL]) -> UploadFileCheck {
  let resolved = file.resolvingSymlinksInPath()
  let parts = resolved.pathComponents
  let inside = roots.contains { root in
    let base = root.resolvingSymlinksInPath().pathComponents
    return parts.count > base.count && Array(parts.prefix(base.count)) == base
  }
  guard inside else { return .notPicked }
  var isDirectory: ObjCBool = false
  guard FileManager.default.fileExists(atPath: resolved.path, isDirectory: &isDirectory), !isDirectory.boolValue,
    FileManager.default.isReadableFile(atPath: resolved.path)
  else { return .unreadable }
  return .sendable(resolved)
}
