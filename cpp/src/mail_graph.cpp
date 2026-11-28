// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Read half of src/gaia/agents/tools/_email/graph.py: lists, searches and
// reads one Outlook mailbox through Microsoft Graph.

#include "mail_internal.h"

#include <algorithm>
#include <map>
#include <stdexcept>

#include "lower_ascii.h"

namespace gaia::mail {

using namespace detail;
using gaia::detail::lowerAscii;

namespace {

const std::string kGraphApiBase = "https://graph.microsoft.com/v1.0";

// Every field the read tools and the triage signals need; `body` only on reads.
const std::string kListSelect =
    "id,conversationId,subject,from,toRecipients,ccRecipients,"
    "receivedDateTime,isRead,isDraft,flag,categories,bodyPreview,hasAttachments";
// The inner $select is what keeps `contentBytes` (the file itself) off the wire.
const std::string kAttachmentExpand = "attachments($select=name,contentType,size,isInline)";
const std::string kFullSelect = kListSelect + ",internetMessageHeaders,body";

// Graph caps $top at 999; asking for more is a 400, not a truncation.
constexpr int kMaxTop = 999;
// One page of a conversation. Longer threads are reported truncated, not cut.
constexpr int kThreadTop = 100;

// Standard base64, so `+`, `/` and `=` are legitimate, as are `-` and `_`.
bool isGraphId(const std::string& s) {
    if (s.empty() || s.size() > 512) return false;
    return std::all_of(s.begin(), s.end(), [](char c) { return isWordChar(c, "+/=_-"); });
}

bool isErrorCode(const std::string& s) {
    if (s.empty() || s.size() > 64) return false;
    return std::all_of(s.begin(), s.end(), [](char c) { return isWordChar(c, "_."); });
}

// Graph's structured `error.code`, never `error.message` or the raw body.
std::string errorCode(const HttpResponse& response) {
    const json parsed = json::parse(response.body, nullptr, false);
    if (parsed.is_discarded()) return "";
    const json& err = field(parsed, "error");
    if (!err.is_object()) return "";
    const json& code = field(err, "code");
    const std::string out = truthy(code) ? pyStr(code) : "";
    return isErrorCode(out) ? out : "";
}

std::string address(const json& entity) {
    if (!truthy(entity)) return "";
    const json& email = field(entity, "emailAddress");
    const std::string name = pyStrip(textOf(field(email, "name")));
    const std::string addr = pyStrip(textOf(field(email, "address")));
    if (!name.empty() && !addr.empty() && lowerAscii(name) != lowerAscii(addr)) return name + " <" + addr + ">";
    return addr.empty() ? name : addr;
}

std::string addressList(const json& entities) {
    std::string out;
    if (!entities.is_array()) return out;
    for (const json& e : entities) {
        const std::string one = address(e);
        if (one.empty()) continue;
        if (!out.empty()) out += ", ";
        out += one;
    }
    return out;
}

json attachments(const json& msg) {
    json out = json::array();
    const json& listed = field(msg, "attachments");
    if (!listed.is_array()) return out;
    for (const json& att : listed) {
        if (truthy(field(att, "isInline"))) continue;
        long long size = 0;
        const json& rawSize = field(att, "size");
        if (truthy(rawSize) && !pyInt(rawSize, size)) {
            throw std::invalid_argument("Microsoft Graph returned an attachment size that is not a number: " +
                                        pyRepr(rawSize));
        }
        const json& name = field(att, "name");
        out.push_back({{"name", truthy(name) ? name : json("")},
                       {"mime_type", lowerAscii(textOf(field(att, "contentType")))},
                       {"size", size}});
    }
    return out;
}

json messageSummary(const json& msg, bool includeBody) {
    const json& flag = field(field(msg, "flag"), "flagStatus");
    std::map<std::string, std::string> headers;
    const json& listed = field(msg, "internetMessageHeaders");
    if (listed.is_array()) {
        for (const json& header : listed) {
            const json& name = field(header, "name");
            if (!truthy(name)) continue;
            const json& value = field(header, "value");
            headers[lowerAscii(pyStr(name))] = truthy(value) ? pyStr(value) : "";
        }
    }
    const auto header = [&](const char* name) {
        const auto it = headers.find(name);
        return it == headers.end() ? std::string() : it->second;
    };
    const json found = attachments(msg);
    const json& isRead = field(msg, "isRead");
    const json& categories = field(msg, "categories");
    const json& subject = field(msg, "subject");
    const json& receivedAt = field(msg, "receivedDateTime");
    json summary = {
        {"id", field(msg, "id")},
        {"thread_id", truthy(field(msg, "conversationId")) ? field(msg, "conversationId") : field(msg, "id")},
        {"subject", truthy(subject) ? subject : json("(no subject)")},
        {"from", address(field(msg, "from"))},
        {"to", addressList(field(msg, "toRecipients"))},
        {"cc", addressList(field(msg, "ccRecipients"))},
        {"received", truthy(receivedAt) ? receivedAt : json("")},
        {"unread", msg.is_object() && msg.contains("isRead") ? !truthy(isRead) : false},
        {"flagged", flag == "flagged" || flag == "complete"},
        {"categories", categories.is_array() ? categories : json::array()},
        {"preview", pyStrip(textOf(field(msg, "bodyPreview")))},
        // Graph's hasAttachments already ignores inline images.
        {"has_attachments", !found.empty() || truthy(field(msg, "hasAttachments"))},
        {"attachments", found},
        {"auto_submitted", header("auto-submitted")},
        {"x_autoreply", header("x-autoreply")},
    };
    if (includeBody) {
        const json& body = field(msg, "body");
        const json& content = field(body, "content");
        const std::string kind = textOf(field(body, "contentType"));
        summary["body"] = truthy(content) ? content : json("");
        summary["body_content_type"] = kind.empty() ? "text" : lowerAscii(kind);
    }
    return summary;
}

class OutlookReadBackend : public ReadBackend {
public:
    OutlookReadBackend(TokenSource token, Transport transport)
        : token_(std::move(token)), transport_(std::move(transport)) {}

    std::string getUserEmail() override {
        const json data = get("/me", {{"$select", "mail,userPrincipalName"}});
        // Personal accounts often have a null `mail` and the address on userPrincipalName.
        const json& mail = field(data, "mail");
        const json& upn = field(data, "userPrincipalName");
        const std::string address = truthy(mail) ? pyStr(mail) : truthy(upn) ? pyStr(upn) : "";
        if (address.empty()) {
            throw withConnector(MailboxError("Microsoft Graph returned no address for the connected account (both "
                                             "`mail` and `userPrincipalName` were empty). The connection is in an "
                                             "unusable state and needs reconnecting."),
                                kMicrosoft);
        }
        return address;
    }

    json listFolders(int limit) override {
        const json data = get("/me/mailFolders", {{"$top", std::to_string(clamp(limit))},
                                                  {"$select", "id,displayName,unreadItemCount,totalItemCount"}});
        json out = json::array();
        for (const json& f : values(data)) {
            const json& name = field(f, "displayName");
            out.push_back({{"id", field(f, "id")},
                           {"name", truthy(name) ? name : json("")},
                           {"unread", valueOr(f, "unreadItemCount", 0)},
                           {"total", valueOr(f, "totalItemCount", 0)}});
        }
        return out;
    }

    json listInbox(int limit, bool unreadOnly) override {
        Params params = {{"$top", std::to_string(clamp(limit))},
                         {"$select", kListSelect},
                         {"$expand", kAttachmentExpand},
                         {"$orderby", "receivedDateTime desc"}};
        if (unreadOnly) {
            // Graph wants the ordered property to lead $filter, or it answers InefficientFilter.
            params.emplace_back("$filter", "receivedDateTime ge 1900-01-01T00:00:00Z and isRead eq false");
        }
        return summaries(get("/me/mailFolders/inbox/messages", params));
    }

    json search(const std::string& query, int limit) override {
        if (pyStrip(query).empty()) throw std::invalid_argument("query must be a non-empty search string");
        // $search cannot be combined with $orderby, so hits come back in relevance order.
        return summaries(get("/me/messages", {{"$top", std::to_string(clamp(limit))},
                                              {"$select", kListSelect},
                                              {"$expand", kAttachmentExpand},
                                              {"$search", "\"" + query + "\""}}));
    }

    json getMessage(const std::string& messageId) override {
        if (pyStrip(messageId).empty()) throw std::invalid_argument("message_id must be a non-empty message id");
        if (!isGraphId(messageId)) {
            throw std::invalid_argument("message_id " + pyRepr(messageId) +
                                        " is not a Microsoft Graph message id. Use an id returned by list_inbox or "
                                        "search.");
        }
        // Quoted whole: a base64 id's `/` is data, never a path separator.
        const json data = get("/me/messages/" + percentEncode(messageId),
                              {{"$select", kFullSelect}, {"$expand", kAttachmentExpand}});
        return messageSummary(data, true);
    }

    json getThread(const std::string& threadId) override {
        // The id lands inside an OData string literal; the alphabet check keeps a quote out.
        if (!isGraphId(threadId)) {
            throw std::invalid_argument("thread_id " + pyRepr(threadId) +
                                        " is not a Microsoft Graph conversation id. Use the thread_id from a "
                                        "list_inbox or search_email result.");
        }
        // $orderby with this filter trips InefficientFilter, so the sort happens here.
        const json data = get("/me/messages", {{"$filter", "conversationId eq '" + threadId + "'"},
                                               {"$select", kFullSelect},
                                               {"$expand", kAttachmentExpand},
                                               {"$top", std::to_string(kThreadTop)}});
        std::vector<json> raw = values(data);
        const auto key = [](const json& m) {
            const json& when = field(m, "receivedDateTime");
            return truthy(when) ? pyStr(when) : std::string();
        };
        std::stable_sort(raw.begin(), raw.end(), [&](const json& a, const json& b) { return key(a) < key(b); });
        json messages = json::array();
        for (const json& msg : raw) {
            json summary = messageSummary(msg, true);
            summary["draft"] = truthy(field(msg, "isDraft"));
            summary["_label_ids"] = json::array();
            messages.push_back(summary);
        }
        return {{"thread_id", threadId},
                {"messages", messages},
                {"truncated", truthy(field(data, "@odata.nextLink"))}};
    }

    json listThreadIds(const std::string& folder, int limit) override {
        std::string name;
        if (folder == "inbox") name = "inbox";
        else if (folder == "sent") name = "sentitems";
        else throw std::invalid_argument("folder must be one of ['inbox', 'sent'], got " + pyRepr(folder));
        const int top = clamp(limit);
        const json data = get("/me/mailFolders/" + name + "/messages", {{"$top", std::to_string(top)},
                                                                       {"$select", "id,conversationId"},
                                                                       {"$orderby", "receivedDateTime desc"}});
        const std::vector<json> rows = values(data);
        json threadIds = json::array();
        for (const json& row : rows) {
            const json& tid = truthy(field(row, "conversationId")) ? field(row, "conversationId") : field(row, "id");
            if (truthy(tid) && std::find(threadIds.begin(), threadIds.end(), tid) == threadIds.end()) {
                threadIds.push_back(tid);
            }
        }
        return {{"thread_ids", threadIds},
                {"messages_listed", rows.size()},
                {"truncated", truthy(field(data, "@odata.nextLink")) || static_cast<int>(rows.size()) >= top}};
    }

private:
    static json valueOr(const json& obj, const char* key, int fallback) {
        return obj.is_object() && obj.contains(key) ? obj.at(key) : json(fallback);
    }

    static std::vector<json> values(const json& data) {
        const json& listed = field(data, "value");
        return listed.is_array() ? listed.get<std::vector<json>>() : std::vector<json>{};
    }

    static json summaries(const json& data) {
        json out = json::array();
        for (const json& m : values(data)) out.push_back(messageSummary(m, false));
        return out;
    }

    static int clamp(int limit) {
        if (limit < 1) throw std::invalid_argument("limit must be >= 1, got " + std::to_string(limit));
        return std::min(limit, kMaxTop);
    }

    // Built from the status and the documented `error.code` only, never the body.
    [[noreturn]] static void raise(const HttpResponse& response, const std::string& where) {
        const std::string code = errorCode(response);
        const int status = response.status;
        if (status == 401) {
            throw withConnector(MailboxAuthError("Microsoft Graph rejected the access token (401). The Outlook "
                                                 "connection has expired or been revoked and needs reconnecting. "
                                                 "(request: " +
                                                 where + ")"),
                                kMicrosoft);
        }
        if (status == 403) {
            throw withConnector(
                MailboxAuthError("Microsoft Graph refused the request (403 \xE2\x80\x94 insufficient permissions). The "
                                 "connected account has not granted Mail.ReadWrite to this agent, so it needs "
                                 "reconnecting with mail access approved. (request: " +
                                 where + "; code: " + (code.empty() ? "forbidden" : code) + ")"),
                kMicrosoft);
        }
        if (status == 404) {
            throw MailboxError("Microsoft Graph has no such message or conversation (404). The id may be mistyped, "
                               "or the mail was deleted. Use an id returned by list_inbox or search_email. (request: " +
                               where + ")");
        }
        if (status == 429) {
            throw MailboxError("Microsoft Graph is rate-limiting this mailbox (429). Retry after " +
                               response.header("Retry-After", "unknown") +
                               "s. Reduce `limit` if this repeats. (request: " + where + ")");
        }
        throw MailboxError("Microsoft Graph request failed: " + where + " returned " + std::to_string(status) +
                           (code.empty() ? "" : " (" + code + ")") +
                           ". Retry; if it persists, check Microsoft 365 service status and reconnect Microsoft "
                           "under Settings > Connectors.");
    }

    // Re-minted per request: a cached token would let a mid-scan revoke look like success.
    json get(const std::string& path, const Params& params) const {
        HttpCall call{"GET", kGraphApiBase + path + queryString(params), {{"Authorization", "Bearer " + token_()}}, ""};
        HttpResponse response;
        try {
            response = transport_(call);
        } catch (const std::exception& e) {
            throw MailboxError("Could not reach Microsoft Graph for " + path + ": " + e.what() +
                               ". Check network connectivity, then retry.");
        }
        if (response.status != 200) raise(response, "GET " + path);
        return parseAnswer(response, "Microsoft Graph", "GET " + path);
    }

    TokenSource token_;
    Transport transport_;
};

}  // namespace

std::unique_ptr<ReadBackend> makeOutlookReadBackend(TokenSource token, Transport transport) {
    return std::make_unique<OutlookReadBackend>(std::move(token), std::move(transport));
}

}  // namespace gaia::mail
