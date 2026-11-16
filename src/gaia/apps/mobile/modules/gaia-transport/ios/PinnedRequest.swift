// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import CryptoKit
import ExpoModulesCore
import Foundation

/// The pin as 32 bytes; nil unless it is 64 hex characters.
func pinBytes(_ hex: String) -> Data? {
  let chars = Array(hex.lowercased().utf8)
  guard chars.count == 64 else { return nil }
  var bytes = Data(capacity: 32)
  var index = 0
  while index < chars.count {
    guard let high = hexValue(chars[index]), let low = hexValue(chars[index + 1]) else { return nil }
    bytes.append(high << 4 | low)
    index += 2
  }
  return bytes
}

private func hexValue(_ char: UInt8) -> UInt8? {
  switch char {
  case 48...57: return char - 48
  case 97...102: return char - 87
  default: return nil
  }
}

private func constantTimeEqual(_ a: Data, _ b: Data) -> Bool {
  guard a.count == b.count else { return false }
  var diff: UInt8 = 0
  for (x, y) in zip(a, b) { diff |= x ^ y }
  return diff == 0
}

/// One request over TLS pinned to a PC's certificate: the leaf's SHA-256 must match, and nothing
/// else about the certificate is checked. Redirects are refused, and nothing is cached or stored.
final class PinnedRequest: NSObject, URLSessionDataDelegate, @unchecked Sendable {
  typealias Read = (Result<Data?, Exception>) -> Void

  private let pin: Data
  private let lock = NSLock()
  private var task: URLSessionDataTask?
  private var onResponse: ((Result<HTTPURLResponse, Exception>) -> Void)?
  private var pinMismatch = false
  private var cancelled = false
  private var buffer = Data()
  private var ended = false
  private var failure: Exception?
  private var waiting: Read?
  private var temporary: URL?

  init(pin: Data) {
    self.pin = pin
  }

  /// With `upload`, the body is sent from its file, and a temporary one is deleted when the request ends.
  func start(
    _ request: URLRequest,
    idleSeconds: TimeInterval,
    upload: UploadBody? = nil,
    onResponse: @escaping (Result<HTTPURLResponse, Exception>) -> Void
  ) {
    let config = URLSessionConfiguration.ephemeral
    config.tlsMinimumSupportedProtocolVersion = .TLSv12
    config.timeoutIntervalForRequest = idleSeconds
    config.httpCookieStorage = nil
    config.httpShouldSetCookies = false
    config.urlCache = nil
    config.urlCredentialStorage = nil
    config.requestCachePolicy = .reloadIgnoringLocalAndRemoteCacheData
    let queue = OperationQueue()
    queue.maxConcurrentOperationCount = 1
    let session = URLSession(configuration: config, delegate: self, delegateQueue: queue)
    var request = request
    request.httpShouldHandleCookies = false
    let task: URLSessionDataTask
    if let upload {
      task = session.uploadTask(with: request, fromFile: upload.file)
    } else {
      task = session.dataTask(with: request)
    }
    let cancelledEarly = lock.withLock { () -> Bool in
      self.onResponse = onResponse
      self.task = task
      self.temporary = upload?.temporary
      return cancelled
    }
    task.resume()
    if cancelledEarly { task.cancel() }
    // The session keeps its delegate until it is invalidated; this lets it go once the task ends.
    session.finishTasksAndInvalidate()
  }

  func cancel() {
    let task = lock.withLock { () -> URLSessionDataTask? in
      cancelled = true
      return self.task
    }
    task?.cancel()
  }

  /// The body received since the last read; nil once it has all arrived.
  func read(_ done: @escaping Read) {
    lock.lock()
    if !buffer.isEmpty {
      let chunk = buffer
      buffer = Data()
      lock.unlock()
      done(.success(chunk))
      return
    }
    if ended {
      let failure = self.failure
      lock.unlock()
      done(failure.map { .failure($0) } ?? .success(nil))
      return
    }
    waiting = done
    lock.unlock()
  }

  func readAll(_ done: @escaping (Result<Data, Exception>) -> Void) {
    var body = Data()
    func next() {
      read { result in
        switch result {
        case .success(let chunk?):
          body.append(chunk)
          next()
        case .success(nil):
          done(.success(body))
        case .failure(let error):
          done(.failure(error))
        }
      }
    }
    next()
  }

  // MARK: - URLSession delegate

  func urlSession(
    _ session: URLSession,
    task: URLSessionTask,
    didReceive challenge: URLAuthenticationChallenge,
    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
  ) {
    guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust else {
      completionHandler(.performDefaultHandling, nil)
      return
    }
    guard let trust = challenge.protectionSpace.serverTrust, matchesPin(trust) else {
      lock.withLock { pinMismatch = true }
      completionHandler(.cancelAuthenticationChallenge, nil)
      return
    }
    completionHandler(.useCredential, URLCredential(trust: trust))
  }

  func urlSession(
    _ session: URLSession,
    task: URLSessionTask,
    willPerformHTTPRedirection response: HTTPURLResponse,
    newRequest request: URLRequest,
    completionHandler: @escaping (URLRequest?) -> Void
  ) {
    completionHandler(nil)
  }

  func urlSession(
    _ session: URLSession,
    dataTask: URLSessionDataTask,
    didReceive response: URLResponse,
    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void
  ) {
    let callback = lock.withLock { () -> ((Result<HTTPURLResponse, Exception>) -> Void)? in
      defer { onResponse = nil }
      return onResponse
    }
    guard let http = response as? HTTPURLResponse else {
      callback?(.failure(transportError("network", "The PC answered with something other than HTTP.")))
      completionHandler(.cancel)
      return
    }
    callback?(.success(http))
    completionHandler(.allow)
  }

  func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
    lock.lock()
    if let waiting {
      self.waiting = nil
      lock.unlock()
      waiting(.success(data))
      return
    }
    buffer.append(data)
    lock.unlock()
  }

  func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
    lock.lock()
    let failure = error.map { describe($0) }
    ended = true
    self.failure = failure
    let callback = onResponse
    onResponse = nil
    let waiting = self.waiting
    self.waiting = nil
    let temporary = self.temporary
    self.temporary = nil
    lock.unlock()
    if let temporary { removeTemporary(temporary) }
    callback?(.failure(failure ?? transportError("network", "The PC closed the connection without answering.")))
    waiting?(failure.map { .failure($0) } ?? .success(nil))
  }

  // MARK: - Private

  private func matchesPin(_ trust: SecTrust) -> Bool {
    guard let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate], let leaf = chain.first else {
      return false
    }
    let digest = Data(SHA256.hash(data: SecCertificateCopyData(leaf) as Data))
    return constantTimeEqual(digest, pin)
  }

  /// Called with the lock held.
  private func describe(_ error: Error) -> Exception {
    if pinMismatch {
      return transportError(
        "pin_mismatch",
        "The PC's certificate doesn't match the one this phone paired with. Pair the phone again from the PC."
      )
    }
    if cancelled || (error as? URLError)?.code == .cancelled {
      return transportError("aborted", "The request was canceled.")
    }
    return transportError("network", "Couldn't reach the PC: \(error.localizedDescription)")
  }
}
