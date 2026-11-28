// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// The accounts a user connected in GAIA, read the way gaia.connectors reads
// them: the encrypted vault (~/.gaia/connectors/vault.enc, its key in the OS
// keychain), the account list (accounts.json) and the per-agent grant ledger
// (grants.json). Under the daemon the keychain is reached through its
// /host/v1/keychain route, as the Python flagship does (#6481).
//
// Read-only: the Python connectors stay the one writer of the vault, the account
// list and the ledger. A refresh here mints an access token and keeps it in
// memory; a rotated refresh token is not saved, and a revoked sign-in is
// reported, not cleared. Both providers keep the stored refresh token valid.

#pragma once

#include <functional>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

#include "gaia/export.h"
#include "gaia/mail.h"

namespace gaia::connectors {

/// A gaia.connectors error. `kind` is the Python class name, which the email
/// tools show in an app ("GAIA could not use the Google connection (<kind>)").
class GAIA_API ConnectorsError : public std::runtime_error {
public:
    ConnectorsError(const std::string& message, std::string kind = "ConnectorsError")
        : std::runtime_error(message), kind_(std::move(kind)) {}
    const std::string& kind() const noexcept { return kind_; }

private:
    std::string kind_;
};

/// One connected account, as api.get_connection returns it. `error` is
/// "busy" or "configuration" when its state can't be used right now.
struct Connection {
    std::string provider;
    std::string accountId;
    std::string accountEmail;  // "" when none was recorded
    std::vector<std::string> scopes;
    std::string error;
};

/// Use the daemon's keychain when it handed this process a credential file
/// (GAIA_HOST_KEYCHAIN_SECRET_FILE). Reads and deletes the file and clears the
/// variable either way. Returns whether the daemon's keychain is now in use.
/// Throws ConnectorsError when the file can't be read or the daemon's URL is
/// missing, the same as the Python flagship at startup.
GAIA_API bool installDaemonKeychainFromEnv();

/// The default account of `provider` when it is connected, else nullopt.
GAIA_API std::optional<Connection> getConnection(const std::string& provider);

/// The scopes `agentId` was granted for `connector`, from the grant ledger.
GAIA_API std::vector<std::string> agentGrants(const std::string& connector, const std::string& agentId);

/// A short-lived access token for `provider`'s default account, after the same
/// gates as api.get_access_token: a non-empty scope list, a grant covering it,
/// a connection carrying it. Cached until 60 s before it expires.
GAIA_API std::string getAccessToken(const std::string& provider, const std::vector<std::string>& scopes,
                                    const std::string& agentId);

// ---- tests ----

/// Replace the keychain (OS or daemon) with `read`. Empty restores it.
using SecretReader = std::function<std::optional<std::string>(const std::string& service, const std::string& username)>;
GAIA_API void setSecretReaderForTesting(SecretReader read);
/// Replace the transport the token refresh POSTs through. Empty restores it.
GAIA_API void setTokenTransportForTesting(mail::Transport transport);
/// Forget cached access tokens and the cached vault key.
GAIA_API void resetForTesting();
/// AES-256-GCM vault envelope for `plaintext` (tests build vaults with it).
GAIA_API std::string encryptVaultForTesting(const std::string& key, const std::string& plaintext);
/// zlib.crc32 as 8 hex digits, as client_id_hash and chunk manifests use it.
GAIA_API std::string crc32Hex(const std::string& data);

}  // namespace gaia::connectors
