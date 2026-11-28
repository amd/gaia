// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/tool_registry.h"

#include <algorithm>
#include <cctype>
#include <cstring>
#include <sstream>
#include <stdexcept>

namespace gaia {

void ToolRegistry::registerTool(ToolInfo info) {
    if (tools_.count(info.name)) {
        throw std::runtime_error("Tool already registered: " + info.name);
    }
    std::string name = info.name;
    tools_.emplace(std::move(name), std::move(info));
}

void ToolRegistry::registerTool(const std::string& name,
                                const std::string& description,
                                ToolCallback callback,
                                std::vector<ToolParameter> params,
                                bool atomic,
                                std::optional<ToolPolicy> policy) {
    ToolInfo info;
    info.name = name;
    info.description = description;
    info.callback = std::move(callback);
    info.parameters = std::move(params);
    info.atomic = atomic;
    info.policy = policy.value_or(defaultPolicy_);
    registerTool(std::move(info));
}

const ToolInfo* ToolRegistry::findTool(const std::string& name) const {
    auto it = tools_.find(name);
    if (it != tools_.end()) {
        return &it->second;
    }
    return nullptr;
}

std::string ToolRegistry::resolveName(const std::string& name) const {
    // Try case-insensitive suffix match (handles unprefixed MCP names)
    std::string lower = toLower(name);
    std::string suffix = "_" + lower;

    std::vector<std::string> matches;
    for (const auto& [registeredName, _] : tools_) {
        std::string regLower = toLower(registeredName);
        if (regLower.size() >= suffix.size() &&
            regLower.substr(regLower.size() - suffix.size()) == suffix) {
            matches.push_back(registeredName);
        }
    }
    if (matches.size() == 1) {
        return matches[0];
    }

    // Try exact case-insensitive match
    matches.clear();
    for (const auto& [registeredName, _] : tools_) {
        if (toLower(registeredName) == lower) {
            matches.push_back(registeredName);
        }
    }
    if (matches.size() == 1) {
        return matches[0];
    }

    return "";
}

bool ToolRegistry::hasTool(const std::string& name) const {
    return tools_.count(name) > 0;
}

bool ToolRegistry::removeTool(const std::string& name) {
    return tools_.erase(name) > 0;
}

const std::map<std::string, ToolInfo>& ToolRegistry::allTools() const {
    return tools_;
}

size_t ToolRegistry::size() const {
    return tools_.size();
}

void ToolRegistry::clear() {
    tools_.clear();
}

bool ToolRegistry::setEnabled(const std::string& name, bool enabled) {
    auto it = tools_.find(name);
    if (it == tools_.end()) return false;
    it->second.enabled = enabled;
    return true;
}

bool ToolRegistry::isEnabled(const std::string& name) const {
    const ToolInfo* tool = findTool(name);
    return tool && tool->enabled;
}

std::vector<std::string> ToolRegistry::enabledTools() const {
    std::vector<std::string> result;
    for (const auto& [name, tool] : tools_) {
        if (tool.enabled) result.push_back(name);
    }
    return result;
}

std::string ToolRegistry::formatForPrompt() const {
    std::ostringstream oss;
    for (const auto& [name, tool] : tools_) {
        if (!tool.enabled) continue;
        oss << "- " << name << "(";

        bool first = true;
        for (const auto& param : tool.parameters) {
            if (!first) oss << ", ";
            first = false;

            oss << param.name;
            if (!param.required) oss << "?";
            oss << ": " << paramTypeToString(param.type);
        }

        oss << "): " << tool.description << "\n";
    }
    return oss.str();
}

namespace {
/// OpenAI requires function names to match ^[a-zA-Z0-9_-]{1,64}$. MCP tools are
/// named mcp_<server>_<tool> from user-supplied server names, so a dot, space,
/// or a long pair can silently produce a name the server rejects — 400-ing the
/// whole request, not just that tool.
bool isValidOpenAiFunctionName(const std::string& name) {
    if (name.empty() || name.size() > 64) return false;
    for (char c : name) {
        const bool ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                        (c >= '0' && c <= '9') || c == '_' || c == '-';
        if (!ok) return false;
    }
    return true;
}
} // namespace

json ToolRegistry::buildOpenAiToolSchemas() const {
    json schemas = json::array();
    for (const auto& [name, tool] : tools_) {
        if (!tool.enabled) continue;

        if (!isValidOpenAiFunctionName(name)) {
            throw std::invalid_argument(
                "Tool '" + name + "' cannot be sent as an OpenAI function: names must "
                "match ^[a-zA-Z0-9_-]{1,64}$ (got " + std::to_string(name.size()) +
                " chars). Rename the tool — or, for an MCP tool, the MCP server whose "
                "name is prefixed onto it. To keep this tool on the prompt-JSON path "
                "instead, set AgentConfig::nativeToolCalls = NativeToolCalls::Never.");
        }

        json properties = json::object();
        json required = json::array();
        for (const auto& param : tool.parameters) {
            json prop;
            // UNKNOWN maps to "string" — the same coercion Python's
            // _python_to_json_type applies to an unrecognized annotation.
            prop["type"] = (param.type == ToolParamType::UNKNOWN)
                               ? "string"
                               : paramTypeToString(param.type);
            if (param.schema.is_object()) prop = param.schema;
            if (!param.description.empty()) {
                prop["description"] = param.description;
            }
            properties[param.name] = prop;
            if (param.required) {
                required.push_back(param.name);
            }
        }

        schemas.push_back(json{
            {"type", "function"},
            {"function",
             {{"name", name},
              {"description", tool.description},
              {"parameters",
               {{"type", "object"},
                {"properties", properties},
                {"required", required}}}}}});
    }
    return schemas;
}

json ToolRegistry::executeTool(const std::string& name, const json& args) {
    const ToolInfo* tool = findTool(name);
    if (!tool) {
        std::string resolved = resolveName(name);
        if (!resolved.empty()) {
            tool = findTool(resolved);
        }
    }

    if (!tool) {
        return json{{"status", "error"}, {"error", "Tool '" + name + "' not found"}};
    }

    if (!tool->enabled) {
        return json{{"status", "error"}, {"error", "Tool '" + tool->name + "' is disabled"}};
    }

    if (!tool->callback) {
        return json{{"status", "error"}, {"error", "Tool '" + name + "' has no callback"}};
    }

    const std::string& resolvedName = tool->name;

    // 1. DENY check
    if (tool->policy == ToolPolicy::DENY) {
        return json{{"status", "error"}, {"error", "Tool '" + resolvedName + "' is denied by policy"}};
    }

    // 2. Argument validation (runs before confirmation so user sees clean args)
    json effectiveArgs = args;
    if (tool->validateArgs.has_value()) {
        try {
            effectiveArgs = (*tool->validateArgs)(resolvedName, args);
        } catch (const std::invalid_argument& e) {
            return json{{"status", "error"}, {"error", std::string("Argument validation failed: ") + e.what()}};
        }
    } else if (!tool->parameters.empty()) {
        // Small models send "5" for 5 and "true" for true; accept the obvious ones.
        effectiveArgs = coerceArgsToSchema(tool->parameters, effectiveArgs);
        std::string validationError = validateArgsAgainstSchema(tool->parameters, effectiveArgs);
        if (!validationError.empty()) {
            return json{{"status", "error"}, {"error", "Invalid arguments for '" + resolvedName + "': " + validationError}};
        }
    }

    if (argRewrite_) argRewrite_(*tool, effectiveArgs);
    if (preCheck_) {
        if (auto refused = preCheck_(*tool, effectiveArgs)) return *refused;
    }

    // 3. CONFIRM check
    if (tool->policy == ToolPolicy::CONFIRM && !(tool->needsConfirm && !tool->needsConfirm(effectiveArgs))) {
        // Fast path: already permanently allowed
        const std::string grantKey =
            tool->grantScope ? resolvedName + ":" + tool->grantScope(effectiveArgs) : resolvedName;
        if (allowedStore_ && allowedStore_->isAlwaysAllowed(grantKey)) {
            // proceed
        } else {
            // Fail-closed: no callback = deny
            if (!confirmCallback_) {
                return json{{"status", "error"}, {"error", "Tool '" + resolvedName + "' requires confirmation but no callback is set"}};
            }

            if (auto refused = declinedRefusal(resolvedName, effectiveArgs)) return *refused;

            ToolConfirmResult decision = confirmCallback_(resolvedName, effectiveArgs);
            if (decision == ToolConfirmResult::ALWAYS_ALLOW || decision == ToolConfirmResult::ALLOW_ONCE) {
                noteApproved(resolvedName, effectiveArgs);
            }
            if (decision == ToolConfirmResult::ALWAYS_ALLOW) {
                if (allowedStore_) {
                    allowedStore_->addAlwaysAllowed(grantKey);
                }
            } else if (decision == ToolConfirmResult::DENY) {
                noteDeclined(resolvedName, effectiveArgs);
                return json{{"status", "error"},
                            {"error", "Tool '" + resolvedName + "' execution denied by user"},
                            {"hint", declinedHint()}};
            } else if (decision == ToolConfirmResult::UNATTENDED) {
                // Nobody said no, so other routes stay open and nothing is remembered.
                return json{{"status", "error"},
                            {"not_executed", true},
                            {"error", "Tool '" + resolvedName +
                                          "' was not run: it needs approval and nobody could give it." +
                                          (unattendedRemedy_.empty() ? "" : " " + unattendedRemedy_)}};
            }
            // ALLOW_ONCE falls through
        }
    }

    // 4. Execute
    try {
        return tool->callback(effectiveArgs);
    } catch (const std::exception& e) {
        return json{{"status", "error"}, {"error", std::string("Tool execution failed: ") + e.what()}};
    }
}

// ---- Calls the user declined ----

namespace {

std::string collapseSpaces(const std::string& s) {
    std::string out;
    bool space = false;
    for (unsigned char c : s) {
        if (std::isspace(c)) {
            space = !out.empty();
            continue;
        }
        if (space) out.push_back(' ');
        space = false;
        out.push_back(static_cast<char>(c));
    }
    return out;
}

// One call's identity: its tool and exact arguments.
std::string callKey(const std::string& tool, const json& args) {
    return json::array({tool, args}).dump(-1, ' ', false, json::error_handler_t::replace);
}

// The arguments that say what a call acts on; a file's new text is not one.
const char* const kTargetKeys[] = {"command", "path", "file_path", "filename", "destination", "code"};

std::string shownCall(const std::string& tool, const json& args) {
    if (args.is_object()) {
        for (const char* key : kTargetKeys) {
            if (args.contains(key) && args[key].is_string()) return collapseSpaces(args[key].get<std::string>());
        }
    }
    return tool;
}

std::vector<std::string> targetStrings(const json& args) {
    std::vector<std::string> out;
    if (!args.is_object()) return out;
    for (const char* key : kTargetKeys) {
        if (args.contains(key) && args[key].is_string()) out.push_back(args[key].get<std::string>());
    }
    return out;
}

std::string baseName(const std::string& path) {
    const size_t cut = path.find_last_of("/\\");
    return cut == std::string::npos ? path : path.substr(cut + 1);
}

bool wildcardMatch(const std::string& pattern, const std::string& text) {
    size_t p = 0, t = 0, star = std::string::npos, mark = 0;
    while (t < text.size()) {
        if (p < pattern.size() && (pattern[p] == '?' || pattern[p] == text[t])) {
            ++p;
            ++t;
        } else if (p < pattern.size() && pattern[p] == '*') {
            star = p++;
            mark = t;
        } else if (star != std::string::npos) {
            p = star + 1;
            t = ++mark;
        } else {
            return false;
        }
    }
    while (p < pattern.size() && pattern[p] == '*') ++p;
    return p == pattern.size();
}

// Split on spaces and on the punctuation around a file name in a command or a line of script.
std::vector<std::string> wordsOf(const std::string& s) {
    std::vector<std::string> words;
    std::string cur;
    for (char c : s) {
        if (std::isspace(static_cast<unsigned char>(c)) || std::strchr("'\"`();|&<>,=", c)) {
            if (!cur.empty()) words.push_back(std::move(cur));
            cur.clear();
        } else {
            cur.push_back(c);
        }
    }
    if (!cur.empty()) words.push_back(std::move(cur));
    return words;
}

// A shell line's commands, split on the unquoted && || ; | and newlines between them.
std::vector<std::string> shellSegments(const std::string& line) {
    std::vector<std::string> out;
    std::string cur;
    char quote = 0;
    for (size_t i = 0; i < line.size(); ++i) {
        const char c = line[i];
        if (quote) {
            if (c == quote) quote = 0;
            cur.push_back(c);
        } else if (c == '\'' || c == '"') {
            quote = c;
            cur.push_back(c);
        } else if (c == ';' || c == '\n' || c == '|' || (c == '&' && i + 1 < line.size() && line[i + 1] == '&')) {
            if ((c == '|' || c == '&') && i + 1 < line.size() && line[i + 1] == c) ++i;
            out.push_back(std::move(cur));
            cur.clear();
        } else {
            cur.push_back(c);
        }
    }
    out.push_back(std::move(cur));
    return out;
}

// One command's words, quotes removed.
std::vector<std::string> shellWords(const std::string& segment) {
    std::vector<std::string> words;
    std::string cur;
    bool inWord = false;
    char quote = 0;
    for (char c : segment) {
        if (quote) {
            if (c == quote) {
                quote = 0;
            } else {
                cur.push_back(c);
            }
        } else if (c == '\'' || c == '"') {
            quote = c;
            inWord = true;
        } else if (std::isspace(static_cast<unsigned char>(c))) {
            if (inWord) words.push_back(std::move(cur));
            cur.clear();
            inWord = false;
        } else {
            cur.push_back(c);
            inWord = true;
        }
    }
    if (inWord) words.push_back(std::move(cur));
    return words;
}

bool isRedirection(const std::string& w) {
    const size_t gt = w.find('>');
    if (gt == std::string::npos) return false;
    const size_t other = w.find_first_not_of("0123456789&");
    return other == std::string::npos || other >= gt;
}

// What one command runs: its program by name and its arguments, without the
// folder it runs in (a leading cd, git -C) or where its output goes.
std::string commandShape(const std::string& segment) {
    const auto words = shellWords(segment);
    size_t i = 0;
    while (i < words.size() && words[i].find('=') != std::string::npos && words[i][0] != '-') ++i;
    if (i >= words.size()) return "";
    const std::string program = baseName(words[i]);
    if (program == "cd" || program == "pushd") return "";
    std::string shape = program;
    for (++i; i < words.size(); ++i) {
        const std::string& w = words[i];
        if (isRedirection(w)) {
            if (w.back() == '>') ++i;  // "2> file": the file is where output goes
            continue;
        }
        if (program == "git") {
            if (w == "-C" || w == "-c") {
                ++i;
                continue;
            }
            if (w == "--no-pager" || w.rfind("--git-dir=", 0) == 0 || w.rfind("--work-tree=", 0) == 0) continue;
        }
        shape += " " + w;
    }
    return shape;
}

std::vector<std::string> commandShapes(const json& args) {
    std::vector<std::string> shapes;
    if (!args.is_object() || !args.contains("command") || !args["command"].is_string()) return shapes;
    for (const auto& segment : shellSegments(args["command"].get<std::string>())) {
        std::string shape = commandShape(segment);
        if (!shape.empty()) shapes.push_back(std::move(shape));
    }
    return shapes;
}

// The file names a declined call was aimed at. A command's first word is its
// program and a bare word may be a subcommand, so neither is a target; nor is
// the folder a command runs in.
std::vector<std::string> targetsOf(const json& args) {
    std::vector<std::string> sources = commandShapes(args);
    for (const char* key : kTargetKeys) {
        if (std::string(key) == "command") continue;
        if (args.is_object() && args.contains(key) && args[key].is_string()) sources.push_back(args[key].get<std::string>());
    }
    std::vector<std::string> targets;
    for (const auto& s : sources) {
        const auto words = wordsOf(s);
        for (size_t i = 0; i < words.size(); ++i) {
            const std::string name = baseName(words[i]);
            if (words.size() > 1 && i == 0) continue;
            if (words[i][0] == '-' || name.size() < 3) continue;
            if (name.find_first_of("*?") != std::string::npos) continue;
            if (name.find('.') == std::string::npos && name == words[i]) continue;
            targets.push_back(name);
        }
    }
    return targets;
}

// Keys under which an approval is remembered: the exact call, and for a
// command what it runs wherever it runs.
std::vector<std::string> approvalKeys(const std::string& tool, const json& args) {
    std::vector<std::string> keys{callKey(tool, args)};
    const auto shapes = commandShapes(args);
    if (!shapes.empty()) {
        std::string joined = tool;
        for (const auto& shape : shapes) joined += "\x1f" + shape;
        keys.push_back(std::move(joined));
    }
    return keys;
}

bool namesTarget(const json& args, const std::vector<std::string>& targets) {
    if (targets.empty()) return false;
    for (const auto& s : targetStrings(args)) {
        for (const auto& target : targets) {
            if (s.find(target) != std::string::npos) return true;
        }
        for (const auto& word : wordsOf(s)) {
            const std::string name = baseName(word);
            if (name.find_first_of("*?") == std::string::npos) continue;
            // A lone "*" names everything in a folder, so it names no one file.
            if (name.find_first_not_of("*?.") == std::string::npos) continue;
            for (const auto& target : targets) {
                if (wildcardMatch(name, target)) return true;
            }
        }
    }
    return false;
}

std::string lowered(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return s;
}

// Whether *phrase* stands as whole words in *text*: "again" is not in "against".
bool hasPhrase(const std::string& text, const std::string& phrase) {
    const auto wordChar = [](char c) { return std::isalnum(static_cast<unsigned char>(c)) != 0; };
    for (size_t at = text.find(phrase); at != std::string::npos; at = text.find(phrase, at + 1)) {
        const size_t end = at + phrase.size();
        if ((at == 0 || !wordChar(text[at - 1])) && (end >= text.size() || !wordChar(text[end]))) return true;
    }
    return false;
}

json notRun(const std::string& shown, const std::string& error) {
    return json{{"status", "error"},
                {"not_executed", true},
                {"already_declined", true},
                {"declined_call", shown},
                {"hint", ToolRegistry::declinedHint()},
                {"error", error}};
}

} // namespace

const char* ToolRegistry::declinedHint() {
    return "The user declined this one call, not the tool and not the task. Do not "
           "repeat it or reword it: the same command, script, or change to the same "
           "file through another tool is the same call. Carry on with the task by a "
           "different, legitimate means (another tool, a loaded skill's tool, reading "
           "the files directly), and say in your answer what you could not do. If "
           "nothing else can do the job, tell the user what you need and why.";
}

void ToolRegistry::beginTurn(const std::string& userMessage) {
    declinedTargets_.clear();
    declinedCommands_.clear();
    lastDeclined_.clear();
    if (declinedCalls_.empty()) return;
    const std::string text = lowered(collapseSpaces(userMessage));
    // "Don't run it again" is not a request to run it.
    for (const char* no : {"don't", "do not", "dont", "never"}) {
        if (hasPhrase(text, no)) return;
    }
    bool retry = false;
    for (const char* again : {"try again", "retry", "run it", "do it", "go ahead", "again"}) {
        if (hasPhrase(text, again)) retry = true;
    }
    // A wrong guess only puts the call to the user again; it never runs anything.
    for (auto it = declinedCalls_.begin(); it != declinedCalls_.end();) {
        if (retry || (!it->second.empty() && text.find(lowered(it->second)) != std::string::npos)) {
            it = declinedCalls_.erase(it);
        } else {
            ++it;
        }
    }
}

void ToolRegistry::forgetDeclinedCalls() {
    declinedCalls_.clear();
    declinedTargets_.clear();
    declinedCommands_.clear();
    lastDeclined_.clear();
    approvedCalls_.clear();
}

void ToolRegistry::noteDeclined(const std::string& tool, const json& args) {
    lastDeclined_ = shownCall(tool, args);
    declinedCalls_[callKey(tool, args)] = lastDeclined_;
    for (auto& t : targetsOf(args)) declinedTargets_.push_back(std::move(t));
    for (auto& c : commandShapes(args)) declinedCommands_.push_back(std::move(c));
    for (const auto& key : approvalKeys(tool, args)) approvedCalls_.erase(key);
}

void ToolRegistry::noteApproved(const std::string& tool, const json& args) {
    for (auto& key : approvalKeys(tool, args)) approvedCalls_.insert(std::move(key));
}

std::optional<json> ToolRegistry::declinedRefusal(const std::string& tool, const json& args) const {
    const auto exact = declinedCalls_.find(callKey(tool, args));
    if (exact != declinedCalls_.end()) {
        return notRun(exact->second,
                      "Not run, and not put to the user again: they declined this exact call (`" + exact->second +
                          "`) earlier in this chat. Do not repeat it or reword it. Tell the user you did not "
                          "run it because they declined it, and that they can ask you to run it again.");
    }
    if (declinedTargets_.empty() && declinedCommands_.empty()) return std::nullopt;
    // A yes given earlier in this chat is not taken back by a no to something else.
    for (const auto& key : approvalKeys(tool, args)) {
        if (approvedCalls_.count(key)) return std::nullopt;
    }
    bool sameCommand = false;
    for (const auto& shape : commandShapes(args)) {
        if (std::find(declinedCommands_.begin(), declinedCommands_.end(), shape) != declinedCommands_.end()) {
            sameCommand = true;
        }
    }
    if (!sameCommand && !namesTarget(args, declinedTargets_)) return std::nullopt;
    std::string offLimits;
    for (const auto* list : {&declinedCommands_, &declinedTargets_}) {
        for (const auto& item : *list) offLimits += (offLimits.empty() ? "`" : ", `") + item + "`";
    }
    return notRun(lastDeclined_,
                  "Not run, and not put to the user again: this reaches what the user declined earlier in "
                  "this request (`" + lastDeclined_ + "`). That no is to this one action, whichever tool "
                  "carries it, so do not reach it with another tool, command, or script. Off-limits until "
                  "the user's next message: " + offLimits + ". Every other step is still open and asks as "
                  "usual. Tell the user what is blocked and what you can do without it.");
}

void ToolRegistry::setConfirmCallback(ToolConfirmCallback cb) {
    confirmCallback_ = std::move(cb);
}

void ToolRegistry::setDefaultPolicy(ToolPolicy policy) {
    defaultPolicy_ = policy;
}

ToolPolicy ToolRegistry::defaultPolicy() const {
    return defaultPolicy_;
}

void ToolRegistry::setAllowedToolsStore(std::shared_ptr<AllowedToolsStore> store) {
    allowedStore_ = std::move(store);
}

std::string ToolRegistry::toLower(const std::string& s) {
    std::string result = s;
    std::transform(result.begin(), result.end(), result.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return result;
}

json ToolRegistry::coerceArgsToSchema(const std::vector<ToolParameter>& params,
                                      const json& args) {
    if (!args.is_object()) return args;
    json out = args;
    for (const auto& param : params) {
        auto it = out.find(param.name);
        if (it == out.end() || !it->is_string()) continue;
        const std::string s = it->get<std::string>();
        if (s.empty()) continue;
        try {
            size_t used = 0;
            if (param.type == ToolParamType::INTEGER) {
                long long v = std::stoll(s, &used);
                if (used == s.size()) *it = v;
            } else if (param.type == ToolParamType::NUMBER) {
                double v = std::stod(s, &used);
                if (used == s.size()) *it = v;
            } else if (param.type == ToolParamType::BOOLEAN) {
                const std::string l = toLower(s);
                if (l == "true") *it = true;
                else if (l == "false") *it = false;
            } else if (param.type == ToolParamType::ARRAY || param.type == ToolParamType::OBJECT) {
                // Small models often send a list or object as its JSON text.
                json parsed = json::parse(s, nullptr, false);
                if (param.type == ToolParamType::ARRAY ? parsed.is_array() : parsed.is_object()) {
                    *it = std::move(parsed);
                }
            }
        } catch (const std::exception&) {
            // Not a number: leave it for the type check to reject with its own message.
        }
    }
    return out;
}

std::string ToolRegistry::validateArgsAgainstSchema(
    const std::vector<ToolParameter>& params, const json& args) {
    // args should be an object (or null/missing treated as empty object)
    json effectiveArgs = (args.is_null() || args.is_discarded()) ? json::object() : args;
    if (!effectiveArgs.is_object()) {
        return "expected object, got " + std::string(effectiveArgs.type_name());
    }

    if (effectiveArgs.empty()) {
        std::string required;
        for (const auto& param : params) {
            if (param.required) required += (required.empty() ? "'" : ", '") + param.name + "'";
        }
        if (!required.empty()) {
            return "missing required parameter: the call arrived with no arguments; it needs " + required +
                   ". Send them in the call's arguments object. If an argument is very long, "
                   "write it to a file in parts instead";
        }
    }

    // Check required parameters are present
    for (const auto& param : params) {
        if (param.required && !effectiveArgs.contains(param.name)) {
            return "missing required parameter '" + param.name + "'";
        }

        // Type-check if the parameter is present
        if (effectiveArgs.contains(param.name)) {
            const auto& val = effectiveArgs[param.name];
            bool typeOk = false;
            switch (param.type) {
                case ToolParamType::STRING:
                    typeOk = val.is_string();
                    break;
                case ToolParamType::INTEGER:
                    typeOk = val.is_number_integer();
                    break;
                case ToolParamType::NUMBER:
                    typeOk = val.is_number();
                    break;
                case ToolParamType::BOOLEAN:
                    typeOk = val.is_boolean();
                    break;
                case ToolParamType::ARRAY:
                    typeOk = val.is_array();
                    break;
                case ToolParamType::OBJECT:
                    typeOk = val.is_object();
                    break;
                case ToolParamType::UNKNOWN:
                    typeOk = true;  // accept anything
                    break;
            }
            if (!typeOk) {
                return "parameter '" + param.name + "' should be " +
                       paramTypeToString(param.type) + ", got " +
                       std::string(val.type_name());
            }
        }
    }

    return "";  // valid
}

} // namespace gaia
