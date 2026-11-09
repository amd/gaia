// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Read secrets that the Python runtime stored with the `keyring` library, so a
// `{"$keyring": "gaia.connections:<connector>:<KEY>"}` reference written by
// `gaia connectors` works from C++ too (#2819). Read-only: C++ never writes or
// deletes keyring entries.
//
//   Windows  Credential Manager generic credential: target <service> with that
//            user name, else target "<username>@<service>" (keyring's two forms)
//   macOS    Keychain generic password (service, account)
//   Linux    Secret Service item {service, username} via `secret-tool`

#pragma once

#include <functional>
#include <optional>
#include <string>

#include "gaia/export.h"

namespace gaia {

/// The secret for (service, username), or nullopt when there is none. Throws
/// std::runtime_error, naming what to install or start, when the OS keyring
/// can't be reached at all.
GAIA_API std::optional<std::string> readKeyringSecret(const std::string& service,
                                                      const std::string& username);

/// Resolve a "<service>:<username>" reference as written by `gaia connectors`.
/// Throws std::runtime_error when it is malformed or holds no secret.
GAIA_API std::string resolveKeyringReference(const std::string& reference);

/// Replace the OS backend (tests). Pass an empty function to restore it.
using KeyringBackend =
    std::function<std::optional<std::string>(const std::string& service, const std::string& username)>;
GAIA_API void setKeyringBackendForTesting(KeyringBackend backend);

} // namespace gaia
