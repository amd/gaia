// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "mail_internal.h"

#include <cstdint>
#include <memory>
#include <mutex>
#include <stdexcept>

#include "gaia/http_client.h"

namespace gaia::mail {

namespace detail {

namespace {

// One code point at `i`; -1 for a byte that does not start valid UTF-8.
long decodeAt(const std::string& s, std::size_t i, std::size_t& len) {
    const auto b = [&](std::size_t k) { return static_cast<unsigned char>(s[k]); };
    const unsigned char c = b(i);
    len = 1;
    if (c < 0x80) return c;
    std::size_t n = 0;
    long cp = 0;
    if ((c & 0xE0) == 0xC0) { n = 2; cp = c & 0x1F; }
    else if ((c & 0xF0) == 0xE0) { n = 3; cp = c & 0x0F; }
    else if ((c & 0xF8) == 0xF0) { n = 4; cp = c & 0x07; }
    else return -1;
    if (i + n > s.size()) return -1;
    for (std::size_t k = 1; k < n; ++k) {
        if ((b(i + k) & 0xC0) != 0x80) return -1;
        cp = (cp << 6) | (b(i + k) & 0x3F);
    }
    len = n;
    return cp;
}

bool isUnicodeSpace(long cp) {
    return (cp >= 0x09 && cp <= 0x0D) || (cp >= 0x1C && cp <= 0x20) || cp == 0x85 || cp == 0xA0 ||
           cp == 0x1680 || (cp >= 0x2000 && cp <= 0x200A) || cp == 0x2028 || cp == 0x2029 ||
           cp == 0x202F || cp == 0x205F || cp == 0x3000;
}

// Byte offset just past the leading whitespace.
std::size_t leadingSpaceEnd(const std::string& s) {
    std::size_t i = 0;
    while (i < s.size()) {
        std::size_t len = 1;
        const long cp = decodeAt(s, i, len);
        if (cp < 0 || !isUnicodeSpace(cp)) break;
        i += len;
    }
    return i;
}

// Byte offset of the start of the trailing whitespace.
std::size_t trailingSpaceStart(const std::string& s, std::size_t floor) {
    std::size_t end = s.size();
    while (end > floor) {
        std::size_t start = end - 1;
        while (start > floor && (static_cast<unsigned char>(s[start]) & 0xC0) == 0x80) --start;
        std::size_t len = 1;
        const long cp = decodeAt(s, start, len);
        if (cp < 0 || start + len != end || !isUnicodeSpace(cp)) break;
        end = start;
    }
    return end;
}

}  // namespace

void appendUtf8(std::string& out, long cp) {
    if (cp < 0x80) {
        out += static_cast<char>(cp);
    } else if (cp < 0x800) {
        out += static_cast<char>(0xC0 | (cp >> 6));
        out += static_cast<char>(0x80 | (cp & 0x3F));
    } else if (cp < 0x10000) {
        out += static_cast<char>(0xE0 | (cp >> 12));
        out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
        out += static_cast<char>(0x80 | (cp & 0x3F));
    } else {
        out += static_cast<char>(0xF0 | (cp >> 18));
        out += static_cast<char>(0x80 | ((cp >> 12) & 0x3F));
        out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
        out += static_cast<char>(0x80 | (cp & 0x3F));
    }
}

const json& field(const json& obj, const char* key) {
    static const json kNull = nullptr;
    if (!obj.is_object()) return kNull;
    const auto it = obj.find(key);
    return it == obj.end() ? kNull : *it;
}

bool truthy(const json& value) {
    switch (value.type()) {
        case json::value_t::null:
        case json::value_t::discarded:
            return false;
        case json::value_t::boolean:
            return value.get<bool>();
        case json::value_t::number_integer:
            return value.get<std::int64_t>() != 0;
        case json::value_t::number_unsigned:
            return value.get<std::uint64_t>() != 0;
        case json::value_t::number_float:
            return value.get<double>() != 0.0;
        case json::value_t::string:
            return !value.get_ref<const std::string&>().empty();
        default:
            return !value.empty();
    }
}

bool isWordChar(char c, const char* extra) {
    return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') ||
           std::string(extra).find(c) != std::string::npos;
}

std::string textOf(const json& value) {
    return value.is_string() ? value.get<std::string>() : std::string();
}

std::string pyStr(const json& value) {
    if (value.is_string()) return value.get<std::string>();
    if (value.is_null()) return "None";
    if (value.is_boolean()) return value.get<bool>() ? "True" : "False";
    return value.dump();
}

std::string pyRepr(const json& value) {
    if (!value.is_string()) return pyStr(value);
    const std::string& s = value.get_ref<const std::string&>();
    const char quote = (s.find('\'') != std::string::npos && s.find('"') == std::string::npos) ? '"' : '\'';
    std::string out(1, quote);
    for (const char ch : s) {
        const auto c = static_cast<unsigned char>(ch);
        if (ch == '\\' || ch == quote) {
            out += '\\';
            out += ch;
        } else if (ch == '\n') {
            out += "\\n";
        } else if (ch == '\r') {
            out += "\\r";
        } else if (ch == '\t') {
            out += "\\t";
        } else if (c < 0x20 || c == 0x7F) {
            static const char* hex = "0123456789abcdef";
            out += "\\x";
            out += hex[c >> 4];
            out += hex[c & 0xF];
        } else {
            out += ch;
        }
    }
    out += quote;
    return out;
}

bool pyInt(const json& value, long long& out) {
    if (value.is_boolean()) {
        out = value.get<bool>() ? 1 : 0;
        return true;
    }
    if (value.is_number_unsigned()) {
        if (value.get<std::uint64_t>() > 9223372036854775807ULL) return false;
        out = static_cast<long long>(value.get<std::uint64_t>());
        return true;
    }
    if (value.is_number_integer()) {
        out = value.get<long long>();
        return true;
    }
    if (value.is_number_float()) {
        const double d = value.get<double>();
        if (!(d > -9.2e18 && d < 9.2e18)) return false;
        out = static_cast<long long>(d);
        return true;
    }
    if (!value.is_string()) return false;
    const std::string s = pyStrip(value.get<std::string>());
    std::size_t i = 0;
    bool negative = false;
    if (i < s.size() && (s[i] == '+' || s[i] == '-')) negative = s[i++] == '-';
    if (i >= s.size()) return false;
    unsigned long long acc = 0;
    bool lastDigit = false;
    for (; i < s.size(); ++i) {
        const char c = s[i];
        if (c == '_') {
            if (!lastDigit) return false;
            lastDigit = false;
            continue;
        }
        if (c < '0' || c > '9') return false;
        if (acc > (9223372036854775807ULL - static_cast<unsigned>(c - '0')) / 10) return false;
        acc = acc * 10 + static_cast<unsigned>(c - '0');
        lastDigit = true;
    }
    if (!lastDigit) return false;
    out = negative ? -static_cast<long long>(acc) : static_cast<long long>(acc);
    return true;
}

std::string pyStrip(const std::string& s) {
    const std::size_t start = leadingSpaceEnd(s);
    return s.substr(start, trailingSpaceStart(s, start) - start);
}

std::string pyLstrip(const std::string& s) { return s.substr(leadingSpaceEnd(s)); }

bool pyIsSpace(const std::string& s) { return !s.empty() && leadingSpaceEnd(s) == s.size(); }

std::string percentEncode(const std::string& s) {
    static const char* hex = "0123456789ABCDEF";
    std::string out;
    for (const char ch : s) {
        const auto c = static_cast<unsigned char>(ch);
        if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-' ||
            c == '.' || c == '_' || c == '~') {
            out += ch;
        } else {
            out += '%';
            out += hex[c >> 4];
            out += hex[c & 0xF];
        }
    }
    return out;
}

std::string utf8Replace(const std::string& bytes) {
    static const std::string kReplacement = "\xEF\xBF\xBD";
    std::string out;
    out.reserve(bytes.size());
    std::size_t i = 0;
    const auto b = [&](std::size_t k) { return static_cast<unsigned char>(bytes[k]); };
    while (i < bytes.size()) {
        const unsigned char c = b(i);
        if (c < 0x80) {
            out += static_cast<char>(c);
            ++i;
            continue;
        }
        std::size_t need = 0;
        unsigned char lo = 0x80, hi = 0xBF;
        if (c >= 0xC2 && c <= 0xDF) need = 1;
        else if (c == 0xE0) { need = 2; lo = 0xA0; }
        else if (c >= 0xE1 && c <= 0xEC) need = 2;
        else if (c == 0xED) { need = 2; hi = 0x9F; }
        else if (c >= 0xEE && c <= 0xEF) need = 2;
        else if (c == 0xF0) { need = 3; lo = 0x90; }
        else if (c >= 0xF1 && c <= 0xF3) need = 3;
        else if (c == 0xF4) { need = 3; hi = 0x8F; }
        if (need == 0) {
            out += kReplacement;
            ++i;
            continue;
        }
        // Each maximal valid prefix of a broken sequence becomes one U+FFFD.
        std::size_t k = 1;
        while (k <= need && i + k < bytes.size()) {
            const unsigned char cc = b(i + k);
            const unsigned char l = k == 1 ? lo : 0x80, h = k == 1 ? hi : 0xBF;
            if (cc < l || cc > h) break;
            ++k;
        }
        if (k == need + 1) {
            out.append(bytes, i, k);
        } else {
            out += kReplacement;
        }
        i += k;
    }
    return out;
}

json parseAnswer(const HttpResponse& response, const std::string& provider, const std::string& where) {
    json parsed = json::parse(response.body, nullptr, false);
    if (parsed.is_discarded()) {
        throw MailboxError(provider + " answered " + where +
                           " with a body that is not JSON, so it cannot be read. Retry, and "
                           "report it at https://github.com/amd/gaia/issues if it repeats.");
    }
    return parsed;
}

}  // namespace detail

std::string queryString(const std::vector<std::pair<std::string, std::string>>& params) {
    std::string out;
    for (const auto& [key, value] : params) {
        out += out.empty() ? '?' : '&';
        out += detail::percentEncode(key);
        out += '=';
        out += detail::percentEncode(value);
    }
    return out;
}

#ifdef CPPHTTPLIB_OPENSSL_SUPPORT

namespace {

constexpr int kTimeoutSec = 15;
// Gmail fetches up to five messages at once; one kept client each keeps its connection warm.
constexpr std::size_t kIdleClients = 5;

// Refused before sending: the mail code only ever talks to the providers over https.
// Returns the host, for error messages.
std::string requireHttps(const std::string& url) {
    const std::string scheme = "https://";
    if (url.compare(0, scheme.size(), scheme) != 0) {
        throw std::invalid_argument("The mailbox transport only sends https:// requests, not " + url);
    }
    const std::size_t slash = url.find('/', scheme.size());
    const std::string authority =
        url.substr(scheme.size(), slash == std::string::npos ? std::string::npos : slash - scheme.size());
    const std::size_t colon = authority.rfind(':');
    if (colon != std::string::npos) {
        const std::string port = authority.substr(colon + 1);
        if (port.empty() || port.find_first_not_of("0123456789") != std::string::npos || port.size() > 5 ||
            std::stoi(port) < 1 || std::stoi(port) > 65535) {
            throw std::invalid_argument("The mailbox transport got a URL with a bad port: " + url);
        }
    }
    const std::string host = authority.substr(0, colon);
    if (host.empty()) throw std::invalid_argument("The mailbox transport got a URL with no host: " + url);
    return host;
}

// HttpClient words a lost connection for developers and names the full URL, search
// terms included. The backends add their own remedy, so this names the host only.
std::string describe(const HttpError& e, const std::string& host) {
    const std::string what = e.what();
    const auto has = [&](const char* s) { return what.find(s) != std::string::npos; };
    const std::string secs = std::to_string(kTimeoutSec);
    if (has("could not connect")) {
        return "could not connect to " + host + " (no network, host unreachable, or no answer within " + secs + " s)";
    }
    if (has("read timeout")) {
        return "no complete answer from " + host + " within " + secs + " s (connection dropped or too slow)";
    }
    if (has("could not be sent")) {
        return "the request to " + host + " could not be sent (connection closed while writing)";
    }
    if (has("TLS handshake failed")) return "the TLS handshake with " + host + " failed";
    if (has("TLS certificates could not be loaded")) return "the system's TLS certificates could not be loaded";
    if (has("certificate verification failed")) return "the TLS certificate of " + host + " could not be verified";
    return "transport error talking to " + host;
}

// The failure as httpx names it, read off HttpClient's wording; a request that
// never connected certainly changed nothing.
TransportError transportError(const HttpError& e, const std::string& host) {
    const std::string what = e.what();
    const auto has = [&](const char* s) { return what.find(s) != std::string::npos; };
    const bool unconnected = has("could not connect") || has("TLS ");
    const char* kind = unconnected                 ? "ConnectError"
                       : has("read timeout")       ? "ReadTimeout"
                       : has("could not be sent")  ? "WriteError"
                                                   : "TransportError";
    return TransportError(kind, unconnected, describe(e, host));
}

class ClientPool {
public:
    explicit ClientPool(HttpClientConfig config) : config_(std::move(config)) {}

    std::unique_ptr<HttpClient> take() {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (!idle_.empty()) {
                auto client = std::move(idle_.back());
                idle_.pop_back();
                return client;
            }
        }
        return std::make_unique<HttpClient>(config_);
    }

    void give(std::unique_ptr<HttpClient> client) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (idle_.size() < kIdleClients) idle_.push_back(std::move(client));
    }

private:
    const HttpClientConfig config_;
    std::mutex mutex_;
    std::vector<std::unique_ptr<HttpClient>> idle_;
};

}  // namespace

Transport httpsTransport() {
    HttpClientConfig config;
    config.timeoutSec = kTimeoutSec;
    config.connectTimeoutSec = kTimeoutSec;
    config.returnErrorStatuses = true;
    auto pool = std::make_shared<ClientPool>(config);
    return [pool](const HttpCall& call) -> HttpResponse {
        const std::string host = requireHttps(call.url);
        if (call.method != "GET" && call.method != "POST" && call.method != "PUT" && call.method != "PATCH" &&
            call.method != "DELETE") {
            throw std::invalid_argument("The mailbox transport sends GET, POST, PUT, PATCH and DELETE only, not " +
                                        call.method);
        }
        auto client = pool->take();
        HttpResponse response;
        try {
            response = call.method == "GET"     ? client->get(call.url, call.headers)
                       : call.method == "PUT"   ? client->put(call.url, call.body, call.headers)
                       : call.method == "PATCH" ? client->patch(call.url, call.body, call.headers)
                       : call.method == "DELETE" ? client->del(call.url, call.headers)
                                                 : client->post(call.url, call.body, call.headers);
        } catch (const HttpError& e) {
            throw transportError(e, host);
        }
        pool->give(std::move(client));
        return response;
    };
}

#else

Transport httpsTransport() {
    throw MailboxError(
        "This build of GAIA has no HTTPS support, so it cannot reach Gmail or Outlook. Rebuild "
        "gaia_core with OpenSSL installed (CMake finds it on its own), or use the Python GAIA agent.");
}

#endif

}  // namespace gaia::mail
