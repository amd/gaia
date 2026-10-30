// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// fetch_webpage and search_web. HTTPS comes from the OS so the binary carries
// no TLS library: WinHTTP on Windows, the system curl elsewhere.

#include "gaia/flagship_tools.h"

#include <algorithm>
#include <cctype>
#include <cstring>
#include <regex>
#include <set>
#include <sstream>
#include <stdexcept>

#include "gaia/flagship_config.h"
#include "gaia/process.h"
#include "gaia/tool_registry.h"

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <winhttp.h>
#else
#include <arpa/inet.h>
#include <netdb.h>
#include <netinet/in.h>
#include <sys/socket.h>
#endif

namespace gaia {

namespace {

constexpr std::size_t kMaxBody = 5u * 1024u * 1024u;
constexpr std::size_t kPageChars = 8000;
const char* const kUserAgent =
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36 GAIA";

std::string lower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return s;
}

std::string trim(std::string s) {
    s.erase(0, s.find_first_not_of(" \t\r\n"));
    s.erase(s.find_last_not_of(" \t\r\n") + 1);
    return s;
}

json error(const std::string& message) { return {{"status", "error"}, {"error", message}}; }

struct UrlParts {
    std::string scheme, host, rest;  // rest = path + query
    int port = 0;
};

bool parseUrl(const std::string& url, UrlParts& out) {
    static const std::regex re(R"(^(https?)://(\[[^\]]+\]|[^/:?#]+)(?::(\d+))?([^#]*))", std::regex::icase);
    std::smatch m;
    if (!std::regex_search(url, m, re)) return false;
    // user:pass@host would be screened as one host and fetched from another.
    const auto authStart = url.find("://") + 3;
    const auto authEnd = url.find_first_of("/?#", authStart);
    if (url.substr(authStart, authEnd == std::string::npos ? std::string::npos : authEnd - authStart).find('@') !=
        std::string::npos) {
        return false;
    }
    out.scheme = lower(m.str(1));
    out.host = m.str(2);
    if (out.host.size() > 1 && out.host.front() == '[') out.host = out.host.substr(1, out.host.size() - 2);
    out.port = m[3].matched ? std::stoi(m.str(3)) : (out.scheme == "https" ? 443 : 80);
    out.rest = m.str(4).empty() ? "/" : m.str(4);
    return true;
}

std::string resolveRedirect(const std::string& base, const std::string& location) {
    if (location.rfind("http://", 0) == 0 || location.rfind("https://", 0) == 0) return location;
    UrlParts b;
    if (!parseUrl(base, b)) return location;
    const std::string origin = b.scheme + "://" + b.host + ((b.port == 80 || b.port == 443) ? "" : ":" + std::to_string(b.port));
    if (location.rfind("//", 0) == 0) return b.scheme + ":" + location;
    if (!location.empty() && location[0] == '/') return origin + location;
    std::string dir = b.rest.substr(0, b.rest.find('?'));
    dir = dir.substr(0, dir.rfind('/') + 1);
    return origin + dir + location;
}

std::string urlEncode(const std::string& s) {
    std::string out;
    char buf[4];
    for (unsigned char c : s) {
        if (std::isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~') {
            out.push_back(static_cast<char>(c));
        } else if (c == ' ') {
            out.push_back('+');
        } else {
            std::snprintf(buf, sizeof(buf), "%%%02X", c);
            out += buf;
        }
    }
    return out;
}

std::string urlDecode(const std::string& s) {
    std::string out;
    for (std::size_t i = 0; i < s.size(); ++i) {
        if (s[i] == '%' && i + 2 < s.size() && std::isxdigit(static_cast<unsigned char>(s[i + 1])) &&
            std::isxdigit(static_cast<unsigned char>(s[i + 2]))) {
            out.push_back(static_cast<char>(std::stoi(s.substr(i + 1, 2), nullptr, 16)));
            i += 2;
        } else {
            out.push_back(s[i] == '+' ? ' ' : s[i]);
        }
    }
    return out;
}

void appendUtf8(std::string& out, unsigned long cp) {
    if (cp == 0 || (cp >= 0xD800 && cp <= 0xDFFF) || cp >= 0x110000) cp = 0xFFFD;  // not a character
    if (cp < 0x80) {
        out.push_back(static_cast<char>(cp));
    } else if (cp < 0x800) {
        out.push_back(static_cast<char>(0xC0 | (cp >> 6)));
        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    } else if (cp < 0x10000) {
        out.push_back(static_cast<char>(0xE0 | (cp >> 12)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    } else {
        out.push_back(static_cast<char>(0xF0 | (cp >> 18)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 12) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    }
}

std::string decodeEntities(const std::string& s) {
    static const std::vector<std::pair<std::string, std::string>> named = {
        {"amp", "&"}, {"lt", "<"}, {"gt", ">"}, {"quot", "\""}, {"apos", "'"}, {"nbsp", " "},
        {"mdash", "\xE2\x80\x94"}, {"ndash", "\xE2\x80\x93"}, {"hellip", "\xE2\x80\xA6"},
        {"rsquo", "\xE2\x80\x99"}, {"lsquo", "\xE2\x80\x98"}, {"rdquo", "\xE2\x80\x9D"},
        {"ldquo", "\xE2\x80\x9C"}, {"copy", "\xC2\xA9"}, {"reg", "\xC2\xAE"}, {"trade", "\xE2\x84\xA2"}};
    std::string out;
    out.reserve(s.size());
    for (std::size_t i = 0; i < s.size(); ++i) {
        if (s[i] != '&') {
            out.push_back(s[i]);
            continue;
        }
        const std::size_t semi = s.find(';', i);
        if (semi == std::string::npos || semi - i > 10) {
            out.push_back('&');
            continue;
        }
        const std::string ent = s.substr(i + 1, semi - i - 1);
        bool done = false;
        if (!ent.empty() && ent[0] == '#') {
            try {
                const unsigned long cp = (ent.size() > 1 && (ent[1] == 'x' || ent[1] == 'X'))
                                             ? std::stoul(ent.substr(2), nullptr, 16)
                                             : std::stoul(ent.substr(1));
                appendUtf8(out, cp);
                done = true;
            } catch (const std::exception&) {
            }
        } else {
            for (const auto& [k, v] : named) {
                if (k == ent) {
                    out += v;
                    done = true;
                    break;
                }
            }
        }
        if (done) i = semi;
        else out.push_back('&');
    }
    return out;
}

std::string stripTags(const std::string& html) {
    std::string out;
    bool inTag = false;
    for (char c : html) {
        if (c == '<') inTag = true;
        else if (c == '>') inTag = false;
        else if (!inTag) out.push_back(c);
    }
    return trim(decodeEntities(out));
}

std::string titleOf(const std::string& html) {
    const std::string low = lower(html.substr(0, 512 * 1024));
    const auto open = low.find("<title");
    if (open == std::string::npos) return "";
    const auto gt = low.find('>', open);
    const auto close = gt == std::string::npos ? gt : low.find("</title>", gt);
    if (close == std::string::npos) return "";
    return stripTags(html.substr(gt + 1, close - gt - 1));
}

// Each <a ...>inner</a>: the opening tag's attributes and the inner HTML, found
// without a regex (pages run to megabytes).
std::vector<std::pair<std::string, std::string>> anchorsOf(const std::string& html) {
    std::vector<std::pair<std::string, std::string>> out;
    const std::string low = lower(html);
    for (std::size_t i = low.find("<a"); i != std::string::npos && out.size() < 5000; i = low.find("<a", i + 2)) {
        if (i + 2 >= low.size() || !(std::isspace(static_cast<unsigned char>(low[i + 2])) || low[i + 2] == '>')) continue;
        const auto gt = low.find('>', i);
        if (gt == std::string::npos) break;
        const auto close = low.find("</a>", gt);
        if (close == std::string::npos) break;
        out.emplace_back(html.substr(i + 2, gt - i - 2), html.substr(gt + 1, close - gt - 1));
        i = close;
    }
    return out;
}

// The value of attribute @p name in a tag's attribute text, or "".
std::string attrOf(const std::string& attrs, const std::string& name) {
    const std::string low = lower(attrs);
    for (std::size_t at = low.find(name); at != std::string::npos; at = low.find(name, at + 1)) {
        if (at > 0 && !std::isspace(static_cast<unsigned char>(low[at - 1]))) continue;
        std::size_t k = at + name.size();
        while (k < low.size() && std::isspace(static_cast<unsigned char>(low[k]))) ++k;
        if (k >= low.size() || low[k] != '=') continue;
        ++k;
        while (k < low.size() && std::isspace(static_cast<unsigned char>(low[k]))) ++k;
        if (k >= low.size()) return "";
        const char q = attrs[k];
        if (q == '"' || q == '\'') {
            const auto end = attrs.find(q, k + 1);
            return end == std::string::npos ? "" : attrs.substr(k + 1, end - k - 1);
        }
        const auto end = attrs.find_first_of(" \t\r\n>", k);
        return attrs.substr(k, end == std::string::npos ? std::string::npos : end - k);
    }
    return "";
}

#ifdef _WIN32
std::wstring widen(const std::string& s) {
    if (s.empty()) return {};
    int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), nullptr, 0);
    std::wstring w(static_cast<std::size_t>(n), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), &w[0], n);
    return w;
}

std::string narrow(const std::wstring& w) {
    if (w.empty()) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, w.data(), static_cast<int>(w.size()), nullptr, 0, nullptr, nullptr);
    std::string s(static_cast<std::size_t>(n), '\0');
    WideCharToMultiByte(CP_UTF8, 0, w.data(), static_cast<int>(w.size()), &s[0], n, nullptr, nullptr);
    return s;
}

struct WinHttpHandle {
    HINTERNET h = nullptr;
    ~WinHttpHandle() {
        if (h) WinHttpCloseHandle(h);
    }
};

std::wstring queryHeader(HINTERNET request, DWORD which) {
    DWORD size = 0;
    WinHttpQueryHeaders(request, which, WINHTTP_HEADER_NAME_BY_INDEX, WINHTTP_NO_OUTPUT_BUFFER, &size,
                        WINHTTP_NO_HEADER_INDEX);
    if (GetLastError() != ERROR_INSUFFICIENT_BUFFER || size == 0) return L"";
    std::wstring value(size / sizeof(wchar_t), L'\0');
    if (!WinHttpQueryHeaders(request, which, WINHTTP_HEADER_NAME_BY_INDEX, &value[0], &size, WINHTTP_NO_HEADER_INDEX)) {
        return L"";
    }
    value.resize(size / sizeof(wchar_t));
    return value;
}
#endif

bool privateV4(unsigned b0, unsigned b1, unsigned b2) {
    return b0 == 0 || b0 == 10 || b0 == 127 || b0 >= 224 ||  // this network, private, loopback, multicast/reserved
           (b0 == 100 && b1 >= 64 && b1 <= 127) ||           // carrier-grade NAT
           (b0 == 169 && b1 == 254) || (b0 == 172 && b1 >= 16 && b1 <= 31) || (b0 == 192 && b1 == 168) ||
           (b0 == 192 && b1 == 0 && (b2 == 0 || b2 == 2)) || (b0 == 198 && (b1 == 18 || b1 == 19)) ||
           (b0 == 198 && b1 == 51 && b2 == 100) || (b0 == 203 && b1 == 0 && b2 == 113);
}

bool privateAddress(const sockaddr* sa) {
    if (sa->sa_family == AF_INET) {
        const unsigned long ip = ntohl(reinterpret_cast<const sockaddr_in*>(sa)->sin_addr.s_addr);
        return privateV4(ip >> 24, (ip >> 16) & 0xFF, (ip >> 8) & 0xFF);
    }
    if (sa->sa_family != AF_INET6) return true;  // nothing else can be vetted
    const unsigned char* b = reinterpret_cast<const sockaddr_in6*>(sa)->sin6_addr.s6_addr;
    static const unsigned char compat[12] = {};
    static const unsigned char mapped[12] = {0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0xFF, 0xFF};
    static const unsigned char nat64[12] = {0, 0x64, 0xFF, 0x9B, 0, 0, 0, 0, 0, 0, 0, 0};
    // ::a.b.c.d (and :: / ::1), ::ffff:a.b.c.d, 64:ff9b::a.b.c.d and 6to4 carry an IPv4 address: judge that.
    if (std::memcmp(b, compat, 12) == 0 || std::memcmp(b, mapped, 12) == 0 || std::memcmp(b, nat64, 12) == 0) {
        return privateV4(b[12], b[13], b[14]);
    }
    if (b[0] == 0x20 && b[1] == 0x02) return privateV4(b[2], b[3], b[4]);
    // fc00::/7 unique-local, ff00::/8 multicast, fe80::/10 link-local and fec0::/10 site-local.
    return (b[0] & 0xFE) == 0xFC || b[0] == 0xFF || (b[0] == 0xFE && (b[1] & 0x80) == 0x80);
}

void ensureWinsock() {
#ifdef _WIN32
    static const bool ok = [] {
        WSADATA d;
        return WSAStartup(MAKEWORD(2, 2), &d) == 0;
    }();
    if (!ok) throw std::runtime_error("Windows sockets could not start");
#endif
}

bool localName(const std::string& h) {
    return h.empty() || h == "localhost" || (h.size() > 10 && h.compare(h.size() - 10, 10, ".localhost") == 0);
}

#ifndef _WIN32
bool ipLiteral(const std::string& host) {
    unsigned char buf[16];
    return inet_pton(AF_INET, host.c_str(), buf) == 1 || inet_pton(AF_INET6, host.c_str(), buf) == 1;
}
#endif

struct Resolved {
    std::vector<std::string> ips;
    bool anyPrivate = false;
};

// Every address @p host resolves to. @throws std::runtime_error when it does not resolve.
Resolved resolveHost(const std::string& host) {
    ensureWinsock();
    addrinfo hints{};
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    addrinfo* res = nullptr;
    if (getaddrinfo(host.c_str(), nullptr, &hints, &res) != 0 || !res) {
        throw std::runtime_error(host + " does not resolve");
    }
    Resolved out;
    for (addrinfo* ai = res; ai; ai = ai->ai_next) {
        out.anyPrivate = out.anyPrivate || privateAddress(ai->ai_addr);
        char text[INET6_ADDRSTRLEN] = {};
        const void* addr = ai->ai_family == AF_INET
                               ? static_cast<const void*>(&reinterpret_cast<const sockaddr_in*>(ai->ai_addr)->sin_addr)
                               : static_cast<const void*>(&reinterpret_cast<const sockaddr_in6*>(ai->ai_addr)->sin6_addr);
        if ((ai->ai_family == AF_INET || ai->ai_family == AF_INET6) && inet_ntop(ai->ai_family, addr, text, sizeof(text))) {
            out.ips.push_back(text);
        }
    }
    freeaddrinfo(res);
    if (out.ips.empty()) throw std::runtime_error(host + " has no IPv4 or IPv6 address");
    return out;
}

#ifndef _WIN32
std::string shellQuoted(const std::string& s) {
    std::string out = "'";
    for (char c : s) out += (c == '\'') ? std::string("'\\''") : std::string(1, c);
    return out + "'";
}
#endif

#ifdef _WIN32
// The address that actually answered must be public: the name may have been
// re-pointed (DNS rebinding) between our check and WinHTTP's own lookup.
void verifyPeer(HINTERNET request, const std::string& host) {
    WINHTTP_PROXY_RESULT_ENTRY proxy{};
    DWORD proxyLen = sizeof(proxy);
    // Through a proxy the peer is the proxy, which resolved the name itself.
    if (WinHttpQueryOption(request, WINHTTP_OPTION_PROXY_RESULT_ENTRY, &proxy, &proxyLen) && proxy.fProxy) return;
    WINHTTP_CONNECTION_INFO info{};
    info.cbSize = sizeof(info);
    DWORD len = sizeof(info);
    if (!WinHttpQueryOption(request, WINHTTP_OPTION_CONNECTION_INFO, &info, &len)) {
        throw std::runtime_error("cannot confirm which address answered for " + host + " (WinHTTP error " +
                                 std::to_string(GetLastError()) + "); the response was not read");
    }
    if (privateAddress(reinterpret_cast<const sockaddr*>(&info.RemoteAddress))) {
        throw std::runtime_error(host + " answered from a local or private address (its DNS changed after it was "
                                 "checked); the response was not read");
    }
}
#endif

}  // namespace

std::size_t parseHttpHeaderBlocks(const std::string& raw, HttpFetch& out) {
    std::size_t pos = 0;
    while (raw.compare(pos, 5, "HTTP/") == 0) {
        std::size_t end = raw.find("\r\n\r\n", pos);
        const std::size_t next = end == std::string::npos ? raw.size() : end + 4;
        if (end == std::string::npos) end = raw.size();
        std::istringstream hs(raw.substr(pos, end - pos));
        std::string line;
        std::getline(hs, line);
        std::istringstream sl(line);
        std::string proto;
        int status = 0;
        sl >> proto >> status;
        out.status = status;
        out.contentType.clear();
        out.location.clear();
        bool framed = false;
        while (std::getline(hs, line)) {
            const auto colon = line.find(':');
            if (colon == std::string::npos) continue;
            const std::string key = lower(trim(line.substr(0, colon)));
            const std::string val = trim(line.substr(colon + 1));
            if (key == "content-type") out.contentType = val;
            if (key == "location") out.location = val;
            if (key == "content-length" || key == "transfer-encoding") framed = true;
        }
        pos = next;
        // 1xx blocks precede the real answer; so does a proxy's bodiless "200 Connection established".
        const bool interim = status >= 100 && status < 200;
        const bool tunnel = status >= 200 && status < 300 && !framed && out.contentType.empty() &&
                            raw.compare(pos, 5, "HTTP/") == 0;
        if (!interim && !tunnel) break;
    }
    return pos;
}

HttpFetch platformHttpGet(const std::string& url, bool allowPrivate) {
    UrlParts u;
    if (!parseUrl(url, u)) throw std::runtime_error("not an http(s) URL: " + url);
    Resolved vetted;
    if (!allowPrivate) {
        if (localName(lower(u.host))) throw std::runtime_error(u.host + " is a local address");
        vetted = resolveHost(u.host);
        if (vetted.anyPrivate) throw std::runtime_error(u.host + " resolves to a local or private address");
    }
    HttpFetch out;
#ifdef _WIN32
    WinHttpHandle session{WinHttpOpen(widen(kUserAgent).c_str(), WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY,
                                      WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0)};
    if (!session.h) {
        throw std::runtime_error("WinHTTP could not start (error " + std::to_string(GetLastError()) + ")");
    }
    WinHttpSetTimeouts(session.h, 10000, 10000, 20000, 20000);
    WinHttpHandle connect{WinHttpConnect(session.h, widen(u.host).c_str(), static_cast<INTERNET_PORT>(u.port), 0)};
    if (!connect.h) throw std::runtime_error("cannot connect to " + u.host + " (error " + std::to_string(GetLastError()) + ")");
    WinHttpHandle request{WinHttpOpenRequest(connect.h, L"GET", widen(u.rest).c_str(), nullptr, WINHTTP_NO_REFERER,
                                             WINHTTP_DEFAULT_ACCEPT_TYPES,
                                             u.scheme == "https" ? WINHTTP_FLAG_SECURE : 0)};
    if (!request.h) throw std::runtime_error("cannot open a request to " + url);
    // Redirects are followed by the caller, which screens every hop.
    DWORD noRedirect = WINHTTP_DISABLE_REDIRECTS;
    if (!WinHttpSetOption(request.h, WINHTTP_OPTION_DISABLE_FEATURE, &noRedirect, sizeof(noRedirect))) {
        throw std::runtime_error("cannot turn off WinHTTP's own redirects (error " + std::to_string(GetLastError()) +
                                 "); the request was not sent");
    }
    const std::wstring headers = L"Accept-Language: en-US,en;q=0.8\r\n";
    if (!WinHttpSendRequest(request.h, headers.c_str(), static_cast<DWORD>(-1L), WINHTTP_NO_REQUEST_DATA, 0, 0, 0) ||
        !WinHttpReceiveResponse(request.h, nullptr)) {
        throw std::runtime_error("request to " + u.host + " failed (WinHTTP error " + std::to_string(GetLastError()) + ")");
    }
    if (!allowPrivate) verifyPeer(request.h, u.host);
    DWORD status = 0, size = sizeof(status);
    WinHttpQueryHeaders(request.h, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER, WINHTTP_HEADER_NAME_BY_INDEX,
                        &status, &size, WINHTTP_NO_HEADER_INDEX);
    out.status = static_cast<int>(status);
    out.contentType = narrow(queryHeader(request.h, WINHTTP_QUERY_CONTENT_TYPE));
    out.location = narrow(queryHeader(request.h, WINHTTP_QUERY_LOCATION));
    for (;;) {
        DWORD avail = 0;
        if (!WinHttpQueryDataAvailable(request.h, &avail) || avail == 0) break;
        std::string chunk(avail, '\0');
        DWORD read = 0;
        if (!WinHttpReadData(request.h, &chunk[0], avail, &read) || read == 0) break;
        out.body.append(chunk, 0, read);
        if (out.body.size() > kMaxBody) break;
    }
#else
    const std::string curl = findExecutable("curl");
    if (curl.empty()) {
        throw std::runtime_error(
            "web access needs curl, which is not installed. Install curl (it ships with macOS and most "
            "Linux distributions) and try again.");
    }
    // Pin the vetted address so curl cannot resolve the name a second time to somewhere else.
    std::string pin;
    if (!vetted.ips.empty() && !ipLiteral(u.host)) {
        const std::string ip = vetted.ips.front();
        pin = " --resolve " +
              shellQuoted(u.host + ":" + std::to_string(u.port) + ":" + (ip.find(':') != std::string::npos ? "[" + ip + "]" : ip));
    }
    const std::string command = shellQuoted(curl) + " -sS --max-time 25 --proto =http,https --max-filesize " +
                                std::to_string(kMaxBody) + pin + " -A " + shellQuoted(kUserAgent) + " -D - " +
                                shellQuoted(url);
    ProcessResult r = ProcessRunner::run(command, 30000, "", {}, kMaxBody + 65536);
    if (r.exitCode != 0) {
        throw std::runtime_error("curl could not fetch " + url + ": " + trim(r.stderr_output));
    }
    // -D - writes the headers first: interim and proxy blocks, then the response's own.
    out.body = r.stdout_output.substr(parseHttpHeaderBlocks(r.stdout_output, out));
#endif
    return out;
}

std::string htmlToText(const std::string& html) {
    std::string s = html;
    // Drop whole elements that carry no readable text.
    for (const char* tag : {"script", "style", "noscript", "svg", "head", "template"}) {
        const std::string open = std::string("<") + tag, close = std::string("</") + tag;
        std::string ls = lower(s);
        std::string out;
        std::size_t pos = 0;
        for (;;) {
            std::size_t a = ls.find(open, pos);
            if (a == std::string::npos) break;
            const char after = a + open.size() < ls.size() ? ls[a + open.size()] : '>';
            if (!(after == '>' || after == ' ' || after == '\t' || after == '\n' || after == '/')) {
                out.append(s, pos, a + open.size() - pos);
                pos = a + open.size();
                continue;
            }
            std::size_t b = ls.find(close, a);
            std::size_t end = b == std::string::npos ? ls.size() : ls.find('>', b);
            end = end == std::string::npos ? ls.size() : end + 1;
            out.append(s, pos, a - pos);
            pos = end;
        }
        out.append(s, pos, std::string::npos);
        s = out;
    }
    std::string text;
    text.reserve(s.size() / 2);
    for (std::size_t i = 0; i < s.size(); ++i) {
        if (s[i] != '<') {
            text.push_back(s[i]);
            continue;
        }
        const std::size_t end = s.find('>', i);
        if (end == std::string::npos) break;
        std::string tag = lower(s.substr(i + 1, std::min<std::size_t>(end - i - 1, 12)));
        if (!tag.empty() && tag[0] == '/') tag = tag.substr(1);
        const std::string name = tag.substr(0, tag.find_first_of(" \t\n/>"));
        static const char* blocks[] = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
                                       "section", "article", "header", "footer", "table", "ul", "ol", "pre", "blockquote"};
        for (const char* b : blocks) {
            if (name == b) {
                text.push_back('\n');
                if (name == "li") text += "- ";
                break;
            }
        }
        if (name == "td" || name == "th") text.push_back(' ');
        i = end;
    }
    text = decodeEntities(text);
    // Collapse runs of spaces; keep at most one blank line.
    std::string out;
    out.reserve(text.size());
    int newlines = 0;
    bool space = false;
    for (char c : text) {
        if (c == '\r') continue;
        if (c == '\n') {
            ++newlines;
            space = false;
            continue;
        }
        if (c == ' ' || c == '\t' || c == '\f' || c == '\v') {
            space = true;
            continue;
        }
        if (newlines > 0) {
            if (!out.empty()) out += newlines > 1 ? "\n\n" : "\n";
            newlines = 0;
        } else if (space && !out.empty()) {
            out.push_back(' ');
        }
        space = false;
        out.push_back(c);
    }
    return out;
}

bool isPrivateHost(const std::string& host) {
    const std::string h = lower(trim(host));
    if (localName(h)) return true;
    try {
        return resolveHost(h).anyPrivate;
    } catch (const std::runtime_error&) {
        return true;  // unvetted: the transport might resolve it differently
    }
}

json FlagshipTools::fetchFollowing(const std::string& start, HttpFetch& out) {
    std::string url = start;
    for (int hop = 0; hop <= 5; ++hop) {
        UrlParts u;
        if (!parseUrl(url, u)) return error("Only http and https URLs can be fetched: " + url);
        if (!allowPrivate_ && isPrivateHost(u.host)) {
            return error("Not fetched: " + u.host +
                         " is a local or private address, or does not resolve. fetch_webpage only reads public web pages.");
        }
        try {
            out = httpGet_(url);
        } catch (const std::exception& e) {
            return error(std::string("Could not fetch ") + url + ": " + e.what());
        }
        if (out.status >= 300 && out.status < 400 && !out.location.empty()) {
            url = resolveRedirect(url, out.location);
            continue;
        }
        return {{"status", "success"}, {"url", url}};
    }
    return error("Too many redirects starting at " + start);
}

json FlagshipTools::fetchWebpage(const json& args) {
    const std::string url = trim(args.value("url", ""));
    if (url.empty()) return error("url is required");
    bool extract = true;
    if (args.contains("extract_text") && args["extract_text"].is_boolean()) extract = args["extract_text"].get<bool>();
    HttpFetch r;
    json res = fetchFollowing(url, r);
    if (res.value("status", "") != "success") return res;
    const std::string finalUrl = res.value("url", url);
    if (r.status >= 400) {
        return error("HTTP " + std::to_string(r.status) + " from " + finalUrl + "; the page could not be read.");
    }
    r.body = toValidUtf8(r.body);  // a page in another encoding must not break the reply
    const std::string ctype = lower(r.contentType);
    const bool html = ctype.find("html") != std::string::npos || ctype.empty();
    std::string content = (extract && html) ? htmlToText(r.body) : r.body;
    const std::size_t total = content.size();
    bool truncated = false;
    if (content.size() > kPageChars) {
        std::size_t cut = kPageChars;
        while (cut > 0 && (static_cast<unsigned char>(content[cut]) & 0xC0) == 0x80) --cut;
        content.resize(cut);
        truncated = true;
    }
    json out = {{"status", "success"}, {"url", finalUrl}, {"http_status", r.status},
                {"content_type", r.contentType}, {extract && html ? "content" : "html", content},
                {"truncated", truncated}, {"total_chars", total}};
    if (html) {
        const std::string title = titleOf(r.body);
        if (!title.empty()) out["title"] = title;
    }
    return out;
}

json FlagshipTools::fetchPage(const json& args) {
    const std::string url = trim(args.value("url", ""));
    if (url.empty()) return error("url is required");
    const std::string mode = lower(trim(args.contains("extract") && args["extract"].is_string()
                                            ? args["extract"].get<std::string>()
                                            : std::string("text")));
    if (mode != "text" && mode != "html" && mode != "links") {
        return error("extract must be text, html or links" +
                     std::string(mode == "tables" ? " (tables are not supported by this build; use text)" : "") + ".");
    }
    long long maxLength = 5000, offset = 0;
    if (args.contains("max_length") && args["max_length"].is_number()) maxLength = args["max_length"].get<long long>();
    if (args.contains("offset") && args["offset"].is_number()) offset = args["offset"].get<long long>();
    maxLength = std::clamp<long long>(maxLength, 512, 20000);
    offset = std::max<long long>(offset, 0);
    const std::string find = args.contains("find") && args["find"].is_string() ? trim(args["find"].get<std::string>()) : "";

    HttpFetch r;
    json res = fetchFollowing(url, r);
    if (res.value("status", "") != "success") return res;
    const std::string finalUrl = res.value("url", url);
    if (r.status >= 400) return error("HTTP " + std::to_string(r.status) + " from " + finalUrl + ".");
    r.body = toValidUtf8(r.body);

    std::string out;
    if (mode == "links") {
        std::set<std::string> seen;
        for (const auto& [attrs, inner] : anchorsOf(r.body)) {
            std::string href = decodeEntities(attrOf(attrs, "href"));
            if (href.empty() || href[0] == '#' || lower(href).rfind("javascript:", 0) == 0) continue;
            href = resolveRedirect(finalUrl, href);
            if (!seen.insert(href).second) continue;
            out += "- " + stripTags(inner) + " — " + href + "\n";
            if (out.size() > static_cast<std::size_t>(maxLength)) break;
        }
        if (out.empty()) out = "(no links found)";
        return {{"status", "success"}, {"url", finalUrl}, {"content", out}};
    }
    std::string text = mode == "html" ? r.body : htmlToText(r.body);
    if (!find.empty()) {
        // Each hit shows a window of its line, so one minified megabyte line cannot fill the reply.
        constexpr std::size_t kSide = 300;
        const std::string needle = lower(find);
        std::istringstream in(text);
        std::string passages;
        std::size_t hits = 0, shown = 0;
        for (std::string line; std::getline(in, line);) {
            const std::size_t at = lower(line).find(needle);
            if (at == std::string::npos) continue;
            ++hits;
            if (passages.size() >= static_cast<std::size_t>(maxLength)) continue;
            std::size_t from = at > kSide ? at - kSide : 0;
            while (from > 0 && (static_cast<unsigned char>(line[from]) & 0xC0) == 0x80) --from;
            std::size_t to = std::min(line.size(), at + needle.size() + kSide);
            while (to < line.size() && (static_cast<unsigned char>(line[to]) & 0xC0) == 0x80) ++to;
            passages += (from > 0 ? "..." : "") + line.substr(from, to - from) + (to < line.size() ? "..." : "") + "\n\n";
            ++shown;
        }
        json result = {{"status", "success"}, {"url", finalUrl}, {"find", find}, {"matches", hits},
                       {"content", passages.empty() ? "No passage on the page contains \"" + find + "\"." : passages}};
        if (shown < hits) {
            result["truncated"] = true;
            result["content"] = passages + "[" + std::to_string(hits - shown) +
                                " more passage(s) match; raise max_length or use a more specific find to see them]";
        }
        return result;
    }
    std::size_t from = std::min(text.size(), static_cast<std::size_t>(offset));
    while (from > 0 && (static_cast<unsigned char>(text[from]) & 0xC0) == 0x80) --from;
    std::size_t to = std::min(text.size(), from + static_cast<std::size_t>(maxLength));
    while (to < text.size() && to > from && (static_cast<unsigned char>(text[to]) & 0xC0) == 0x80) --to;
    json result = {{"status", "success"}, {"url", finalUrl}, {"content", text.substr(from, to - from)},
                   {"offset", from}, {"total_chars", text.size()}};
    const std::string title = titleOf(r.body);
    if (!title.empty()) result["title"] = title;
    if (to < text.size()) {
        result["next_offset"] = to;
        result["content"] = result["content"].get<std::string>() + "\n[More on this page: call fetch_page again with offset=" +
                            std::to_string(to) + "]";
    }
    return result;
}

json FlagshipTools::searchWeb(const json& args) {
    const std::string query = trim(args.value("query", ""));
    if (query.empty()) return error("query is required");
    long long n = 5;
    if (args.contains("num_results") && args["num_results"].is_number()) n = args["num_results"].get<long long>();
    n = std::clamp<long long>(n, 1, 10);
    HttpFetch r;
    const std::string url = "https://html.duckduckgo.com/html/?q=" + urlEncode(query);
    try {
        r = httpGet_(url);
    } catch (const std::exception& e) {
        return error(std::string("Web search failed: ") + e.what());
    }
    if (r.status != 200) {
        return error("DuckDuckGo answered HTTP " + std::to_string(r.status) +
                     "; it may be rate-limiting this machine. Try again in a minute, or fetch a known URL with fetch_webpage.");
    }
    r.body = toValidUtf8(r.body);
    std::vector<std::string> snippets;
    {
        const std::string low = lower(r.body);
        for (std::size_t at = low.find("result__snippet"); at != std::string::npos;
             at = low.find("result__snippet", at + 1)) {
            const auto gt = low.find('>', at);
            if (gt == std::string::npos) break;
            std::size_t end = std::string::npos;
            for (const char* closer : {"</a>", "</div>", "</td>"}) end = std::min(end, low.find(closer, gt));
            if (end == std::string::npos) break;
            snippets.push_back(stripTags(r.body.substr(gt + 1, end - gt - 1)));
            at = end;
        }
    }
    std::string text = "Web search results for: \"" + query + "\"\n\n";
    long long count = 0;
    json results = json::array();
    for (const auto& [attrs, inner] : anchorsOf(r.body)) {
        if (count >= n) break;
        const std::string cls = attrOf(attrs, "class");
        if ((" " + cls + " ").find(" result__a ") == std::string::npos) continue;
        std::string href = decodeEntities(attrOf(attrs, "href"));
        const auto uddg = href.find("uddg=");
        if (uddg != std::string::npos) {
            href = urlDecode(href.substr(uddg + 5, href.find('&', uddg) - uddg - 5));
        } else if (href.rfind("//", 0) == 0) {
            href = "https:" + href;
        }
        if (href.find("duckduckgo.com/y.js") != std::string::npos) continue;  // ads
        const std::string title = stripTags(inner);
        const std::string snip = static_cast<std::size_t>(count) < snippets.size() ? snippets[static_cast<std::size_t>(count)] : "";
        ++count;
        text += std::to_string(count) + ". " + title + "\n   " + href + "\n";
        if (!snip.empty()) text += "   " + snip + "\n";
        text += "\n";
        results.push_back({{"title", title}, {"url", href}, {"snippet", snip}});
    }
    if (count == 0) {
        return error("DuckDuckGo returned no results for \"" + query +
                     "\" (it may be showing this machine a bot check). Try different words, or fetch a known URL.");
    }
    text += "Use fetch_webpage(url) to read a result.";
    return {{"status", "success"}, {"query", query}, {"results", results}, {"output", text}};
}

}  // namespace gaia
