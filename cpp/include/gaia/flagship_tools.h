// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// The flagship's tool surface under the Python flagship's names and argument
// names, so a SKILL.md written for one runtime drives the other:
//   files    read_file, write_file, edit_file, search_file, find_files,
//            search_file_content, browse_directory, get_file_info
//   compute  run_shell_command, run_python
//   web      fetch_webpage, fetch_page, search_web
//   data     analyze_data_file, create_table, insert_data, query_data, list_tables, drop_table
//   more     tree, file_info, list_recent_files, search_directory, download_file,
//            dump_document, request_user_input
//   misc     sleep
//
// File tools are confined to allowedPaths and refuse credential files; an
// existing file is only overwritten or edited after read_file saw its current
// contents. The shell and Python run under CONFIRM policy every call.

#pragma once

#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <vector>

#include "gaia/database.h"
#include "gaia/export.h"
#include "gaia/types.h"

namespace gaia {

class ShellSession;
class ToolRegistry;

struct GAIA_API FlagshipToolContext {
    /// Folders the file tools may touch. Empty means the user's home.
    std::vector<std::string> allowedPaths;
    /// Where relative paths resolve and where the shell and Python start.
    std::string workDir;
    /// Scratch folder for run_python scripts and file backups.
    std::string scratchDir;
    /// SQLite file behind the scratchpad tables, or ":memory:".
    std::string scratchpadDb = ":memory:";
    /// Polled during sleep and long waits; true stops them early.
    std::function<bool()> cancelled;
    /// Asks the person a question and returns their answer, or nullopt when
    /// nobody can answer (request_user_input then reports "__NO_RESPONSE__").
    std::function<std::optional<std::string>(const std::string& question, const std::vector<std::string>& choices)> askUser;
    /// The session model's vision: (prompt, raw image bytes) → reply. Unset, the
    /// image tools are not registered and scanned pages stay unread.
    std::function<std::string(const std::string& prompt, const std::vector<std::string>& images)> vision;
};

/// One HTTP GET, redirects not followed: what web tools build on.
struct GAIA_API HttpFetch {
    int status = 0;
    std::string body;
    std::string contentType;
    std::string location;  ///< Location header of a redirect
};

/// Why @p path must never be read (a credential file or folder), or "" when it may be.
GAIA_API std::string credentialRefusal(const std::string& path);

/// Why GAIA must never write @p path (~ expanded, relative to the current folder),
/// or "" when it may: credential files, files and folders that run code on their
/// own (shell startup, git, autostart, Python .pth, PATH folders, a .bat that
/// would shadow a command), and GAIA's own settings folder.
GAIA_API std::string writeTargetRefusal(const std::string& path);

/// Whether an edit to @p target may be accepted without asking: it resolves
/// inside one of @p roots (never a home folder or drive root), passes
/// writeTargetRefusal, and has no hidden file or folder below the root.
GAIA_API bool editIsInside(const std::string& target, const std::vector<std::string>& roots);

class GAIA_API FlagshipTools {
public:
    explicit FlagshipTools(FlagshipToolContext ctx);
    ~FlagshipTools();

    /// Register every tool above. @p includeWeb=false leaves out the
    /// network tools (fetch_webpage, fetch_page, search_web).
    void registerAll(ToolRegistry& registry, bool includeWeb = true);

    // Tool bodies — public so tests can drive them without a registry.
    json readFile(const json& args);
    json writeFile(const json& args);
    json editFile(const json& args);
    json searchFile(const json& args);
    json findFiles(const json& args);
    json searchFileContent(const json& args);
    json browseDirectory(const json& args);
    json getFileInfo(const json& args);
    json analyzeDataFile(const json& args);
    json runShellCommand(const json& args);
    json runPython(const json& args);
    json fetchWebpage(const json& args);
    json fetchPage(const json& args);
    json searchWeb(const json& args);
    json createTable(const json& args);
    json insertData(const json& args);
    json queryData(const json& args);
    json listTables(const json& args);
    json dropTable(const json& args);
    json sleep(const json& args);
    json tree(const json& args);
    json fileInfo(const json& args);
    json listRecentFiles(const json& args);
    json searchDirectory(const json& args);
    json downloadFile(const json& args);
    json dumpDocument(const json& args);
    json requestUserInput(const json& args);
    json analyzeImage(const json& args);
    json answerQuestionAboutImage(const json& args);

    /// Resolve @p raw (~ expanded, relative to workDir) to an absolute path,
    /// or "" when it falls outside allowedPaths.
    std::string resolveAllowed(const std::string& raw) const;

    /// Replace the HTTP transport (tests point it at a local server).
    void setHttpGet(std::function<HttpFetch(const std::string& url)> get) { httpGet_ = std::move(get); }
    /// Let fetch_webpage reach loopback/private addresses (tests only).
    void allowPrivateAddresses(bool allow) { allowPrivate_ = allow; }

private:
    json fetchFollowing(const std::string& url, HttpFetch& out);
    void backup(const std::string& path);

    FlagshipToolContext ctx_;
    std::unique_ptr<ShellSession> shell_;
    std::mutex shellMutex_;
    std::mutex dbMutex_;
    Database scratch_;
    std::function<HttpFetch(const std::string&)> httpGet_;
    bool allowPrivate_ = false;
};

/// The platform transport: WinHTTP on Windows, the system curl elsewhere.
/// Unless @p allowPrivate, a host that resolves to a local or private address
/// is refused, and the address actually used must be the vetted one (curl is
/// pinned to it; WinHTTP's peer is checked before the body is read).
/// @throws std::runtime_error naming what failed.
GAIA_API HttpFetch platformHttpGet(const std::string& url, bool allowPrivate = false);

/// Parse the header blocks curl's `-D -` writes ahead of a body, skipping 1xx
/// and proxy-tunnel blocks, into @p out's status, content type and location.
/// @return where the body starts in @p raw.
GAIA_API std::size_t parseHttpHeaderBlocks(const std::string& raw, HttpFetch& out);

/// Visible text of an HTML page (scripts, styles and tags removed, entities decoded).
GAIA_API std::string htmlToText(const std::string& html);

/// True when @p host is, or resolves to, a loopback, private, link-local,
/// shared, multicast, reserved or unspecified address, or does not resolve.
GAIA_API bool isPrivateHost(const std::string& host);

}  // namespace gaia
