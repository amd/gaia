// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import Foundation

/// A pairing answer's JSON object without its `token`, so the device token never reaches JavaScript; anything else,
/// or an object that can't be written back, becomes an empty body.
func withoutToken(_ data: Data) -> Data {
  guard var answer = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return Data() }
  answer.removeValue(forKey: "token")
  return (try? JSONSerialization.data(withJSONObject: answer)) ?? Data()
}
