// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import ExpoModulesCore
import Foundation
import Security

/// What this phone keeps for one paired PC. It never crosses into JavaScript.
struct DeviceSecret: Codable {
  let origin: String
  let pin: String
  let token: String
}

/// One Keychain item per paired PC, readable only on this device after its first unlock.
enum DeviceVault {
  private static let service = "ai.amd.gaia.paired-pc"

  static func save(_ pcId: String, _ secret: DeviceSecret) throws {
    try delete(pcId)
    var query = item(pcId)
    query[kSecValueData as String] = try JSONEncoder().encode(secret)
    query[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
    let status = SecItemAdd(query as CFDictionary, nil)
    guard status == errSecSuccess else { throw vaultError("save", status) }
  }

  static func load(_ pcId: String) throws -> DeviceSecret? {
    var query = item(pcId)
    query[kSecReturnData as String] = true
    query[kSecMatchLimit as String] = kSecMatchLimitOne
    var found: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &found)
    if status == errSecItemNotFound { return nil }
    guard status == errSecSuccess, let data = found as? Data else { throw vaultError("read", status) }
    return try JSONDecoder().decode(DeviceSecret.self, from: data)
  }

  static func delete(_ pcId: String) throws {
    let status = SecItemDelete(item(pcId) as CFDictionary)
    guard status == errSecSuccess || status == errSecItemNotFound else { throw vaultError("delete", status) }
  }

  /// Every paired-PC item this app owns, including ones no saved list names.
  static func deleteAll() throws {
    let query: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
    ]
    let status = SecItemDelete(query as CFDictionary)
    guard status == errSecSuccess || status == errSecItemNotFound else { throw vaultError("delete", status) }
  }

  private static func item(_ pcId: String) -> [String: Any] {
    [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
      kSecAttrAccount as String: pcId,
    ]
  }

  private static func vaultError(_ action: String, _ status: OSStatus) -> Exception {
    transportError(
      "keychain",
      "Couldn't \(action) this phone's key for the paired PC in the Keychain (OSStatus \(status)). Pair the phone again."
    )
  }
}

func transportError(_ code: String, _ message: String) -> Exception {
  Exception(name: code, description: message, code: code)
}
