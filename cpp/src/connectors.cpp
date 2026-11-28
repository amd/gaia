// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Read-only port of gaia.connectors: daemon_keyring.py, vault.py (reads),
// store.py (legacy slot reads), accounts.py (default account), grants.py
// (ledger reads), providers/ (client config) and api.get_access_token.

#include "gaia/connectors.h"

#include <algorithm>
#include <array>
#include <cstring>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <map>
#include <mutex>
#include <set>
#include <sstream>

#ifdef CPPHTTPLIB_OPENSSL_SUPPORT
#include <openssl/evp.h>
#include <openssl/rand.h>
#endif

#include "gaia/http_client.h"
#include "gaia/keyring.h"
#include "gaia/types.h"

namespace fs = std::filesystem;

namespace gaia::connectors {

namespace {

constexpr const char* kService = "gaia.connections";
constexpr const char* kMasterKeyUsername = "vault-master-key";
constexpr const char* kDefaultSlot = "default";
constexpr const char* kVaultMagic = "GAIAV1";
constexpr const char* kVaultAad = "gaia.connections:vault:v1";
constexpr const char* kChunkSentinel = "gaia-chunked-v2:";
const std::string kLegacyChunkSentinel = std::string("\0gaia-chunked\0", 14);
constexpr int kExpiryBufferSeconds = 60;
constexpr const char* kConnectionsDocs = "docs/sdk/infrastructure/connections.mdx";

// ---- where things live ----

fs::path homeDir() {
#ifdef _WIN32
    return fs::u8path(getEnvVar("USERPROFILE"));
#else
    return fs::u8path(getEnvVar("HOME"));
#endif
}

fs::path connectorsDir() { return homeDir() / ".gaia" / "connectors"; }

fs::path vaultPath() {
    const std::string override = getEnvVar("GAIA_VAULT_FILE");
    if (!override.empty()) return fs::u8path(override);
    return connectorsDir() / "vault.enc";
}

std::optional<std::string> readFile(const fs::path& path) {
    std::error_code ec;
    if (!fs::exists(path, ec)) return std::nullopt;
    std::ifstream in(path, std::ios::binary);
    if (!in) throw ConnectorsError("Could not read " + path.u8string() + ". Check its permissions.");
    return std::string((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
}

// ---- the keychain: the daemon's route, else the OS ----

std::mutex& stateMutex() {
    static std::mutex m;
    return m;
}

struct State {
    SecretReader testReader;
    std::string daemonBase;
    std::string daemonSecret;
    mail::Transport tokenTransport;
    std::optional<std::string> vaultKey;  // raw 32 bytes, as last read
    struct Cached {
        std::string token;
        std::chrono::steady_clock::time_point expiresAt;
    };
    std::map<std::string, Cached> tokens;  // "<provider>:<slot>"
};

State& state() {
    static State s;
    return s;
}

std::optional<std::string> daemonRead(const std::string& base, const std::string& secret,
                                      const std::string& service, const std::string& username) {
    HttpClientConfig cfg;
    cfg.baseUrl = base;
    // Long enough for a person to answer the macOS dialog the daemon may be behind.
    cfg.timeoutSec = 300;
    cfg.connectTimeoutSec = 5;
    HttpClient client(cfg);
    const json body = {{"service", service}, {"username", username}};
    HttpResponse resp;
    try {
        resp = client.post("/host/v1/keychain/get", body.dump(),
                           {{"Authorization", "Bearer " + secret}, {"Content-Type", "application/json"}});
    } catch (const HttpError& e) {
        if (e.status() == 0) {
            throw std::runtime_error("the GAIA daemon at " + base +
                                     " did not answer a keychain read (" + std::string(e.what()) +
                                     "). If macOS is showing a keychain dialog, answer it. Otherwise run "
                                     "`gaia daemon status`, then restart the agent.");
        }
        std::string detail = e.body();
        try {
            const json j = json::parse(e.body());
            if (j.is_object() && j.contains("detail")) {
                detail = j["detail"].is_string() ? j["detail"].get<std::string>() : j["detail"].dump();
            }
        } catch (const json::exception&) {
        }
        throw std::runtime_error("the GAIA daemon refused a keychain read (HTTP " + std::to_string(e.status()) +
                                 "): " + detail);
    }
    json j;
    try {
        j = json::parse(resp.body);
    } catch (const json::exception&) {
        throw std::runtime_error("the GAIA daemon answered a keychain read with something that is not JSON");
    }
    if (!j.is_object() || !j.contains("value")) {
        throw std::runtime_error("the GAIA daemon answered a keychain read without a value");
    }
    if (j["value"].is_null()) return std::nullopt;
    return j["value"].get<std::string>();
}

std::optional<std::string> rawGet(const std::string& username) {
    SecretReader test;
    std::string base, secret;
    {
        std::lock_guard<std::mutex> lock(stateMutex());
        test = state().testReader;
        base = state().daemonBase;
        secret = state().daemonSecret;
    }
    try {
        if (test) return test(kService, username);
        if (!base.empty()) return daemonRead(base, secret, kService, username);
        return readKeyringSecret(kService, username);
    } catch (const ConnectorsError&) {
        throw;
    } catch (const std::exception& e) {
        throw ConnectorsError(std::string("Keyring get_password failed: ") + e.what() +
                              ". Install a system credential store (gnome-keyring on Linux, or rely on the "
                              "macOS Keychain / Windows Credential Locker), configure it, and restart GAIA. See "
                              "docs/security/connections.mdx.");
    }
}

// ---- the vault ----

std::string parseMasterKey(const std::string& raw) {
    std::string hex;
    for (char c : raw) {
        if (c != ' ' && c != '\n' && c != '\r' && c != '\t') hex += c;
    }
    auto bad = [] {
        return ConnectorsError(std::string("The stored vault master key `") + kMasterKeyUsername +
                               "` in service `" + kService +
                               "` is corrupt. Delete it from your OS credential store and reconnect your accounts.");
    };
    if (hex.size() % 2 != 0) throw bad();
    std::string key;
    for (size_t i = 0; i < hex.size(); i += 2) {
        auto nib = [&](char c) -> int {
            if (c >= '0' && c <= '9') return c - '0';
            if (c >= 'a' && c <= 'f') return c - 'a' + 10;
            if (c >= 'A' && c <= 'F') return c - 'A' + 10;
            return -1;
        };
        const int hi = nib(hex[i]), lo = nib(hex[i + 1]);
        if (hi < 0 || lo < 0) throw bad();
        key += static_cast<char>((hi << 4) | lo);
    }
    if (key.size() != 32) {
        throw ConnectorsError(std::string("The stored vault master key `") + kMasterKeyUsername +
                              "` in service `" + kService +
                              "` has the wrong length. Delete it from your OS credential store and reconnect your "
                              "accounts.");
    }
    return key;
}

#ifdef CPPHTTPLIB_OPENSSL_SUPPORT
std::optional<std::string> aesGcmDecrypt(const std::string& key, const std::string& nonce,
                                         const std::string& cipherAndTag) {
    if (cipherAndTag.size() < 16) return std::nullopt;
    const std::string cipher = cipherAndTag.substr(0, cipherAndTag.size() - 16);
    std::string tag = cipherAndTag.substr(cipherAndTag.size() - 16);
    EVP_CIPHER_CTX* ctx = EVP_CIPHER_CTX_new();
    if (!ctx) return std::nullopt;
    std::string out(cipher.size() + 16, '\0');
    int len = 0, total = 0;
    const auto* k = reinterpret_cast<const unsigned char*>(key.data());
    const auto* n = reinterpret_cast<const unsigned char*>(nonce.data());
    bool ok = EVP_DecryptInit_ex(ctx, EVP_aes_256_gcm(), nullptr, nullptr, nullptr) == 1 &&
              EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN, static_cast<int>(nonce.size()), nullptr) == 1 &&
              EVP_DecryptInit_ex(ctx, nullptr, nullptr, k, n) == 1 &&
              EVP_DecryptUpdate(ctx, nullptr, &len, reinterpret_cast<const unsigned char*>(kVaultAad),
                                static_cast<int>(std::strlen(kVaultAad))) == 1 &&
              EVP_DecryptUpdate(ctx, reinterpret_cast<unsigned char*>(&out[0]), &len,
                                reinterpret_cast<const unsigned char*>(cipher.data()),
                                static_cast<int>(cipher.size())) == 1;
    total = len;
    ok = ok && EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_TAG, 16, &tag[0]) == 1 &&
         EVP_DecryptFinal_ex(ctx, reinterpret_cast<unsigned char*>(&out[0]) + total, &len) == 1;
    EVP_CIPHER_CTX_free(ctx);
    if (!ok) return std::nullopt;
    out.resize(static_cast<size_t>(total + len));
    return out;
}
#endif

std::map<std::string, std::string> decryptVault(const std::string& key, const std::string& blob,
                                                const fs::path& path) {
    const size_t header = std::strlen(kVaultMagic) + 1;
    if (blob.size() < header + 12 + 16 || blob.compare(0, std::strlen(kVaultMagic), kVaultMagic) != 0) {
        throw ConnectorsError("The encrypted credential vault at " + path.u8string() +
                              " is corrupt or truncated. Remove the file and reconnect your accounts.");
    }
    const int version = static_cast<unsigned char>(blob[std::strlen(kVaultMagic)]);
    if (version != 1) {
        throw ConnectorsError("Unsupported credential vault version " + std::to_string(version) + " at " +
                              path.u8string() + ". Update GAIA to a version that supports this vault format.");
    }
#ifdef CPPHTTPLIB_OPENSSL_SUPPORT
    const std::optional<std::string> plain = aesGcmDecrypt(key, blob.substr(header, 12), blob.substr(header + 12));
#else
    (void)key;
    const std::optional<std::string> plain;
    throw ConnectorsError("This gaiacpp was built without OpenSSL, so it cannot open the credential vault at " +
                          path.u8string() + ". Rebuild it with OpenSSL installed.");
#endif
    if (!plain) {
        throw ConnectorsError("Could not decrypt the credential vault at " + path.u8string() +
                              ": the master key changed or the file was tampered with. Delete `" +
                              kMasterKeyUsername + "` from your OS credential store and remove " + path.u8string() +
                              ", then reconnect your accounts.");
    }
    std::map<std::string, std::string> entries;
    try {
        const json parsed = json::parse(*plain);
        if (!parsed.is_object() || parsed.value("v", 0) != 1 || !parsed.contains("entries") ||
            !parsed["entries"].is_object()) {
            throw std::runtime_error("unexpected vault payload shape");
        }
        for (const auto& [k, v] : parsed["entries"].items()) {
            if (!v.is_string()) throw std::runtime_error("vault entries must map strings to strings");
            entries[k] = v.get<std::string>();
        }
    } catch (const std::exception& e) {
        throw ConnectorsError("The credential vault payload at " + path.u8string() + " is malformed (" + e.what() +
                              "). Remove the file and reconnect your accounts.");
    }
    return entries;
}

std::optional<std::map<std::string, std::string>> vaultEntries() {
    const fs::path path = vaultPath();
    const std::optional<std::string> blob = readFile(path);
    if (!blob) return std::nullopt;
    std::optional<std::string> cached;
    {
        std::lock_guard<std::mutex> lock(stateMutex());
        cached = state().vaultKey;
    }
    auto freshKey = [&]() -> std::string {
        const std::optional<std::string> raw = rawGet(kMasterKeyUsername);
        if (!raw || raw->empty()) {
            throw ConnectorsError("The credential vault at " + path.u8string() + " exists, but its master key `" +
                                  kMasterKeyUsername + "` is missing from your OS credential store. Remove " +
                                  path.u8string() + " and reconnect your accounts.");
        }
        std::string key = parseMasterKey(*raw);
        std::lock_guard<std::mutex> lock(stateMutex());
        state().vaultKey = key;
        return key;
    };
    if (cached) {
        try {
            return decryptVault(*cached, *blob, path);
        } catch (const ConnectorsError&) {
            // Another process may have re-minted the vault under a new key.
            const std::string key = freshKey();
            if (key == *cached) throw;
            return decryptVault(key, *blob, path);
        }
    }
    return decryptVault(freshKey(), *blob, path);
}

std::optional<std::string> legacyGet(const std::string& username) {
    const std::optional<std::string> raw = rawGet(username);
    if (!raw || raw->empty()) return std::nullopt;
    const bool v2 = raw->rfind(kChunkSentinel, 0) == 0;
    const bool v1 = raw->rfind(kLegacyChunkSentinel, 0) == 0;
    if (!v2 && !v1) return raw;
    const std::string body = raw->substr(v2 ? std::strlen(kChunkSentinel) : kLegacyChunkSentinel.size());
    const size_t colon = body.find(':');
    const std::string countText = body.substr(0, colon);
    const std::string crc = colon == std::string::npos ? "" : body.substr(colon + 1);
    int count = 0;
    try {
        size_t used = 0;
        count = std::stoi(countText, &used);
        if (used != countText.size()) return std::nullopt;
    } catch (const std::exception&) {
        return std::nullopt;
    }
    std::string value;
    for (int i = 0; i < count; ++i) {
        const std::optional<std::string> part = rawGet(username + "#" + std::to_string(i));
        if (!part) return std::nullopt;
        value += *part;
    }
    if (!crc.empty() && crc32Hex(value) != crc) return std::nullopt;
    return value;
}

/// vault.get without the migration write: the vault first, then a legacy item.
std::optional<std::string> readSecret(const std::string& username) {
    if (auto entries = vaultEntries()) {
        auto it = entries->find(username);
        if (it != entries->end()) return it->second;
    }
    return legacyGet(username);
}

std::optional<json> readJsonSecret(const std::string& username) {
    const std::optional<std::string> raw = readSecret(username);
    if (!raw) return std::nullopt;
    try {
        json j = json::parse(*raw);
        if (j.is_object()) return j;
    } catch (const json::exception&) {
    }
    return std::nullopt;
}

// ---- providers ----

struct Provider {
    std::string id;
    std::string clientId;
    std::string clientSecret;
    std::string tenant;  // Microsoft only
    std::string tokenUrl;
    std::string clientIdHash;
};

std::string stringField(const json& j, const char* key) {
    return j.contains(key) && j[key].is_string() ? j[key].get<std::string>() : std::string();
}

Provider loadProvider(const std::string& id) {
    if (id != mail::kGoogle && id != mail::kMicrosoft) {
        throw ConnectorsError("No OAuth provider is registered for '" + id + "'.", "ConfigurationError");
    }
    const json stored = readJsonSecret("provider:" + id).value_or(json::object());
    std::string prefix = "GAIA_" + id;
    for (auto& c : prefix) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    Provider p;
    p.id = id;
    p.clientId = stringField(stored, "client_id");
    if (p.clientId.empty()) p.clientId = getEnvVar((prefix + "_CLIENT_ID").c_str());
    if (p.clientId.empty()) {
        const std::string label = id == mail::kGoogle ? "Google" : "Microsoft";
        throw ConnectorsError("No OAuth client is configured for " + label + ", so its connection can't be used. "
                              "Set it up under Settings > Connectors, or run `gaia connectors configure " + id +
                              "`. See https://amd-gaia.ai/docs/connectors/" + id,
                              "OAuthClientNotConfiguredError");
    }
    p.clientSecret = stringField(stored, "client_secret");
    if (p.clientSecret.empty()) p.clientSecret = getEnvVar((prefix + "_CLIENT_SECRET").c_str());
    p.clientIdHash = crc32Hex(p.clientId);
    if (id == mail::kGoogle) {
        p.tokenUrl = "https://oauth2.googleapis.com/token";
    } else {
        p.tenant = stringField(stored, "tenant");
        if (p.tenant.empty()) p.tenant = "consumers";  // the microsoft connector's oauth_tenant
        p.tokenUrl = "https://login.microsoftonline.com/" + p.tenant + "/oauth2/v2.0/token";
    }
    return p;
}

// ---- accounts (accounts.py) ----

json loadIndex() {
    const fs::path path = connectorsDir() / "accounts.json";
    const std::optional<std::string> raw = readFile(path);
    if (!raw) return json{{"version", 1}, {"connectors", json::object()}};
    json data;
    try {
        data = json::parse(*raw);
    } catch (const json::exception& e) {
        throw ConnectorsError("The connected-account list at " + path.u8string() + " could not be read (" +
                              e.what() + "). Delete it to rebuild it (`rm " + path.u8string() +
                              "`): accounts other than each connector's first then have to be connected again.");
    }
    if (!data.is_object() || !data.contains("connectors") || !data["connectors"].is_object()) {
        throw ConnectorsError("The connected-account list at " + path.u8string() +
                              " has the wrong shape. Delete it to rebuild it (`rm " + path.u8string() + "`).");
    }
    return data;
}

json sectionOf(const json& index, const std::string& provider) {
    const json& c = index["connectors"];
    return c.contains(provider) && c[provider].is_object() ? c[provider] : json::object();
}

std::string defaultOf(const json& section, const std::vector<std::string>& slots) {
    const std::string chosen = stringField(section, "default_slot");
    if (!chosen.empty() && std::find(slots.begin(), slots.end(), chosen) != slots.end()) return chosen;
    if (std::find(slots.begin(), slots.end(), kDefaultSlot) != slots.end()) return kDefaultSlot;
    return slots.empty() ? std::string() : slots.front();
}

std::string defaultSlot(const std::string& provider) {
    const json section = sectionOf(loadIndex(), provider);
    std::vector<std::string> slots;
    if (section.contains("accounts") && section["accounts"].is_array()) {
        for (const auto& e : section["accounts"]) slots.push_back(stringField(e, "slot"));
    }
    const std::string slot = defaultOf(section, slots);
    return slot.empty() ? kDefaultSlot : slot;
}

/// The index entry for `slot`, including the legacy default slot the index never recorded.
std::optional<json> accountEntry(const std::string& provider, const std::string& slot) {
    const json section = sectionOf(loadIndex(), provider);
    if (section.contains("accounts") && section["accounts"].is_array()) {
        for (const auto& e : section["accounts"]) {
            if (stringField(e, "slot") == slot) return e;
        }
    }
    if (slot == kDefaultSlot && readJsonSecret(provider + ":" + slot)) {
        return json{{"slot", kDefaultSlot}, {"state", "ok"}};
    }
    return std::nullopt;
}

bool forwardInterrupted(const std::string& provider) {
    return readSecret("recovery:forward:" + provider).has_value();
}

std::string busyMessage(const std::string& provider) {
    return "The " + provider +
           " connection is being saved by another GAIA operation (in this process or another) right now, so it "
           "was left as is. Retry in a moment; if this keeps happening, look for a stuck `gaia daemon` or Agent UI "
           "process.";
}

bool disagrees(const Provider& p, const json& stored) {
    if (stringField(stored, "client_id_hash") != p.clientIdHash) return true;
    const std::string storedTenant = stringField(stored, "tenant");
    return !storedTenant.empty() && !p.tenant.empty() && storedTenant != p.tenant;
}

std::vector<std::string> scopesOf(const json& stored) {
    std::vector<std::string> out;
    if (stored.contains("scopes") && stored["scopes"].is_array()) {
        for (const auto& s : stored["scopes"]) {
            if (s.is_string()) out.push_back(s.get<std::string>());
        }
    }
    return out;
}

std::string joined(const std::vector<std::string>& items, const std::string& sep) {
    std::string out;
    for (const auto& s : items) out += (out.empty() ? "" : sep) + s;
    return out;
}

ConnectorsError authRequired(const std::string& reason, const std::string& provider, const std::string& agent = "",
                             const std::vector<std::string>& missing = {},
                             const std::vector<std::string>& full = {}) {
    const std::string pid = provider.empty() ? "<provider>" : provider;
    std::string m;
    if (reason == "not_connected") {
        m = "No " + provider + " connection. Connect via Settings → Connectors in AgentUI, or run `gaia connectors "
            "connect " + pid + "`. See " + kConnectionsDocs + ".";
    } else if (reason == "agent_not_granted") {
        const std::vector<std::string>& scopes = full.empty() ? missing : full;
        m = "Agent '" + (agent.empty() ? std::string("this agent") : agent) + "' has no grant for " + provider +
            ". Grant the required scopes in Settings → Connectors, or run `gaia connectors grants grant " + pid + " " +
            agent + " --scopes " + joined(scopes, " ") + "`. See " + kConnectionsDocs + ".";
    } else if (reason == "connection_missing_scopes") {
        m = "The " + provider + " connection lacks required scopes (" + joined(missing, ", ") +
            "). Reconnect with the missing scopes from Settings → Connectors, or run `gaia connectors connect " + pid +
            " --scopes " + joined(full, " ") + "`. See " + kConnectionsDocs + ".";
    } else if (reason == "tenant_mismatch") {
        m = "The stored " + provider + " connection was authenticated against a different Microsoft tenant than the "
            "connector currently resolves to. Reconnect from Settings → Connectors, or run `gaia connectors connect " +
            pid + "`. See docs/connectors/microsoft.mdx.";
    } else {
        m = "The stored " + provider + " credentials are no longer valid (client rotation or remote revocation). "
            "Reconnect from Settings → Connectors, or run `gaia connectors connect " + pid +
            "`. See docs/runbooks/google-oauth-client.md.";
    }
    return ConnectorsError(m, "AuthRequiredError");
}

/// tokens.load_current_connection without the clearing writes: the connection,
/// or nullopt for none, throwing busy or a tripwire as Python does.
std::optional<json> loadCurrentConnection(const Provider& p, const std::string& slot) {
    if (forwardInterrupted(p.id)) throw ConnectorsError(busyMessage(p.id), "ConnectionBusyError");
    std::optional<json> stored = readJsonSecret(p.id + ":" + slot);
    if (!stored) return std::nullopt;
    if (stringField(*stored, "client_id_hash") != p.clientIdHash) throw authRequired("reauth_required", p.id);
    if (disagrees(p, *stored)) throw authRequired("tenant_mismatch", p.id);
    return stored;
}

mail::Transport tokenTransport() {
    std::lock_guard<std::mutex> lock(stateMutex());
    return state().tokenTransport ? state().tokenTransport : mail::httpsTransport();
}

struct Refreshed {
    std::string accessToken;
    int expiresIn = 3600;
};

Refreshed refreshToken(const Provider& p, const std::string& refreshToken) {
    std::vector<std::pair<std::string, std::string>> form = {
        {"grant_type", "refresh_token"}, {"refresh_token", refreshToken}, {"client_id", p.clientId}};
    if (!p.clientSecret.empty()) form.emplace_back("client_secret", p.clientSecret);
    mail::HttpCall call;
    call.method = "POST";
    call.url = p.tokenUrl;
    call.headers = {{"Content-Type", "application/x-www-form-urlencoded"}, {"Accept", "application/json"}};
    call.body = mail::queryString(form).substr(1);
    HttpResponse resp;
    try {
        resp = tokenTransport()(call);
    } catch (const std::exception& e) {
        throw ConnectorsError("Token endpoint for " + p.id + " could not be reached (" + e.what() +
                                  "). Check your connection and retry.",
                              "ConnectorsError");
    }
    json payload = json::object();
    try {
        payload = json::parse(resp.body);
        if (!payload.is_object()) payload = json::object();
    } catch (const json::exception&) {
    }
    if (resp.status == 400) {
        if (stringField(payload, "error") == "invalid_grant") {
            throw ConnectorsError("The " + p.id +
                                      " connection was revoked or its refresh token is no longer accepted by the "
                                      "provider. Reconnect from Settings → Connectors, or run `gaia connectors "
                                      "connect " + p.id + "`. See docs/security/connections.mdx.",
                                  "ConnectionRevokedError");
        }
        const std::string err = stringField(payload, "error");
        throw ConnectorsError("Token endpoint refused refresh for " + p.id + ": " + (err.empty() ? "unknown" : err) +
                              " (status 400). See docs/security/connections.mdx.");
    }
    if (resp.status == 401) {
        if (p.clientSecret.empty() && p.id == mail::kGoogle) {
            throw ConnectorsError("Token endpoint returned 401 for " + p.id +
                                      ": client_secret is not configured. Open Settings → Connectors → " + p.id +
                                      " and re-enter the Client Secret, or set the GAIA_GOOGLE_CLIENT_SECRET "
                                      "environment variable. See docs/runbooks/google-oauth-client.md.",
                                  "ConfigurationError");
        }
        const std::string err = stringField(payload, "error");
        throw ConnectorsError("Token endpoint returned 401 for " + p.id + " (" +
                                  (err.empty() ? "invalid_client" : err) +
                                  "). Reconnect from Settings → Connectors → " + p.id +
                                  ". See docs/runbooks/google-oauth-client.md.",
                              "AuthRequiredError");
    }
    if (resp.status != 200) {
        throw ConnectorsError("Token endpoint returned " + std::to_string(resp.status) + " for " + p.id +
                              " refresh. See docs/security/connections.mdx.");
    }
    Refreshed out;
    out.accessToken = stringField(payload, "access_token");
    if (out.accessToken.empty()) {
        throw ConnectorsError("Token endpoint response for " + p.id +
                              " omitted access_token. See docs/security/connections.mdx.");
    }
    if (payload.contains("expires_in") && payload["expires_in"].is_number() && payload["expires_in"].get<double>() > 0) {
        out.expiresIn = static_cast<int>(payload["expires_in"].get<double>());
    }
    return out;
}

}  // namespace

// ---- public ----

std::string crc32Hex(const std::string& data) {
    static const std::array<uint32_t, 256> table = [] {
        std::array<uint32_t, 256> t{};
        for (uint32_t i = 0; i < 256; ++i) {
            uint32_t c = i;
            for (int k = 0; k < 8; ++k) c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
            t[i] = c;
        }
        return t;
    }();
    uint32_t crc = 0xFFFFFFFFu;
    for (unsigned char ch : data) crc = table[(crc ^ ch) & 0xFF] ^ (crc >> 8);
    crc ^= 0xFFFFFFFFu;
    char buf[9];
    std::snprintf(buf, sizeof buf, "%08x", crc);
    return buf;
}

bool installDaemonKeychainFromEnv() {
    const std::string path = getEnvVar("GAIA_HOST_KEYCHAIN_SECRET_FILE");
    if (path.empty()) return false;
#ifdef _WIN32
    _putenv_s("GAIA_HOST_KEYCHAIN_SECRET_FILE", "");
#else
    unsetenv("GAIA_HOST_KEYCHAIN_SECRET_FILE");
#endif
    std::string secret;
    bool opened = false;
    {
        std::ifstream in(fs::u8path(path), std::ios::binary);
        opened = static_cast<bool>(in);
        if (opened) secret.assign((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    }
    // Closed first: Windows refuses to delete a file that is still open.
    std::error_code ec;
    if (!opened || !fs::remove(fs::u8path(path), ec)) {
        throw ConnectorsError("Could not read the keychain credential the GAIA daemon left at " + path + ": " +
                              (opened ? ec.message() : std::string("it can't be opened")) +
                              ". Restart the agent with `gaia daemon restart`.");
    }
    // An explicit keyring backend keeps a test run on its own store, as in Python.
    if (!getEnvVar("PYTHON_KEYRING_BACKEND").empty()) return false;
    const std::string base = getEnvVar("GAIA_HOST_CUSTODY_URL");
    if (base.empty()) {
        throw ConnectorsError(
            "The GAIA daemon set GAIA_HOST_KEYCHAIN_SECRET_FILE but not GAIA_HOST_CUSTODY_URL, so the agent cannot "
            "reach the keychain. Restart it with `gaia daemon restart`.");
    }
    std::string trimmed = base;
    while (!trimmed.empty() && trimmed.back() == '/') trimmed.pop_back();
    std::lock_guard<std::mutex> lock(stateMutex());
    state().daemonBase = trimmed;
    state().daemonSecret = secret;
    return true;
}

std::optional<Connection> getConnection(const std::string& provider) {
    const std::string slot = defaultSlot(provider);
    const std::optional<json> entry = accountEntry(provider, slot);
    if (!entry) return std::nullopt;
    // accounts._status: a sign-in marked expired, or a token gone, is not a connection.
    const std::optional<json> peek = readJsonSecret(provider + ":" + slot);
    if (stringField(*entry, "state") == "reauth_required" || !peek) return std::nullopt;
    Connection row;
    row.provider = provider;
    row.accountId = stringField(*peek, "account_id");
    if (row.accountId.empty()) row.accountId = stringField(*entry, "account_id");
    if (row.accountId.empty()) row.accountId = kDefaultSlot;
    std::optional<json> stored;
    try {
        stored = loadCurrentConnection(loadProvider(provider), slot);
    } catch (const ConnectorsError& e) {
        if (e.kind() == "ConnectionBusyError") {
            row.error = "busy";
            row.accountEmail = stringField(*peek, "account_email");
            return row;
        }
        if (e.kind() == "ConfigurationError" || e.kind() == "OAuthClientNotConfiguredError") {
            row.error = "configuration";
            return row;
        }
        if (e.kind() == "AuthRequiredError") return std::nullopt;
        throw;
    }
    if (!stored) return std::nullopt;
    row.accountEmail = stringField(*stored, "account_email");
    row.scopes = scopesOf(*stored);
    return row;
}

std::vector<std::string> agentGrants(const std::string& connector, const std::string& agentId) {
    const fs::path path = connectorsDir() / "grants.json";
    const std::optional<std::string> raw = readFile(path);
    if (!raw) return {};
    json data;
    try {
        data = json::parse(*raw);
    } catch (const json::exception& e) {
        throw ConnectorsError("Grants ledger at " + path.u8string() + " is corrupted (" + e.what() +
                              "). Delete the file to reset all per-agent grants:   rm " + path.u8string() +
                              "\nYou will need to re-grant scopes from Settings → Connectors or via `gaia connectors "
                              "grants grant ...`.");
    }
    if (!data.is_object()) {
        throw ConnectorsError("Grants ledger at " + path.u8string() +
                              " has the wrong shape (expected a JSON object). Delete with `rm " + path.u8string() +
                              "` to reset.");
    }
    std::vector<std::string> out;
    if (data.contains(connector) && data[connector].is_object() && data[connector].contains(agentId) &&
        data[connector][agentId].is_array()) {
        for (const auto& s : data[connector][agentId]) {
            if (s.is_string()) out.push_back(s.get<std::string>());
        }
    }
    return out;
}

std::string getAccessToken(const std::string& provider, const std::vector<std::string>& scopes,
                           const std::string& agentId) {
    const std::string slot = defaultSlot(provider);
    if (scopes.empty()) {
        throw ConnectorsError("get_access_token('" + provider + "') named no scopes, so the per-agent grant for '" +
                                  agentId +
                                  "' cannot be verified and no token will be issued. Pass the scopes the caller "
                                  "actually needs, e.g. scopes=['https://www.googleapis.com/auth/gmail.readonly'].",
                              "ConfigurationError");
    }
    const std::vector<std::string> granted = agentGrants(provider, agentId);
    for (const auto& s : scopes) {
        if (std::find(granted.begin(), granted.end(), s) == granted.end()) {
            throw authRequired("agent_not_granted", provider, agentId, scopes);
        }
    }
    const Provider p = loadProvider(provider);
    const std::optional<json> stored = loadCurrentConnection(p, slot);
    if (!stored) throw authRequired("not_connected", provider);
    const std::vector<std::string> have = scopesOf(*stored);
    std::vector<std::string> missing;
    for (const auto& s : scopes) {
        if (std::find(have.begin(), have.end(), s) == have.end()) missing.push_back(s);
    }
    if (!missing.empty()) {
        std::set<std::string> full(have.begin(), have.end());
        full.insert(missing.begin(), missing.end());
        throw authRequired("connection_missing_scopes", provider, agentId, missing,
                           std::vector<std::string>(full.begin(), full.end()));
    }

    static std::mutex refreshMu;
    std::lock_guard<std::mutex> refreshing(refreshMu);
    const std::string key = provider + ":" + slot;
    const auto now = std::chrono::steady_clock::now();
    {
        std::lock_guard<std::mutex> lock(stateMutex());
        auto it = state().tokens.find(key);
        if (it != state().tokens.end() &&
            it->second.expiresAt - now >= std::chrono::seconds(kExpiryBufferSeconds)) {
            return it->second.token;
        }
    }
    Refreshed fresh;
    try {
        fresh = refreshToken(p, stringField(*stored, "refresh_token"));
    } catch (const ConnectorsError& e) {
        const bool protocol = e.kind() != "ConfigurationError" && e.kind() != "AuthRequiredError";
        if (protocol && provider == mail::kMicrosoft && stringField(*stored, "tenant").empty()) {
            throw ConnectorsError(std::string(e.what()) +
                                      " This looks like it could be the Microsoft connector split (#2628): if this "
                                      "account is a work/school account, reconnect via the 'microsoft_work' "
                                      "connector instead.",
                                  e.kind());
        }
        throw;
    }
    std::lock_guard<std::mutex> lock(stateMutex());
    state().tokens[key] = {fresh.accessToken, now + std::chrono::seconds(fresh.expiresIn)};
    return fresh.accessToken;
}

void setSecretReaderForTesting(SecretReader read) {
    std::lock_guard<std::mutex> lock(stateMutex());
    state().testReader = std::move(read);
}

void setTokenTransportForTesting(mail::Transport transport) {
    std::lock_guard<std::mutex> lock(stateMutex());
    state().tokenTransport = std::move(transport);
}

void resetForTesting() {
    std::lock_guard<std::mutex> lock(stateMutex());
    state().tokens.clear();
    state().vaultKey.reset();
    state().daemonBase.clear();
    state().daemonSecret.clear();
}

std::string encryptVaultForTesting(const std::string& key, const std::string& plaintext) {
#ifdef CPPHTTPLIB_OPENSSL_SUPPORT
    unsigned char nonce[12];
    RAND_bytes(nonce, sizeof nonce);
    EVP_CIPHER_CTX* ctx = EVP_CIPHER_CTX_new();
    std::string out(plaintext.size() + 16, '\0');
    int len = 0, total = 0;
    EVP_EncryptInit_ex(ctx, EVP_aes_256_gcm(), nullptr, nullptr, nullptr);
    EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN, 12, nullptr);
    EVP_EncryptInit_ex(ctx, nullptr, nullptr, reinterpret_cast<const unsigned char*>(key.data()), nonce);
    EVP_EncryptUpdate(ctx, nullptr, &len, reinterpret_cast<const unsigned char*>(kVaultAad),
                      static_cast<int>(std::strlen(kVaultAad)));
    EVP_EncryptUpdate(ctx, reinterpret_cast<unsigned char*>(&out[0]), &len,
                      reinterpret_cast<const unsigned char*>(plaintext.data()), static_cast<int>(plaintext.size()));
    total = len;
    EVP_EncryptFinal_ex(ctx, reinterpret_cast<unsigned char*>(&out[0]) + total, &len);
    total += len;
    unsigned char tag[16];
    EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_GET_TAG, 16, tag);
    EVP_CIPHER_CTX_free(ctx);
    out.resize(static_cast<size_t>(total));
    return std::string(kVaultMagic) + '\x01' + std::string(reinterpret_cast<char*>(nonce), 12) + out +
           std::string(reinterpret_cast<char*>(tag), 16);
#else
    (void)key;
    (void)plaintext;
    throw ConnectorsError("built without OpenSSL");
#endif
}

}  // namespace gaia::connectors
