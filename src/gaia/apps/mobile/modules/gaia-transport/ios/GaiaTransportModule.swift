// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import ExpoModulesCore
import Foundation

private let pairRoute = "/api/devices/pair"
private let probeRoute = "/api/health"
private let tokenPrefix = "gaiad_"
private let probeSeconds: TimeInterval = 5
private let redeemSeconds: TimeInterval = 30
// Longer than the app's own longest wait for a quiet stream (a turn queued for the model).
private let requestIdleSeconds: TimeInterval = 15 * 60

/// Talks to a paired PC over TLS pinned to its certificate. The device token stays in the
/// Keychain: native code adds it to each request, and JavaScript never sees it.
public class GaiaTransportModule: Module {
  private let open = OpenRequests()

  public func definition() -> ModuleDefinition {
    Name("GaiaTransport")

    // Redeems a pairing code; the PC's answer without the token, which is kept here.
    AsyncFunction("pair") {
      (pcId: String, origins: [String], pin: String, headers: [String: String], body: String, promise: Promise) in
      Pairing(pcId: pcId, pinHex: pin, headers: headers, body: body, promise: promise).run(origins)
    }

    AsyncFunction("start") {
      (id: String, pcId: String, method: String, url: String, headers: [String: String], body: String?, promise: Promise) in
      self.start(id: id, pcId: pcId, method: method, url: url, headers: headers, body: body, promise: promise)
    }

    // As start, with a body from disk: one picked file, or a multipart form JavaScript laid out.
    AsyncFunction("upload") {
      (
        id: String, pcId: String, method: String, url: String, headers: [String: String], segments: [BodySegment],
        promise: Promise
      ) in
      self.upload(id: id, pcId: pcId, method: method, url: url, headers: headers, segments: segments, promise: promise)
    }

    AsyncFunction("read") { (id: String, promise: Promise) in
      guard let request = self.open.get(id) else {
        promise.reject(transportError("unknown_request", "No request \(id) is open."))
        return
      }
      request.read { result in
        switch result {
        case .success(let chunk?):
          promise.resolve(chunk as Any)
        case .success(nil):
          self.open.remove(id)
          promise.resolve(nil)
        case .failure(let error):
          self.open.remove(id)
          promise.reject(error)
        }
      }
    }

    Function("cancel") { (id: String) in
      if let request = self.open.remove(id) { request.cancel() }
    }

    Function("isPaired") { (pcId: String) throws -> Bool in
      try DeviceVault.load(pcId) != nil
    }

    AsyncFunction("drop") { (pcId: String) throws in
      try DeviceVault.delete(pcId)
    }

    AsyncFunction("dropAll") { () throws in
      try DeviceVault.deleteAll()
    }

    // Makes a secret saved by `pair` under a staging id the one for that PC, once the PC allows it.
    AsyncFunction("promote") { (stagedId: String, pcId: String) throws in
      guard let staged = try DeviceVault.load(stagedId) else {
        throw transportError("not_paired", "The new pairing's key isn't on this phone anymore. Pair the phone again.")
      }
      try DeviceVault.save(pcId, staged)
      try DeviceVault.delete(stagedId)
    }
  }

  private func start(
    id: String, pcId: String, method: String, url: String, headers: [String: String], body: String?, promise: Promise
  ) {
    guard let (prepared, pin) = authorized(pcId: pcId, method: method, url: url, headers: headers, promise: promise)
    else { return }
    var request = prepared
    if let body { request.httpBody = Data(body.utf8) }
    let pinned = PinnedRequest(pin: pin)
    open.add(id, pinned)
    send(id, pinned, request, upload: nil, promise: promise)
  }

  private func upload(
    id: String, pcId: String, method: String, url: String, headers: [String: String], segments: [BodySegment],
    promise: Promise
  ) {
    guard let (request, pin) = authorized(pcId: pcId, method: method, url: url, headers: headers, promise: promise)
    else { return }
    // Open before the body is written, so a cancel while a large file is copied still lands.
    let pinned = PinnedRequest(pin: pin)
    open.add(id, pinned)
    let body: UploadBody
    do {
      body = try UploadBody.make(segments)
    } catch {
      open.remove(id)
      promise.reject(error)
      return
    }
    send(id, pinned, request, upload: body, promise: promise)
  }

  /// The request to the paired PC with its device token, or nil once the promise is rejected.
  private func authorized(
    pcId: String, method: String, url: String, headers: [String: String], promise: Promise
  ) -> (URLRequest, Data)? {
    let secret: DeviceSecret
    do {
      guard let found = try DeviceVault.load(pcId) else {
        promise.reject(transportError("not_paired", "This phone isn't paired with that PC. Pair it again from the PC."))
        return nil
      }
      secret = found
    } catch {
      promise.reject(error)
      return nil
    }
    guard let target = URL(string: url), sameOrigin(target, secret.origin) else {
      promise.reject(transportError("origin_mismatch", "That address isn't the paired PC's, so the request wasn't sent."))
      return nil
    }
    guard let pin = pinBytes(secret.pin) else {
      promise.reject(transportError("keychain", "The paired PC's saved pin is damaged. Pair the phone again."))
      return nil
    }
    var request = URLRequest(url: target)
    request.httpMethod = method
    for (name, value) in headers where name.lowercased() != "authorization" {
      request.setValue(value, forHTTPHeaderField: name)
    }
    request.setValue("Bearer \(secret.token)", forHTTPHeaderField: "Authorization")
    return (request, pin)
  }

  private func send(_ id: String, _ pinned: PinnedRequest, _ request: URLRequest, upload: UploadBody?, promise: Promise) {
    pinned.start(request, idleSeconds: requestIdleSeconds, upload: upload) { result in
      switch result {
      case .success(let response):
        promise.resolve(describe(response))
      case .failure(let error):
        self.open.remove(id)
        promise.reject(error)
      }
    }
  }
}

private func describe(_ response: HTTPURLResponse) -> [String: Any] {
  var headers: [String: String] = [:]
  for (name, value) in response.allHeaderFields {
    if let name = name as? String { headers[name.lowercased()] = "\(value)" }
  }
  return ["status": response.statusCode, "headers": headers]
}

private func sameOrigin(_ url: URL, _ origin: String) -> Bool {
  guard let expected = URL(string: origin), url.scheme == "https", expected.scheme == "https" else { return false }
  return url.host?.lowercased() == expected.host?.lowercased() && url.port == expected.port
}

private final class OpenRequests: @unchecked Sendable {
  private let lock = NSLock()
  private var byId: [String: PinnedRequest] = [:]

  func add(_ id: String, _ request: PinnedRequest) { lock.withLock { byId[id] = request } }
  func get(_ id: String) -> PinnedRequest? { lock.withLock { byId[id] } }
  @discardableResult
  func remove(_ id: String) -> PinnedRequest? { lock.withLock { byId.removeValue(forKey: id) } }
}

/// Every offered address is tried at once; the first to finish the pinned handshake gets the
/// pairing request. The invitation is single use, so it goes to one address only.
private final class Pairing: @unchecked Sendable {
  private let pcId: String
  private let pinHex: String
  private let headers: [String: String]
  private let body: String
  private let promise: Promise
  private let lock = NSLock()
  private var probes: [PinnedRequest] = []
  private var pending = 0
  private var decided = false
  private var sawPinMismatch = false

  init(pcId: String, pinHex: String, headers: [String: String], body: String, promise: Promise) {
    self.pcId = pcId
    self.pinHex = pinHex
    self.headers = headers
    self.body = body
    self.promise = promise
  }

  func run(_ origins: [String]) {
    guard let pin = pinBytes(pinHex) else {
      promise.reject(transportError("invalid_pin", "The pairing code's pin isn't 64 hex characters."))
      return
    }
    let home = origins.map(homeNetworkOrigin)
    guard home.allSatisfy({ $0 != nil }) else {
      promise.reject(
        transportError(
          "not_home_origin",
          "This code isn't from a GAIA PC on your home network, so this phone won't pair with it. On your PC, open Settings, then Phones and tablets, and show a new code."
        ))
      return
    }
    let targets = home.compactMap { origin -> (String, URL)? in
      guard let origin, let url = URL(string: origin + probeRoute) else { return nil }
      return (origin, url)
    }
    guard !targets.isEmpty else {
      promise.reject(transportError("unreachable", "The pairing code offers no address this phone can use."))
      return
    }
    lock.withLock { pending = targets.count }
    for (origin, url) in targets {
      let probe = PinnedRequest(pin: pin)
      lock.withLock { probes.append(probe) }
      probe.start(URLRequest(url: url), idleSeconds: probeSeconds) { result in
        self.probed(origin, pin: pin, result)
      }
    }
  }

  private func probed(_ origin: String, pin: Data, _ result: Result<HTTPURLResponse, Exception>) {
    lock.lock()
    if decided {
      lock.unlock()
      return
    }
    pending -= 1
    switch result {
    case .success(let response):
      decided = true
      let probes = self.probes
      lock.unlock()
      probes.forEach { $0.cancel() }
      if response.statusCode == 403 {
        promise.reject(transportError("not_home_network", "The PC answers devices on its home network only."))
        return
      }
      redeem(origin, pin: pin)
    case .failure(let error):
      if error.code == "pin_mismatch" { sawPinMismatch = true }
      let last = pending == 0
      if last { decided = true }
      let mismatch = sawPinMismatch
      lock.unlock()
      guard last else { return }
      promise.reject(
        mismatch
          ? transportError(
            "pin_mismatch",
            "The PC's certificate doesn't match its pairing code. Show a new code on the PC and scan it again.")
          : transportError(
            "unreachable",
            "Couldn't reach the PC at any address in its pairing code. Connect this phone to the same Wi-Fi as the PC.")
      )
    }
  }

  private func redeem(_ origin: String, pin: Data) {
    guard let url = URL(string: origin + pairRoute) else {
      promise.reject(transportError("unreachable", "The pairing code's address \(origin) isn't valid."))
      return
    }
    var request = URLRequest(url: url)
    request.httpMethod = "POST"
    for (name, value) in headers { request.setValue(value, forHTTPHeaderField: name) }
    request.httpBody = Data(body.utf8)
    let pinned = PinnedRequest(pin: pin)
    pinned.start(request, idleSeconds: redeemSeconds) { result in
      switch result {
      case .failure(let error):
        self.promise.reject(error)
      case .success(let response):
        pinned.readAll { read in
          switch read {
          case .failure(let error):
            self.promise.reject(error)
          case .success(let data):
            self.finish(origin, status: response.statusCode, data: data)
          }
        }
      }
    }
  }

  private func finish(_ origin: String, status: Int, data: Data) {
    guard status == 200 else {
      promise.resolve(["status": status, "origin": origin, "body": String(decoding: withoutToken(data), as: UTF8.self)])
      return
    }
    guard var answer = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
      let token = answer.removeValue(forKey: "token") as? String, token.hasPrefix(tokenPrefix)
    else {
      promise.reject(transportError("bad_response", "The PC's pairing answer had no device token."))
      return
    }
    do {
      try DeviceVault.save(pcId, DeviceSecret(origin: origin, pin: pinHex.lowercased(), token: token))
      let rest = try JSONSerialization.data(withJSONObject: answer)
      promise.resolve(["status": status, "origin": origin, "body": String(decoding: rest, as: UTF8.self)])
    } catch {
      promise.reject(error)
    }
  }
}
