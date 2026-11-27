// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// gaiacode's GitHub support: issues, pull requests and CI over the GitHub REST
// API, with no dependency on the gh CLI. Tokens are bound to the host they
// belong to, and an API override is honored only on loopback (tests, evals).

#pragma once

#include <functional>
#include <memory>
#include <optional>
#include <set>
#include <string>

#include <gaia/types.h>

namespace gaia {
class ToolRegistry;
}

namespace gaia::code {

/// A GitHub repository: the host it lives on and owner/name.
struct GitHubRepo {
    std::string host;  // "github.com" or a GitHub Enterprise host
    std::string owner;
    std::string name;
    std::string slug() const { return owner + "/" + name; }
};

/// The repository a git remote URL names (https, ssh or scp-style), with any user
/// or token in it dropped; nullopt for a URL that names none.
std::optional<GitHubRepo> parseRemote(const std::string& url);

/// "owner/name" as a repository on github.com; nullopt when malformed.
std::optional<GitHubRepo> parseRepoSlug(const std::string& slug);

/// Whether a URL points at this machine (127.0.0.1, ::1, localhost).
bool isLoopbackUrl(const std::string& url);

/// Where GitHub's API is, and the token for it, found the way the CLI finds them.
struct GitHubAccess {
    std::string apiBase;      // "https://api.github.com", "https://<host>/api/v3", or a loopback override
    std::string token;        // empty = unauthenticated (public reads only)
    std::string tokenSource;  // "GH_TOKEN", "gh auth token", ...: named in errors
};

/// How to read the environment and run `gh auth token`, injectable for tests.
struct GitHubEnvironment {
    std::function<std::string(const std::string&)> getEnv;
    /// The token `gh auth token --hostname host` prints, or "" when gh isn't there or signed in.
    std::function<std::string(const std::string& host)> ghToken;
    static GitHubEnvironment real();
};

/// The API base and token for `repo`. A token is only ever sent to its own host:
/// GH_TOKEN / GITHUB_TOKEN to github.com, GH_ENTERPRISE_TOKEN to the host GH_HOST
/// names, and gh's stored token to the host it was stored for. GITHUB_API_URL is
/// honored only on loopback. Throws std::runtime_error naming the fix otherwise.
GitHubAccess resolveGitHubAccess(const GitHubRepo& repo, const GitHubEnvironment& env);

/// Which writes a headless run may make: "comment", "pr", "review", "issue", "rerun". Anything else
/// is refused.
using GitHubWriteKinds = std::set<std::string>;

/// The repository the shell's folder works on: the `upstream` remote when there is
/// one (a fork's issues live there), else `origin`. Throws when neither names one.
GitHubRepo repoFromGit(const std::string& folder);

/// Register the gh_* tools. `repoOverride` ("owner/name") wins over the git remotes;
/// `writes` lists the write kinds allowed; `folder` returns the shell's current folder.
void registerGitHubTools(ToolRegistry& registry, std::function<std::string()> folder,
                         std::string repoOverride, GitHubWriteKinds writes,
                         GitHubEnvironment env = GitHubEnvironment::real());

}  // namespace gaia::code
