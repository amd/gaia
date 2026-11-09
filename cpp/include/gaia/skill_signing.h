// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Verifies a skill bundle's SIGNATURE.json exactly as the Python runtime does
// (src/gaia/skills/signing.py): Ed25519 over canonical JSON of
// {algorithm, files: {path: sha256}, name, version, signature_version}, the key
// id being the first 32 hex chars of SHA-256(public key). A verified signature
// earns a tier through the trust store: role "amd" -> verified, "publisher" ->
// community, anything else -> experimental.

#pragma once

#include <map>
#include <string>

#include "gaia/export.h"
#include "gaia/skill.h"

namespace gaia {

struct GAIA_API SkillSignature {
    bool signedBundle = false;  // false: no SIGNATURE.json (unsigned, allowed)
    std::string keyId;
    std::string publisher;
    std::string role;  // "" when the key isn't trusted
    /// verified | community | experimental
    std::string tier() const;
};

/// Lower-case hex SHA-256 of `data`. Throws SkillError in a build without OpenSSL.
GAIA_API std::string skillSha256Hex(const std::string& data);

/// SHA-256 hex of every file under `directory` except SIGNATURE.json, keyed by
/// POSIX relative path — Python's file_digests().
GAIA_API std::map<std::string, std::string> skillFileDigests(const std::string& directory);

/// The exact bytes a signature covers — Python's signing_payload().
GAIA_API std::string skillSigningPayload(const std::string& name, const std::string& version,
                                         const std::map<std::string, std::string>& digests);

/// Verify `directory`'s signature for skill `name` at `version`, resolving the
/// signer through `<trustRoot>/trusted-keys.json` plus the built-in AMD key.
/// Returns signedBundle=false when there is no SIGNATURE.json. Throws
/// SkillError for a signature that is present and wrong (tampering), a
/// malformed trust store, or a build without Ed25519 support.
GAIA_API SkillSignature verifySkillBundle(const std::string& directory, const std::string& name,
                                          const std::string& version, const std::string& trustRoot);

} // namespace gaia
