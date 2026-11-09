// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/keyring.h"

#include <mutex>
#include <stdexcept>

#ifdef _WIN32
#include <windows.h>
#include <wincred.h>
#else
#include "gaia/process.h"
#endif

namespace gaia {

namespace {

std::mutex& backendMutex() {
    static std::mutex m;
    return m;
}
KeyringBackend& testBackend() {
    static KeyringBackend b;
    return b;
}

#ifdef _WIN32
std::wstring widen(const std::string& s) {
    if (s.empty()) return {};
    const int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), nullptr, 0);
    std::wstring w(static_cast<size_t>(n), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), &w[0], n);
    return w;
}

std::string narrow(const wchar_t* w, size_t len) {
    if (len == 0) return {};
    const int n = WideCharToMultiByte(CP_UTF8, 0, w, static_cast<int>(len), nullptr, 0, nullptr, nullptr);
    std::string s(static_cast<size_t>(n), '\0');
    WideCharToMultiByte(CP_UTF8, 0, w, static_cast<int>(len), &s[0], n, nullptr, nullptr);
    return s;
}

// keyring stores the password as UTF-16 in the credential blob.
std::optional<std::string> readTarget(const std::string& target, const std::string* requireUser) {
    PCREDENTIALW cred = nullptr;
    if (!CredReadW(widen(target).c_str(), CRED_TYPE_GENERIC, 0, &cred)) {
        const DWORD err = GetLastError();
        if (err == ERROR_NOT_FOUND) return std::nullopt;
        throw std::runtime_error("Windows Credential Manager refused to read '" + target +
                                 "' (error " + std::to_string(err) + ").");
    }
    std::optional<std::string> out;
    const bool userOk = !requireUser ||
                        (cred->UserName && narrow(cred->UserName, wcslen(cred->UserName)) == *requireUser);
    if (userOk && cred->CredentialBlob) {
        out = narrow(reinterpret_cast<const wchar_t*>(cred->CredentialBlob),
                     cred->CredentialBlobSize / sizeof(wchar_t));
    }
    CredFree(cred);
    return out;
}

std::optional<std::string> osRead(const std::string& service, const std::string& username) {
    if (auto v = readTarget(service, &username)) return v;
    return readTarget(username + "@" + service, nullptr);
}
#else
std::string shellQuote(const std::string& s) {
    std::string out = "'";
    for (char c : s) out += (c == '\'') ? std::string("'\\''") : std::string(1, c);
    return out + "'";
}

std::optional<std::string> osRead(const std::string& service, const std::string& username) {
#ifdef __APPLE__
    const ProcessResult r = ProcessRunner::run("security find-generic-password -s " + shellQuote(service) +
                                                   " -a " + shellQuote(username) + " -w",
                                               15000);
    if (r.exitCode == 44) return std::nullopt;  // errSecItemNotFound
    if (r.exitCode != 0) {
        throw std::runtime_error("The macOS Keychain couldn't be read: " + r.stderr_output.substr(0, 200));
    }
#else
    const ProcessResult r = ProcessRunner::run("secret-tool lookup service " + shellQuote(service) +
                                                   " username " + shellQuote(username),
                                               15000);
    if (r.exitCode == 127) {
        throw std::runtime_error(
            "Reading keyring secrets needs secret-tool (package libsecret-tools) and a running "
            "Secret Service such as GNOME Keyring or KWallet. Install it, or use ${ENV_VAR} "
            "references instead.");
    }
    if (r.exitCode == 1 && r.stdout_output.empty()) {
        if (r.stderr_output.find("ecret") != std::string::npos ||
            r.stderr_output.find("bus") != std::string::npos) {
            throw std::runtime_error("The Secret Service isn't reachable: " +
                                     r.stderr_output.substr(0, 200) +
                                     " Start GNOME Keyring (or KWallet) in this session.");
        }
        return std::nullopt;
    }
    if (r.exitCode != 0) {
        throw std::runtime_error("secret-tool failed: " + r.stderr_output.substr(0, 200));
    }
#endif
    std::string v = r.stdout_output;
    while (!v.empty() && (v.back() == '\n' || v.back() == '\r')) v.pop_back();
    return v;
}
#endif

}  // namespace

void setKeyringBackendForTesting(KeyringBackend backend) {
    std::lock_guard<std::mutex> lock(backendMutex());
    testBackend() = std::move(backend);
}

std::optional<std::string> readKeyringSecret(const std::string& service, const std::string& username) {
    {
        std::lock_guard<std::mutex> lock(backendMutex());
        if (testBackend()) return testBackend()(service, username);
    }
    return osRead(service, username);
}

std::string resolveKeyringReference(const std::string& reference) {
    const size_t colon = reference.find(':');
    if (colon == std::string::npos || colon == 0 || colon + 1 >= reference.size()) {
        throw std::runtime_error("Keyring reference '" + reference +
                                 "' must look like <service>:<name>, e.g. "
                                 "gaia.connections:mcp-github:GITHUB_PERSONAL_ACCESS_TOKEN.");
    }
    const std::string service = reference.substr(0, colon);
    const std::string username = reference.substr(colon + 1);
    std::optional<std::string> secret = readKeyringSecret(service, username);
    if (!secret || secret->empty()) {
        throw std::runtime_error("The OS keyring has no secret for " + reference +
                                 ". Reconnect the connector (gaia connectors), or set the value "
                                 "with ${ENV_VAR} in mcp.json.");
    }
    return *secret;
}

} // namespace gaia
