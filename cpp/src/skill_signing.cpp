// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/skill_signing.h"

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <set>
#include <sstream>
#include <vector>

#include <nlohmann/json.hpp>

#ifdef CPPHTTPLIB_OPENSSL_SUPPORT
#include <openssl/evp.h>
#endif

namespace fs = std::filesystem;
using json = nlohmann::json;

namespace gaia {

namespace {

constexpr const char* kSignatureFile = "SIGNATURE.json";
constexpr const char* kTrustStoreFile = "trusted-keys.json";
constexpr int kSignatureVersion = 1;
constexpr const char* kDocs = "https://amd-gaia.ai/docs/plans/skill-format#security-tiers";

// Mirrors BUILTIN_TRUSTED_KEYS in src/gaia/skills/signing.py.
const std::map<std::string, std::pair<std::string, std::string>>& builtinKeys() {
    static const std::map<std::string, std::pair<std::string, std::string>> keys = {
        {"32197c4382b36336fa69e041750dd0d9", {"AMD", "amd"}},
    };
    return keys;
}

[[noreturn]] void fail(const std::string& msg) { throw SkillError(msg + " See " + kDocs); }

#ifdef CPPHTTPLIB_OPENSSL_SUPPORT
std::string sha256Hex(const std::string& data) {
    unsigned char md[EVP_MAX_MD_SIZE];
    unsigned int n = 0;
    if (EVP_Digest(data.data(), data.size(), md, &n, EVP_sha256(), nullptr) != 1) {
        throw SkillError("SHA-256 failed in OpenSSL");
    }
    static const char* hex = "0123456789abcdef";
    std::string out;
    for (unsigned int i = 0; i < n; ++i) {
        out.push_back(hex[md[i] >> 4]);
        out.push_back(hex[md[i] & 0xF]);
    }
    return out;
}

std::string unb64(const json& value, const std::string& what) {
    if (!value.is_string()) fail(what + " is missing or not a string.");
    const std::string in = value.get<std::string>();
    if (in.empty() || in.size() % 4 != 0) fail(what + " is not valid base64.");
    std::string out(in.size() / 4 * 3, '\0');
    const int n = EVP_DecodeBlock(reinterpret_cast<unsigned char*>(&out[0]),
                                  reinterpret_cast<const unsigned char*>(in.data()),
                                  static_cast<int>(in.size()));
    if (n < 0) fail(what + " is not valid base64.");
    size_t len = static_cast<size_t>(n);
    if (in.size() >= 1 && in[in.size() - 1] == '=') --len;
    if (in.size() >= 2 && in[in.size() - 2] == '=') --len;
    out.resize(len);
    return out;
}

bool ed25519Verify(const std::string& publicKey, const std::string& signature,
                   const std::string& message) {
    EVP_PKEY* key = EVP_PKEY_new_raw_public_key(
        EVP_PKEY_ED25519, nullptr, reinterpret_cast<const unsigned char*>(publicKey.data()),
        publicKey.size());
    if (!key) return false;
    EVP_MD_CTX* ctx = EVP_MD_CTX_new();
    bool ok = ctx && EVP_DigestVerifyInit(ctx, nullptr, nullptr, nullptr, key) == 1 &&
              EVP_DigestVerify(ctx, reinterpret_cast<const unsigned char*>(signature.data()),
                               signature.size(),
                               reinterpret_cast<const unsigned char*>(message.data()),
                               message.size()) == 1;
    EVP_MD_CTX_free(ctx);
    EVP_PKEY_free(key);
    return ok;
}
#else
std::string sha256Hex(const std::string&) {
    throw SkillError("This build has no OpenSSL, so it can't hash or verify skill bundles. "
                     "Rebuild gaia_core with OpenSSL.");
}
#endif

std::string readFile(const fs::path& p) {
    std::ifstream in(p, std::ios::binary);
    if (!in) throw SkillError("Can't read " + p.string());
    std::ostringstream s;
    s << in.rdbuf();
    return s.str();
}

// key id -> role, from the trust store plus the built-in key.
std::string roleFor(const std::string& keyId, const std::string& trustRoot) {
    const fs::path store = fs::path(trustRoot) / kTrustStoreFile;
    std::error_code ec;
    if (fs::is_regular_file(store, ec)) {
        json data;
        try {
            data = json::parse(readFile(store));
        } catch (const json::parse_error& e) {
            throw SkillError("Trust store " + store.string() + " is not readable JSON: " + e.what() +
                             ". Fix or delete it; GAIA won't silently trust nothing.");
        }
        for (const auto& k : data.value("keys", json::array())) {
            const std::string role = k.value("role", "");
            if (k.value("key_id", "").empty()) {
                throw SkillError("Trust store " + store.string() + ": an entry is missing 'key_id'.");
            }
            if (role != "amd" && role != "publisher") {
                throw SkillError("Trust store " + store.string() + ": key " + k.value("key_id", "") +
                                 " has role '" + role + "'; valid roles are amd, publisher.");
            }
            if (k.value("key_id", "") == keyId) return role;
        }
    }
    auto it = builtinKeys().find(keyId);
    return it == builtinKeys().end() ? "" : it->second.second;
}

}  // namespace

std::string skillSha256Hex(const std::string& data) { return sha256Hex(data); }

std::string SkillSignature::tier() const {
    if (!signedBundle) return "experimental";
    if (role == "amd") return "verified";
    if (role == "publisher") return "community";
    return "experimental";
}

std::map<std::string, std::string> skillFileDigests(const std::string& directory) {
    std::map<std::string, std::string> out;
    std::error_code ec;
    for (const auto& e : fs::recursive_directory_iterator(directory, ec)) {
        if (!e.is_regular_file(ec)) continue;
        const std::string rel = fs::relative(e.path(), directory, ec).generic_string();
        if (rel == kSignatureFile) continue;
        out[rel] = sha256Hex(readFile(e.path()));
    }
    return out;
}

std::string skillSigningPayload(const std::string& name, const std::string& version,
                                const std::map<std::string, std::string>& digests) {
    json doc = {{"algorithm", "ed25519"},
                {"files", digests},
                {"name", name},
                {"version", version},
                {"signature_version", kSignatureVersion}};
    // Python: json.dumps(sort_keys=True, separators=(",", ":")) with ensure_ascii.
    return doc.dump(-1, ' ', true);
}

SkillSignature verifySkillBundle(const std::string& directory, const std::string& name,
                                 const std::string& version, const std::string& trustRoot) {
    const fs::path sigPath = fs::path(directory) / kSignatureFile;
    std::error_code ec;
    if (!fs::is_regular_file(sigPath, ec)) return {};
#ifndef CPPHTTPLIB_OPENSSL_SUPPORT
    (void)name;
    (void)version;
    (void)trustRoot;
    throw SkillError("This build has no OpenSSL, so it can't verify " + sigPath.string() +
                     ". Rebuild gaia_core with OpenSSL.");
#else
    json rec;
    try {
        rec = json::parse(readFile(sigPath));
    } catch (const json::parse_error& e) {
        fail(sigPath.string() + " is not readable JSON: " + e.what() + ". Re-download the skill.");
    }
    if (!rec.is_object()) fail(sigPath.string() + " must contain a JSON object.");
    if (rec.value("algorithm", "") != "ed25519") {
        fail(sigPath.string() + " declares algorithm '" + rec.value("algorithm", "") +
             "'; this build verifies ed25519 only.");
    }
    if (!rec.contains("signature_version") || rec["signature_version"] != kSignatureVersion) {
        fail(sigPath.string() + " is not signature format v1; upgrade gaiacpp to install it.");
    }
    if (rec.value("name", "") != name || rec.value("version", "") != version) {
        fail("Signature in " + sigPath.string() + " is for '" + rec.value("name", "") + "' " +
             rec.value("version", "") + ", but the bundle is '" + name + "' " + version +
             ". A signature does not transfer between skills or versions.");
    }
    if (!rec.contains("files") || !rec["files"].is_object()) {
        fail(sigPath.string() + " has no 'files' digest map, so it attests to nothing.");
    }
    std::map<std::string, std::string> signedDigests;
    for (auto& [k, v] : rec["files"].items()) signedDigests[k] = v.is_string() ? v.get<std::string>() : v.dump();

    const std::string publicKey = unb64(rec.value("public_key", json()), sigPath.string() + ": public_key");
    const std::string signature = unb64(rec.value("signature", json()), sigPath.string() + ": signature");
    const std::string actualId = sha256Hex(publicKey).substr(0, 32);
    if (rec.value("key_id", "") != actualId) {
        fail(sigPath.string() + " claims key id " + rec.value("key_id", "") +
             " but its public key hashes to " + actualId + ".");
    }
    if (!ed25519Verify(publicKey, signature, skillSigningPayload(name, version, signedDigests))) {
        fail("The signature in " + sigPath.string() +
             " does not verify. The bundle was modified after signing, or the signature is "
             "forged; refusing to install.");
    }
    const auto actual = skillFileDigests(directory);
    std::vector<std::string> mismatched;
    std::set<std::string> all;
    for (const auto& [k, _] : actual) all.insert(k);
    for (const auto& [k, _] : signedDigests) all.insert(k);
    for (const auto& f : all) {
        auto a = actual.find(f);
        auto s = signedDigests.find(f);
        if (a == actual.end() || s == signedDigests.end() || a->second != s->second) mismatched.push_back(f);
    }
    if (!mismatched.empty()) {
        std::string list;
        for (size_t i = 0; i < mismatched.size() && i < 8; ++i) list += (i ? ", " : "") + mismatched[i];
        fail("Bundle contents don't match the signed digest list in " + sigPath.string() + ": " +
             list + ". The bundle was modified after signing; refusing to install.");
    }
    SkillSignature out;
    out.signedBundle = true;
    out.keyId = actualId;
    out.publisher = rec.value("publisher", "");
    out.role = roleFor(actualId, trustRoot);
    return out;
#endif
}

} // namespace gaia
