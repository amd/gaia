// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Read half of src/gaia/agents/tools/_email/gmail.py. Gmail API v1 resources
// are flattened here into the same summary shape the Graph backend emits.

#include "mail_internal.h"

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstdint>
#include <exception>
#include <map>
#include <optional>
#include <set>
#include <stdexcept>
#include <thread>

#include "lower_ascii.h"
#include "mail_text.h"

namespace gaia::mail {

using namespace detail;
using gaia::detail::lowerAscii;

namespace {

const std::string kGmailApiBase = "https://gmail.googleapis.com/gmail/v1";

// Every listed id costs a `messages.get`, so Graph's 999 would be 999 fetches.
constexpr int kGmailMaxLimit = 100;
// Under Gmail's per-user concurrency limit.
constexpr std::size_t kFetchConcurrency = 5;
// Gmail's `fields` syntax has no recursion, so the part tree is spelled out.
constexpr int kPartTreeDepth = 8;

const char* const kGmailEnableUrl = "https://console.cloud.google.com/apis/library/gmail.googleapis.com";

const std::set<std::string> kReservedLabelIds = {"INBOX",   "SENT",    "DRAFT",     "SPAM", "TRASH",
                                                 "UNREAD",  "STARRED", "IMPORTANT", "CHAT"};
const std::string kReservedLabelPrefix = "CATEGORY_";

std::string partFields(int depth) {
    std::string fields = "mimeType,filename,headers,body/size";
    if (depth) fields += ",parts(" + partFields(depth - 1) + ")";
    return fields;
}

const std::string& summaryFields() {
    static const std::string fields =
        "id,threadId,labelIds,snippet,internalDate,payload(" + partFields(kPartTreeDepth) + ")";
    return fields;
}

// The shape Gmail issues for a message id. Thread ids share it.
bool isGmailId(const std::string& s) {
    if (s.empty() || s.size() > 128) return false;
    return std::all_of(s.begin(), s.end(), [](char c) { return isWordChar(c, "_-"); });
}

// The shape Gmail documents for `errors[].reason` and `error.status`.
bool isReasonToken(const std::string& s) {
    if (s.empty() || s.size() > 64) return false;
    return std::all_of(s.begin(), s.end(), [](char c) { return isWordChar(c, "_."); });
}

// ---- error detail ----

struct ErrorDetail {
    std::string reason;
    std::string helpUrl;
};

// Gmail's structured error fields only, never the upstream-controlled body.
ErrorDetail errorDetail(const HttpResponse& response) {
    const json parsed = json::parse(response.body, nullptr, false);
    if (parsed.is_discarded()) return {};
    const json& err = field(parsed, "error");
    if (!err.is_object()) return {};
    const json& errors = field(err, "errors");
    const json& first = errors.is_array() && !errors.empty() ? errors[0] : field(json(), "");
    const json& reasonValue = truthy(field(first, "reason")) ? field(first, "reason") : field(err, "status");
    ErrorDetail out;
    out.reason = truthy(reasonValue) ? pyStr(reasonValue) : "";
    if (!isReasonToken(out.reason)) out.reason.clear();
    const json& help = field(first, "extendedHelp");
    out.helpUrl = truthy(help) ? pyStr(help) : "";
    if (out.helpUrl.compare(0, 8, "https://") != 0) out.helpUrl.clear();
    return out;
}

// ---- base64 (binascii.a2b_base64, non-strict) ----

bool a2bBase64(const std::string& in, std::string& out) {
    out.clear();
    int quadPos = 0;
    int pads = 0;
    unsigned leftchar = 0;
    for (const char ch : in) {
        if (ch == '=') {
            if (quadPos >= 2 && quadPos + ++pads >= 4) return true;
            continue;
        }
        int v;
        if (ch >= 'A' && ch <= 'Z') v = ch - 'A';
        else if (ch >= 'a' && ch <= 'z') v = ch - 'a' + 26;
        else if (ch >= '0' && ch <= '9') v = ch - '0' + 52;
        else if (ch == '+') v = 62;
        else if (ch == '/') v = 63;
        else continue;
        pads = 0;
        const auto u = static_cast<unsigned>(v);
        switch (quadPos) {
            case 0:
                quadPos = 1;
                leftchar = u;
                break;
            case 1:
                quadPos = 2;
                out += static_cast<char>(((leftchar << 2) | (u >> 4)) & 0xFF);
                leftchar = u & 0x0F;
                break;
            case 2:
                quadPos = 3;
                out += static_cast<char>(((leftchar << 4) | (u >> 2)) & 0xFF);
                leftchar = u & 0x03;
                break;
            default:
                quadPos = 0;
                out += static_cast<char>(((leftchar << 6) | u) & 0xFF);
                leftchar = 0;
                break;
        }
    }
    return quadPos == 0;
}

// ---- charsets ----

// cp1252 for 0x80-0x9F; 0 where the codec has no character.
const long kCp1252High[32] = {0x20AC, 0,      0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021,
                              0x02C6, 0x2030, 0x0160, 0x2039, 0x0152, 0,      0x017D, 0,
                              0,      0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2013, 0x2014,
                              0x02DC, 0x2122, 0x0161, 0x203A, 0x0153, 0,      0x017E, 0x0178};

// email.charset ALIASES, applied to a lowercased name.
std::string charsetName(const std::string& lowered) {
    if (lowered == "latin_1" || lowered == "latin-1") return "iso-8859-1";
    if (lowered == "ascii") return "us-ascii";
    if (lowered == "utf8") return "utf-8";
    return lowered;
}

// Strict decode to UTF-8; false where Python raises LookupError or UnicodeDecodeError.
bool decodeCharset(const std::string& bytes, const std::string& name, std::string& out) {
    out.clear();
    if (name == "utf-8") {
        out = bytes;
        return utf8Replace(bytes) == bytes;
    }
    if (name == "us-ascii") {
        out = bytes;
        return std::all_of(bytes.begin(), bytes.end(), [](char c) { return static_cast<unsigned char>(c) < 0x80; });
    }
    const bool latin1 = name == "iso-8859-1" || name == "iso8859-1" || name == "iso_8859_1" ||
                        name == "latin1" || name == "l1";
    const bool cp1252 = name == "windows-1252" || name == "cp1252";
    if (!latin1 && !cp1252) return false;
    for (const char ch : bytes) {
        const auto c = static_cast<unsigned char>(ch);
        long cp = c;
        if (cp1252 && c >= 0x80 && c <= 0x9F) {
            cp = kCp1252High[c - 0x80];
            if (cp == 0) return false;
        }
        appendUtf8(out, cp);
    }
    return true;
}

// ---- RFC 2047 (email.header.decode_header + make_header) ----

struct Word {
    std::string text;
    bool encoded = false;
    char encoding = 0;
    std::string charset;
};

// The encoded words and the text between them on one line, as ecre.split gives.
std::vector<Word> splitEncodedWords(const std::string& line) {
    std::vector<Word> out;
    std::string pending;
    std::size_t i = 0;
    while (i < line.size()) {
        if (line.compare(i, 2, "=?") == 0) {
            const std::size_t q1 = line.find('?', i + 2);
            if (q1 != std::string::npos && q1 + 2 < line.size() &&
                std::string("qQbB").find(line[q1 + 1]) != std::string::npos && line[q1 + 2] == '?') {
                const std::size_t close = line.find("?=", q1 + 3);
                if (close != std::string::npos) {
                    Word unencoded;
                    unencoded.text = pending;
                    out.push_back(unencoded);
                    pending.clear();
                    Word w;
                    w.encoded = true;
                    w.charset = line.substr(i + 2, q1 - i - 2);
                    w.encoding = static_cast<char>(std::tolower(static_cast<unsigned char>(line[q1 + 1])));
                    w.text = line.substr(q1 + 3, close - q1 - 3);
                    out.push_back(w);
                    i = close + 2;
                    continue;
                }
            }
        }
        pending += line[i++];
    }
    Word tail;
    tail.text = pending;
    out.push_back(tail);
    return out;
}

bool hasEncodedWord(const std::string& raw) {
    std::size_t start = 0;
    while (start <= raw.size()) {
        const std::size_t nl = raw.find_first_of("\r\n", start);
        const std::string line = raw.substr(start, nl == std::string::npos ? std::string::npos : nl - start);
        if (splitEncodedWords(line).size() > 1) return true;
        if (nl == std::string::npos) break;
        start = nl + 1;
    }
    return false;
}

// Header._nonctext of the first or last code point of `s`.
bool nonCtext(const std::string& s, bool first) {
    if (s.empty()) return false;
    std::string cp;
    if (first) {
        std::size_t n = 1;
        while (n < s.size() && (static_cast<unsigned char>(s[n]) & 0xC0) == 0x80) ++n;
        cp = s.substr(0, n);
    } else {
        std::size_t start = s.size() - 1;
        while (start > 0 && (static_cast<unsigned char>(s[start]) & 0xC0) == 0x80) --start;
        cp = s.substr(start);
    }
    return cp == "(" || cp == ")" || cp == "\\" || pyIsSpace(cp);
}

bool isAsciiCharset(const std::string& cs) { return cs.empty() || cs == "us-ascii"; }

// Throws std::invalid_argument where Python's decode raises one of the caught errors.
std::string decodeHeader(const std::string& raw) {
    std::vector<Word> words;
    std::size_t start = 0;
    while (true) {
        const std::size_t nl = raw.find_first_of("\r\n", start);
        const std::string line = raw.substr(start, nl == std::string::npos ? std::string::npos : nl - start);
        bool first = true;
        for (Word w : splitEncodedWords(line)) {
            if (!w.encoded) {
                if (first) w.text = pyLstrip(w.text);
                first = false;
                if (!w.text.empty()) words.push_back(w);
            } else {
                first = false;
                w.charset = lowerAscii(w.charset);
                words.push_back(w);
            }
        }
        if (nl == std::string::npos) break;
        start = nl + ((raw.compare(nl, 2, "\r\n") == 0) ? 2 : 1);
        if (start >= raw.size()) break;
    }

    std::vector<std::size_t> drop;
    for (std::size_t n = 2; n < words.size(); ++n) {
        if (words[n].encoded && words[n - 2].encoded && pyIsSpace(words[n - 1].text)) drop.push_back(n - 1);
    }
    for (auto it = drop.rbegin(); it != drop.rend(); ++it) words.erase(words.begin() + static_cast<long>(*it));

    // (bytes, charset) with consecutive same-charset runs collapsed.
    std::vector<std::pair<std::string, std::string>> collapsed;
    bool haveLast = false;
    for (const Word& w : words) {
        std::string bytes;
        if (!w.encoded) {
            bytes = w.text;
        } else if (w.encoding == 'q') {
            for (std::size_t k = 0; k < w.text.size(); ++k) {
                const char c = w.text[k];
                if (c == '=' && k + 2 < w.text.size() && std::isxdigit(static_cast<unsigned char>(w.text[k + 1])) &&
                    std::isxdigit(static_cast<unsigned char>(w.text[k + 2]))) {
                    bytes += static_cast<char>(std::stoi(w.text.substr(k + 1, 2), nullptr, 16));
                    k += 2;
                } else {
                    bytes += c == '_' ? ' ' : c;
                }
            }
        } else {
            std::string padded = w.text;
            if (padded.size() % 4) padded += std::string("===").substr(0, 4 - padded.size() % 4);
            if (!a2bBase64(padded, bytes)) throw std::invalid_argument("base64 decoding error");
        }
        const std::string cs = w.encoded ? w.charset : std::string();
        if (!haveLast) {
            collapsed.emplace_back(bytes, cs);
            haveLast = true;
        } else if (cs != collapsed.back().second) {
            collapsed.emplace_back(bytes, cs);
        } else if (cs.empty()) {
            collapsed.back().first += " " + bytes;
        } else {
            collapsed.back().first += bytes;
        }
    }

    // make_header: decode each chunk, merge equal charsets, then join.
    std::vector<std::pair<std::string, std::string>> chunks;
    for (const auto& [bytes, cs] : collapsed) {
        const std::string name = cs.empty() ? "us-ascii" : charsetName(cs);
        std::string decoded;
        if (!decodeCharset(bytes, name, decoded)) throw std::invalid_argument("undecodable header");
        if (!chunks.empty() && chunks.back().second == name) {
            chunks.back().first += " " + decoded;
        } else {
            chunks.emplace_back(decoded, name);
        }
    }
    std::string out;
    std::string lastcs;
    bool lastspace = false;
    bool any = false;
    for (const auto& [s, cs] : chunks) {
        std::string nextcs = cs;
        if (any) {
            const bool hasspace = nonCtext(s, true);
            if (!isAsciiCharset(lastcs)) {
                if (isAsciiCharset(nextcs) && !hasspace) {
                    out += ' ';
                    nextcs.clear();
                }
            } else if (!isAsciiCharset(nextcs) && !lastspace) {
                out += ' ';
            }
        }
        lastspace = nonCtext(s, false);
        lastcs = nextcs;
        out += s;
        any = true;
    }
    return out;
}

// Decode RFC 2047 encoded words. Idempotent on already-plain text.
std::string decodeWords(const std::string& raw) {
    if (raw.empty()) return "";
    if (!hasEncodedWord(raw)) return raw;
    try {
        return decodeHeader(raw);
    } catch (const std::invalid_argument&) {
        return raw;
    }
}

// ---- addresses ----

// One RFC 5322 address header as Graph's `Name <addr>` list.
std::string addressList(const std::string& raw) {
    std::string out;
    for (const auto& address : text::getaddresses(decodeWords(raw))) {
        const std::string name = pyStrip(address.name), addr = pyStrip(address.addr);
        std::string part;
        if (!name.empty() && !addr.empty() && lowerAscii(name) != lowerAscii(addr)) {
            part = name + " <" + addr + ">";
        } else if (!addr.empty() || !name.empty()) {
            part = addr.empty() ? name : addr;
        } else {
            continue;
        }
        if (!out.empty()) out += ", ";
        out += part;
    }
    return out;
}

// ---- message parts ----

using HeaderMap = std::map<std::string, std::string>;

HeaderMap headerMap(const json& part) {
    HeaderMap out;
    const json& headers = field(part, "headers");
    if (!headers.is_array()) return out;
    for (const json& header : headers) {
        const std::string name = lowerAscii(textOf(field(header, "name")));
        if (!name.empty() && !out.count(name)) out[name] = textOf(field(header, "value"));
    }
    return out;
}

std::string headerOf(const HeaderMap& headers, const std::string& name) {
    const auto it = headers.find(name);
    return it == headers.end() ? std::string() : it->second;
}

std::string received(const json& internalDate) {
    long long millis = 0;
    if (!pyInt(internalDate, millis)) return "";
    long long secs = millis / 1000;
    if (millis % 1000 < 0) --secs;
    long long days = secs / 86400;
    long long rem = secs % 86400;
    if (rem < 0) {
        rem += 86400;
        --days;
    }
    // Days since 1970-01-01 to a civil date (Howard Hinnant's algorithm).
    days += 719468;
    const long long era = (days >= 0 ? days : days - 146096) / 146097;
    const long long doe = days - era * 146097;
    const long long yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    long long y = yoe + era * 400;
    const long long doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    const long long mp = (5 * doy + 2) / 153;
    const long long d = doy - (153 * mp + 2) / 5 + 1;
    const long long m = mp < 10 ? mp + 3 : mp - 9;
    if (m <= 2) ++y;
    if (y < 1 || y > 9999) return "";
    char buf[32];
    std::snprintf(buf, sizeof buf, "%04lld-%02lld-%02lldT%02lld:%02lld:%02lldZ", y, m, d, rem / 3600, rem / 60 % 60,
                  rem % 60);
    return buf;
}

const json* findPart(const json& part, const std::string& mimeType) {
    if (lowerAscii(textOf(field(part, "mimeType"))) == mimeType && !truthy(field(part, "filename"))) return &part;
    const json& parts = field(part, "parts");
    if (!parts.is_array()) return nullptr;
    for (const json& child : parts) {
        if (const json* found = findPart(child, mimeType)) return found;
    }
    return nullptr;
}

std::string decodePart(const json& part) {
    const std::string data = textOf(field(field(part, "body"), "data"));
    if (data.empty()) return "";
    const auto badBase64 = [] {
        return MailboxError(
            "Gmail returned a message body that is not valid base64, so it cannot be read. Open the "
            "message in Gmail directly, and report this at https://github.com/amd/gaia/issues if it "
            "repeats.");
    };
    std::string translated;
    for (const char c : data) {
        if (static_cast<unsigned char>(c) >= 0x80) throw badBase64();
        translated += c == '-' ? '+' : c == '_' ? '/' : c;
    }
    translated += std::string((4 - data.size() % 4) % 4, '=');
    std::string raw;
    if (!a2bBase64(translated, raw)) throw badBase64();
    return utf8Replace(raw);
}

std::pair<std::string, std::string> selectBody(const json& payload) {
    static const std::pair<const char*, const char*> kKinds[] = {{"text/plain", "text"}, {"text/html", "html"}};
    for (const auto& [mimeType, kind] : kKinds) {
        if (const json* part = findPart(payload, mimeType)) return {decodePart(*part), kind};
    }
    return {"", "text"};
}

// An inline image (signature logo, cid: reference), not a file. Apple Mail
// marks PDFs inline too, so only images count.
bool isInline(const json& part) {
    if (lowerAscii(textOf(field(part, "mimeType"))).compare(0, 6, "image/") != 0) return false;
    const HeaderMap headers = headerMap(part);
    const std::string disposition = lowerAscii(pyStrip(headerOf(headers, "content-disposition")));
    if (disposition.compare(0, 10, "attachment") == 0) return false;
    return disposition.compare(0, 6, "inline") == 0 || !headerOf(headers, "content-id").empty();
}

void collectAttachments(const json& part, json& found) {
    const std::string filename = textOf(field(part, "filename"));
    if (!filename.empty() && !isInline(part)) {
        long long size = 0;
        const json& rawSize = field(field(part, "body"), "size");
        if (truthy(rawSize) && !pyInt(rawSize, size)) {
            throw std::invalid_argument("Gmail returned a part size that is not a number: " + pyRepr(rawSize));
        }
        found.push_back({{"name", decodeWords(filename)},
                         {"mime_type", lowerAscii(textOf(field(part, "mimeType")))},
                         {"size", size}});
    }
    const json& parts = field(part, "parts");
    if (!parts.is_array()) return;
    for (const json& child : parts) collectAttachments(child, found);
}

json categories(const json& labelIds, const std::map<std::string, json>& labelNames) {
    json out = json::array();
    if (!labelIds.is_array()) return out;
    for (const json& id : labelIds) {
        if (!id.is_string()) continue;
        const std::string lid = id.get<std::string>();
        if (kReservedLabelIds.count(lid) || lid.compare(0, kReservedLabelPrefix.size(), kReservedLabelPrefix) == 0) continue;
        const auto it = labelNames.find(lid);
        out.push_back(it == labelNames.end() ? id : it->second);
    }
    return out;
}

bool hasLabel(const json& labelIds, const char* label) {
    if (!labelIds.is_array()) return false;
    return std::any_of(labelIds.begin(), labelIds.end(), [&](const json& l) { return l.is_string() && l == label; });
}

json messageSummary(const json& msg, const std::map<std::string, json>& labelNames, bool includeBody) {
    const json& payload = field(msg, "payload");
    const HeaderMap headers = headerMap(payload);
    const json& labelIds = field(msg, "labelIds");
    json attachments = json::array();
    collectAttachments(payload, attachments);
    const std::string subject = decodeWords(headerOf(headers, "subject"));
    json summary = {
        {"id", field(msg, "id")},
        {"thread_id", truthy(field(msg, "threadId")) ? field(msg, "threadId") : field(msg, "id")},
        {"subject", subject.empty() ? "(no subject)" : subject},
        {"from", addressList(headerOf(headers, "from"))},
        {"to", addressList(headerOf(headers, "to"))},
        {"cc", addressList(headerOf(headers, "cc"))},
        {"received", received(field(msg, "internalDate"))},
        {"unread", hasLabel(labelIds, "UNREAD")},
        {"flagged", hasLabel(labelIds, "STARRED")},
        {"categories", categories(labelIds, labelNames)},
        // Gmail entity-encodes the snippet where Graph's bodyPreview is plain.
        {"preview", pyStrip(text::htmlUnescape(textOf(field(msg, "snippet"))))},
        {"has_attachments", !attachments.empty()},
        {"attachments", attachments},
        {"auto_submitted", decodeWords(headerOf(headers, "auto-submitted"))},
        {"x_autoreply", decodeWords(headerOf(headers, "x-autoreply"))},
    };
    if (includeBody) {
        const auto [body, kind] = selectBody(payload);
        summary["body"] = body;
        summary["body_content_type"] = kind;
    }
    return summary;
}

// ---- backend ----

class GmailReadBackend : public ReadBackend {
public:
    GmailReadBackend(TokenSource token, Transport transport)
        : token_(std::move(token)), transport_(std::move(transport)) {}

    std::string getUserEmail() override {
        const json data = get("/users/me/profile");
        const std::string address = pyStrip(textOf(field(data, "emailAddress")));
        if (address.empty()) {
            throw withConnector(MailboxError("Gmail returned no address for the connected account (`emailAddress` "
                                             "was empty). The connection is in an unusable state and needs "
                                             "reconnecting."),
                                kGoogle);
        }
        return address;
    }

    json listFolders(int limit) override {
        std::vector<std::string> ids;
        for (const json& lab : labels()) ids.push_back(pyStr(lab["id"]));
        const auto keep = static_cast<std::size_t>(clamp(limit));
        if (ids.size() > keep) ids.resize(keep);
        json out = json::array();
        if (ids.empty()) return out;
        std::vector<std::string> paths;
        for (const auto& id : ids) paths.push_back("/users/me/labels/" + percentEncode(id));
        for (const json& detail : fanOut(paths, {})) {
            const json& id = field(detail, "id");
            const json& name = field(detail, "name");
            out.push_back({{"id", id},
                           {"name", truthy(name) ? name : truthy(id) ? id : json("")},
                           {"unread", valueOr(detail, "messagesUnread", 0)},
                           {"total", valueOr(detail, "messagesTotal", 0)}});
        }
        return out;
    }

    json listInbox(int limit, bool unreadOnly) override {
        return fetchSummaries(listIds(unreadOnly ? std::vector<std::string>{"INBOX", "UNREAD"}
                                                 : std::vector<std::string>{"INBOX"},
                                      "", limit));
    }

    json search(const std::string& query, int limit) override {
        if (pyStrip(query).empty()) throw std::invalid_argument("query must be a non-empty search string");
        return fetchSummaries(listIds({}, query, limit));
    }

    json getMessage(const std::string& messageId) override {
        if (pyStrip(messageId).empty()) throw std::invalid_argument("message_id must be a non-empty message id");
        if (!isGmailId(messageId)) {
            throw std::invalid_argument("message_id " + pyRepr(messageId) +
                                        " is not a Gmail message id. Use an id returned by list_inbox or search.");
        }
        const json data = get("/users/me/messages/" + messageId, {{"format", "full"}});
        return messageSummary(data, labelMap(), true);
    }

    json getThread(const std::string& threadId) override {
        if (!isGmailId(threadId)) {
            throw std::invalid_argument("thread_id " + pyRepr(threadId) +
                                        " is not a Gmail thread id. Use the thread_id from a list_inbox or "
                                        "search_email result.");
        }
        const json data = get("/users/me/threads/" + threadId, {{"format", "full"}});
        std::vector<std::pair<long long, json>> raw;
        const json& listed = field(data, "messages");
        if (listed.is_array()) {
            for (const json& m : listed) {
                long long when = 0;
                const json& date = field(m, "internalDate");
                if (truthy(date) && !pyInt(date, when)) {
                    throw std::invalid_argument("Gmail returned an internalDate that is not a number: " + pyRepr(date));
                }
                raw.emplace_back(when, m);
            }
        }
        // Gmail does not promise thread order.
        std::stable_sort(raw.begin(), raw.end(), [](const auto& a, const auto& b) { return a.first < b.first; });
        const auto names = labelMap();
        json messages = json::array();
        for (const auto& [when, msg] : raw) {
            (void)when;
            json summary = messageSummary(msg, names, true);
            const json& labelIds = field(msg, "labelIds");
            summary["draft"] = hasLabel(labelIds, "DRAFT");
            summary["_label_ids"] = truthy(labelIds) && labelIds.is_array() ? labelIds : json::array();
            messages.push_back(summary);
        }
        return {{"thread_id", threadId}, {"messages", messages}, {"truncated", false}};
    }

    json listThreadIds(const std::string& folder, int limit) override {
        std::string label;
        if (folder == "inbox") label = "INBOX";
        else if (folder == "sent") label = "SENT";
        else throw std::invalid_argument("folder must be one of ['inbox', 'sent'], got " + pyRepr(folder));
        const int wanted = clamp(limit);
        const json data = get("/users/me/messages", {{"labelIds", label}, {"maxResults", std::to_string(wanted)}});
        const json& listed = field(data, "messages");
        const json stubs = truthy(listed) && listed.is_array() ? listed : json::array();
        json threadIds = json::array();
        for (const json& stub : stubs) {
            const json& tid = field(stub, "threadId");
            if (!truthy(tid)) {
                throw MailboxError("Gmail listed message " + pyRepr(field(stub, "id")) +
                                   " without a threadId, so it cannot be grouped into a conversation. Retry; "
                                   "report it at https://github.com/amd/gaia/issues if it repeats.");
            }
            if (std::find(threadIds.begin(), threadIds.end(), tid) == threadIds.end()) threadIds.push_back(tid);
        }
        return {{"thread_ids", threadIds},
                {"messages_listed", stubs.size()},
                {"truncated", truthy(field(data, "nextPageToken")) || static_cast<int>(stubs.size()) >= wanted}};
    }

private:
    static json valueOr(const json& obj, const char* key, int fallback) {
        return obj.is_object() && obj.contains(key) ? obj.at(key) : json(fallback);
    }

    static int clamp(int limit) {
        if (limit < 1) throw std::invalid_argument("limit must be >= 1, got " + std::to_string(limit));
        return std::min(limit, kGmailMaxLimit);
    }

    [[noreturn]] static void raise(const HttpResponse& response, const std::string& where) {
        const ErrorDetail detail = errorDetail(response);
        const int code = response.status;
        if (code == 401) {
            throw withConnector(MailboxAuthError("Gmail rejected the access token (401). The Google connection has "
                                                 "expired or been revoked and needs reconnecting. (request: " +
                                                 where + ")"),
                                kGoogle);
        }
        if (code == 403 && detail.reason == "accessNotConfigured") {
            throw MailboxAuthError(
                "The Gmail API is not enabled for the Google Cloud project behind this connection (403 "
                "accessNotConfigured), so no mailbox can be read. Enable it, wait a minute, then retry: " +
                (detail.helpUrl.empty() ? std::string(kGmailEnableUrl) : detail.helpUrl) + " (request: " + where +
                ")");
        }
        if (code == 403) {
            throw withConnector(
                MailboxAuthError("Gmail refused the request (403 \xE2\x80\x94 insufficient permissions). The connected "
                                 "Google account has not given GAIA permission to read mail, so it needs reconnecting "
                                 "with mail access ticked. (request: " +
                                 where + "; reason: " + (detail.reason.empty() ? "forbidden" : detail.reason) + ")"),
                kGoogle);
        }
        if (code == 404) {
            throw MailboxError("Gmail has no such message or thread (404). The id may be mistyped, or the mail was "
                               "deleted. Use an id returned by list_inbox or search_email. (request: " +
                               where + ")");
        }
        if (code == 429) {
            throw MailboxError("Gmail is rate-limiting this mailbox (429). Retry after " +
                               response.header("Retry-After", "unknown") +
                               "s. Reduce `limit` if this repeats. (request: " + where + ")");
        }
        throw MailboxError("Gmail request failed: " + where + " returned " + std::to_string(code) +
                           (detail.reason.empty() ? "" : " (" + detail.reason + ")") +
                           ". Retry, and check https://www.google.com/appsstatus if it persists.");
    }

    // Re-minted per request unless a fan-out already minted one: a cached token
    // would let a mid-scan revoke look like success.
    json get(const std::string& path, const Params& params = {}, const std::string& token = "") const {
        const std::string bearer = token.empty() ? token_() : token;
        HttpCall call{"GET", kGmailApiBase + path + queryString(params), {{"Authorization", "Bearer " + bearer}}, ""};
        HttpResponse response;
        try {
            response = transport_(call);
        } catch (const std::exception& e) {
            throw MailboxError("Could not reach Gmail for " + path + ": " + e.what() +
                               ". Check network connectivity, then retry.");
        }
        if (response.status != 200) raise(response, "GET " + path);
        return parseAnswer(response, "Gmail", "GET " + path);
    }

    const std::vector<json>& labels() {
        if (!labelCache_) {
            const json data = get("/users/me/labels");
            std::vector<json> kept;
            const json& listed = field(data, "labels");
            if (listed.is_array()) {
                for (const json& lab : listed) {
                    if (truthy(field(lab, "id"))) kept.push_back(lab);
                }
            }
            labelCache_ = std::move(kept);
        }
        return *labelCache_;
    }

    // Label id -> display name; the message resource carries only opaque ids.
    std::map<std::string, json> labelMap() {
        std::map<std::string, json> out;
        for (const json& lab : labels()) {
            const json& name = field(lab, "name");
            out[pyStr(lab["id"])] = truthy(name) ? name : lab["id"];
        }
        return out;
    }

    std::vector<json> fanOut(const std::vector<std::string>& paths, const Params& params) const {
        const std::string token = token_();
        std::vector<json> results(paths.size());
        std::vector<std::exception_ptr> errors(paths.size());
        std::atomic<std::size_t> next{0};
        const auto worker = [&] {
            for (std::size_t i = next++; i < paths.size(); i = next++) {
                try {
                    results[i] = get(paths[i], params, token);
                } catch (...) {
                    errors[i] = std::current_exception();
                }
            }
        };
        std::vector<std::thread> pool;
        for (std::size_t n = 0; n < std::min(kFetchConcurrency, paths.size()); ++n) pool.emplace_back(worker);
        for (auto& t : pool) t.join();
        // A failed subrequest raises rather than shortening the listing into a smaller-looking inbox.
        for (const auto& e : errors) {
            if (e) std::rethrow_exception(e);
        }
        return results;
    }

    std::vector<std::string> listIds(const std::vector<std::string>& labelIds, const std::string& query, int limit) {
        Params params = {{"maxResults", std::to_string(clamp(limit))}};
        for (const auto& id : labelIds) params.emplace_back("labelIds", id);
        if (!query.empty()) params.emplace_back("q", query);
        const json data = get("/users/me/messages", params);
        std::vector<std::string> ids;
        // The key is absent, not empty, when nothing matches.
        const json& listed = field(data, "messages");
        if (!listed.is_array()) return ids;
        for (const json& m : listed) {
            if (truthy(field(m, "id"))) ids.push_back(pyStr(m["id"]));
        }
        return ids;
    }

    json fetchSummaries(const std::vector<std::string>& ids) {
        json out = json::array();
        if (ids.empty()) return out;
        const auto names = labelMap();
        std::vector<std::string> paths;
        for (const auto& id : ids) paths.push_back("/users/me/messages/" + percentEncode(id));
        for (const json& m : fanOut(paths, {{"format", "full"}, {"fields", summaryFields()}})) {
            out.push_back(messageSummary(m, names, false));
        }
        return out;
    }

    TokenSource token_;
    Transport transport_;
    std::optional<std::vector<json>> labelCache_;
};

}  // namespace

std::unique_ptr<ReadBackend> makeGmailReadBackend(TokenSource token, Transport transport) {
    return std::make_unique<GmailReadBackend>(std::move(token), std::move(transport));
}

}  // namespace gaia::mail
