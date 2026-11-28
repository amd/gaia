// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// The Gmail and Graph read backends, driven through a fake Transport that
// records every call, so each test checks the request the mailbox would get
// (method, path, query, token), not just that a stub answered. The cases follow
// tests/unit/agents/tools/test_gmail_backend.py, test_email_tools.py,
// test_email_thread_tools.py and test_email_attachments.py. The parity cases
// replay inputs whose Python answers fixtures/mail/make_python_parity.py wrote.

#include <gtest/gtest.h>
#include <gaia/mail.h>

#include <atomic>
#include <chrono>
#include <fstream>
#include <functional>
#include <mutex>
#include <optional>
#include <sstream>
#include <string>
#include <thread>
#include <utility>
#include <vector>

using namespace gaia;
using namespace gaia::mail;

namespace {

// ---- fake transport ----

std::string pctDecode(const std::string& s) {
    std::string out;
    for (std::size_t i = 0; i < s.size(); ++i) {
        if (s[i] == '%' && i + 2 < s.size()) {
            out += static_cast<char>(std::stoi(s.substr(i + 1, 2), nullptr, 16));
            i += 2;
        } else {
            out += s[i];
        }
    }
    return out;
}

struct Seen {
    std::string method;
    std::string url;
    std::string rawPath;  // as sent, still percent-encoded
    std::string path;
    std::vector<std::pair<std::string, std::string>> params;
    std::string auth;

    bool has(const std::string& key) const {
        for (const auto& [k, v] : params) {
            if (k == key) return true;
        }
        return false;
    }
    std::string param(const std::string& key) const {
        for (const auto& [k, v] : params) {
            if (k == key) return v;
        }
        return "";
    }
    std::vector<std::string> all(const std::string& key) const {
        std::vector<std::string> out;
        for (const auto& [k, v] : params) {
            if (k == key) out.push_back(v);
        }
        return out;
    }
};

Seen parseCall(const HttpCall& call) {
    Seen s;
    s.method = call.method;
    s.url = call.url;
    const std::size_t hostEnd = call.url.find('/', std::string("https://").size());
    const std::string rest = call.url.substr(hostEnd);
    const std::size_t q = rest.find('?');
    s.rawPath = rest.substr(0, q);
    s.path = pctDecode(s.rawPath);
    if (q != std::string::npos) {
        std::stringstream query(rest.substr(q + 1));
        std::string pair;
        while (std::getline(query, pair, '&')) {
            const std::size_t eq = pair.find('=');
            s.params.emplace_back(pctDecode(pair.substr(0, eq)), pctDecode(pair.substr(eq + 1)));
        }
    }
    const auto it = call.headers.find("Authorization");
    if (it != call.headers.end()) s.auth = it->second;
    return s;
}

class FakeApi {
public:
    using Handler = std::function<HttpResponse(const Seen&)>;
    explicit FakeApi(Handler handler) : handler_(std::move(handler)) {}

    Transport transport() {
        return [this](const HttpCall& call) {
            const Seen seen = parseCall(call);
            {
                std::lock_guard<std::mutex> lock(mutex_);
                seen_.push_back(seen);
            }
            return handler_(seen);
        };
    }

    std::vector<Seen> seen() {
        std::lock_guard<std::mutex> lock(mutex_);
        return seen_;
    }

    std::vector<Seen> where(const std::function<bool(const Seen&)>& pred) {
        std::vector<Seen> out;
        for (const auto& s : seen()) {
            if (pred(s)) out.push_back(s);
        }
        return out;
    }

private:
    Handler handler_;
    std::mutex mutex_;
    std::vector<Seen> seen_;
};

HttpResponse ok(const json& body) {
    HttpResponse r;
    r.status = 200;
    r.body = body.dump();
    return r;
}

HttpResponse answer(int status, const std::string& body = "", HttpHeaders headers = {}) {
    HttpResponse r;
    r.status = status;
    r.body = body;
    r.headers = std::move(headers);
    return r;
}

bool endsWith(const std::string& s, const std::string& tail) {
    return s.size() >= tail.size() && s.compare(s.size() - tail.size(), tail.size(), tail) == 0;
}

bool contains(const std::string& s, const std::string& needle) { return s.find(needle) != std::string::npos; }

std::string lower(std::string s) {
    for (auto& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return s;
}

template <typename E, typename F>
std::optional<E> caught(F&& f) {
    try {
        f();
    } catch (const E& e) {
        return e;
    }
    return std::nullopt;
}

TokenSource fixedToken() {
    return [] { return std::string("test-token"); };
}

// ---- Gmail fixtures ----

std::string b64url(const std::string& in) {
    static const char* table = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    std::string out;
    std::size_t i = 0;
    for (; i + 2 < in.size(); i += 3) {
        const unsigned n = (static_cast<unsigned char>(in[i]) << 16) | (static_cast<unsigned char>(in[i + 1]) << 8) |
                           static_cast<unsigned char>(in[i + 2]);
        out += table[n >> 18];
        out += table[(n >> 12) & 63];
        out += table[(n >> 6) & 63];
        out += table[n & 63];
    }
    if (i + 1 == in.size()) {
        const unsigned n = static_cast<unsigned char>(in[i]) << 16;
        out += table[n >> 18];
        out += table[(n >> 12) & 63];
    } else if (i + 2 == in.size()) {
        const unsigned n = (static_cast<unsigned char>(in[i]) << 16) | (static_cast<unsigned char>(in[i + 1]) << 8);
        out += table[n >> 18];
        out += table[(n >> 12) & 63];
        out += table[(n >> 6) & 63];
    }
    return out;
}

json textPart(const std::string& mime, const std::string& body) {
    return {{"mimeType", mime},
            {"filename", ""},
            {"headers", json::array()},
            {"body", {{"size", body.size()}, {"data", b64url(body)}}}};
}

const std::string kAsk = "Can you confirm the Q3 figures?";
// 2026-09-02T08:15:00Z
const long long kSeptember2 = 1788336900000LL;

json gmailMessage(const std::string& id, const std::string& thread = "", long long date = kSeptember2,
                  json labels = json::array({"INBOX", "UNREAD"}), const std::string& body = kAsk) {
    return {{"id", id},
            {"threadId", thread.empty() ? id : thread},
            {"labelIds", std::move(labels)},
            {"snippet", "Q3 &amp; Q4"},
            {"internalDate", std::to_string(date)},
            {"payload",
             {{"mimeType", "multipart/alternative"},
              {"filename", ""},
              {"headers",
               {{{"name", "From"}, {"value", "\"Ruiz, Dana\" <dana@example.com>"}},
                {{"name", "To"}, {"value", "Me <me@example.com>"}},
                {{"name", "Subject"}, {"value", "Q3 numbers"}}}},
              {"body", {{"size", 0}}},
              {"parts", {textPart("text/plain", body), textPart("text/html", "<p>" + body + "</p>")}}}}};
}

const json kLabels = {{"labels", {{{"id", "INBOX"}, {"name", "INBOX"}}, {{"id", "Label_17"}, {"name", "Receipts"}}}}};

const std::string kGmailBase = "https://gmail.googleapis.com/gmail/v1";

// Serves messages.list, messages.get and labels.list for `ids`.
FakeApi::Handler inboxHandler(std::vector<std::string> ids) {
    return [ids](const Seen& s) {
        if (endsWith(s.path, "/users/me/labels")) return ok(kLabels);
        if (endsWith(s.path, "/users/me/messages")) {
            json stubs = json::array();
            for (const auto& id : ids) stubs.push_back({{"id", id}, {"threadId", id}});
            return ok({{"messages", stubs}});
        }
        return ok(gmailMessage(s.path.substr(s.path.rfind('/') + 1)));
    };
}

std::unique_ptr<ReadBackend> gmail(FakeApi& api, TokenSource token = fixedToken()) {
    return makeGmailReadBackend(std::move(token), api.transport());
}

json gmailError(int status, const std::string& reason, const std::string& help = "") {
    json err = {{"message", "boom"}, {"domain", "global"}, {"reason", reason}};
    if (!help.empty()) err["extendedHelp"] = help;
    return {{"error", {{"code", status}, {"message", "boom"}, {"errors", {err}}}}};
}

// ---- Graph fixtures ----

const std::string kGraphBase = "https://graph.microsoft.com/v1.0";

json graphMessage(const std::string& id = "AAMk-1", const std::string& conv = "conv-1",
                  const std::string& received = "2026-09-02T08:15:00Z") {
    return {{"id", id},
            {"conversationId", conv},
            {"subject", "Q3 numbers"},
            {"from", {{"emailAddress", {{"name", "Dana Ruiz"}, {"address", "dana@example.com"}}}}},
            {"toRecipients", {{{"emailAddress", {{"name", "Me"}, {"address", "me@example.com"}}}}}},
            {"ccRecipients", json::array()},
            {"receivedDateTime", received},
            {"isRead", false},
            {"isDraft", false},
            {"flag", {{"flagStatus", "flagged"}}},
            {"categories", {"Work"}},
            {"bodyPreview", "  " + kAsk + "  "},
            {"body", {{"contentType", "html"}, {"content", "<p>Can you confirm?</p>"}}}};
}

std::unique_ptr<ReadBackend> outlook(FakeApi& api, TokenSource token = fixedToken()) {
    return makeOutlookReadBackend(std::move(token), api.transport());
}

json loadParity() {
    std::ifstream in(std::string(GAIA_TEST_FIXTURES_DIR) + "/mail/python_parity.json");
    EXPECT_TRUE(in.good()) << "missing fixtures/mail/python_parity.json";
    return json::parse(in);
}

}  // namespace

// ===========================================================================
// queryString / transport
// ===========================================================================

TEST(MailHttp, QueryStringEncodesEveryReservedCharacterAndKeepsRepeats) {
    EXPECT_EQ(queryString({}), "");
    EXPECT_EQ(queryString({{"labelIds", "INBOX"}, {"labelIds", "UNREAD"}, {"maxResults", "10"}}),
              "?labelIds=INBOX&labelIds=UNREAD&maxResults=10");
    EXPECT_EQ(queryString({{"$search", "\"a b&c\""}}), "?%24search=%22a%20b%26c%22");
    EXPECT_EQ(queryString({{"q", "caf\xC3\xA9 ~._-"}}), "?q=caf%C3%A9%20~._-");
}

TEST(MailHttp, HttpsTransportRefusesAnythingButHttpsBeforeSending) {
    const Transport transport = httpsTransport();
    EXPECT_THROW(transport({"GET", "http://gmail.googleapis.com/gmail/v1/users/me/profile", {}, ""}),
                 std::invalid_argument);
    EXPECT_THROW(transport({"GET", "https://:443/x", {}, ""}), std::invalid_argument);
    EXPECT_THROW(transport({"GET", "https://host:99999/x", {}, ""}), std::invalid_argument);
}

TEST(MailHttp, HttpsTransportNamesOnlyTheHostWhenNothingAnswers) {
    const Transport transport = httpsTransport();
    try {
        transport({"GET", "https://127.0.0.1:1/gmail/v1/users/me/messages?q=secret%20plans", {}, ""});
        FAIL() << "expected an error";
    } catch (const std::runtime_error& e) {
        const std::string what = e.what();
        EXPECT_NE(what.find("could not connect to 127.0.0.1"), std::string::npos) << what;
        EXPECT_EQ(what.find("secret"), std::string::npos) << what;
        EXPECT_EQ(what.find("HttpClientConfig"), std::string::npos) << what;
    }
}

// ===========================================================================
// Gmail: HTTP layer and get_user_email
// ===========================================================================

TEST(MailGmail, ProfileRequestShape) {
    FakeApi api([](const Seen&) { return ok({{"emailAddress", " me@example.com "}}); });
    EXPECT_EQ(gmail(api)->getUserEmail(), "me@example.com");
    const auto seen = api.seen();
    ASSERT_EQ(seen.size(), 1u);
    EXPECT_EQ(seen[0].method, "GET");
    EXPECT_EQ(seen[0].url, kGmailBase + "/users/me/profile");
    EXPECT_EQ(seen[0].auth, "Bearer test-token");
}

TEST(MailGmail, ProfileWithoutAnAddressFailsLoudly) {
    FakeApi api([](const Seen&) { return ok({{"emailAddress", ""}}); });
    const auto err = caught<MailboxError>([&] { gmail(api)->getUserEmail(); });
    ASSERT_TRUE(err);
    EXPECT_TRUE(contains(err->what(), "unusable state"));
    EXPECT_EQ(err->connect, std::vector<std::string>{"google"});
}

TEST(MailGmail, Status401NeedsReconnecting) {
    FakeApi api([](const Seen&) { return answer(401, gmailError(401, "authError").dump()); });
    const auto err = caught<MailboxAuthError>([&] { gmail(api)->getUserEmail(); });
    ASSERT_TRUE(err);
    EXPECT_TRUE(contains(err->what(), "needs reconnecting"));
    EXPECT_TRUE(contains(err->what(), "(request: GET /users/me/profile)"));
    EXPECT_EQ(err->connect, std::vector<std::string>{"google"});
    EXPECT_EQ(err->reconnect, std::vector<std::string>{"google"});
}

TEST(MailGmail, Status403NamesTheReason) {
    FakeApi api([](const Seen&) { return answer(403, gmailError(403, "insufficientPermissions").dump()); });
    const auto err = caught<MailboxAuthError>([&] { gmail(api)->getUserEmail(); });
    ASSERT_TRUE(err);
    EXPECT_TRUE(contains(lower(err->what()), "gmail"));
    EXPECT_TRUE(contains(err->what(), "reason: insufficientPermissions)"));
    EXPECT_EQ(err->connect, std::vector<std::string>{"google"});
}

TEST(MailGmail, AccessNotConfiguredSurfacesTheEnableUrl) {
    const std::string url = "https://console.developers.google.com/apis/api/gmail.googleapis.com/overview";
    FakeApi withHelp([&](const Seen&) { return answer(403, gmailError(403, "accessNotConfigured", url).dump()); });
    const auto err = caught<MailboxAuthError>([&] { gmail(withHelp)->getUserEmail(); });
    ASSERT_TRUE(err);
    EXPECT_TRUE(contains(err->what(), url));
    EXPECT_TRUE(err->connect.empty());

    FakeApi noHelp([](const Seen&) { return answer(403, gmailError(403, "accessNotConfigured").dump()); });
    const auto fallback = caught<MailboxAuthError>([&] { gmail(noHelp)->getUserEmail(); });
    ASSERT_TRUE(fallback);
    EXPECT_TRUE(contains(fallback->what(), "https://console.cloud.google.com/apis/library/gmail.googleapis.com"));
}

TEST(MailGmail, NonHttpsHelpLinkIsDropped) {
    FakeApi api([](const Seen&) {
        return answer(403, gmailError(403, "accessNotConfigured", "javascript:alert(1)").dump());
    });
    const auto err = caught<MailboxAuthError>([&] { gmail(api)->getUserEmail(); });
    ASSERT_TRUE(err);
    EXPECT_FALSE(contains(err->what(), "javascript"));
}

TEST(MailGmail, Status404NamesTheFix) {
    FakeApi api([](const Seen&) { return answer(404); });
    const auto err = caught<MailboxError>([&] { gmail(api)->getThread("nope"); });
    ASSERT_TRUE(err);
    EXPECT_TRUE(contains(err->what(), "(404)"));
    EXPECT_TRUE(contains(err->what(), "list_inbox"));
    EXPECT_TRUE(contains(err->what(), "GET /users/me/threads/nope"));
}

TEST(MailGmail, Status429SurfacesRetryAfter) {
    FakeApi api([](const Seen&) { return answer(429, "slow down", {{"retry-after", "30"}}); });
    const auto err = caught<MailboxError>([&] { gmail(api)->getUserEmail(); });
    ASSERT_TRUE(err);
    EXPECT_TRUE(contains(err->what(), "Retry after 30s"));

    FakeApi bare([](const Seen&) { return answer(429); });
    const auto unknown = caught<MailboxError>([&] { gmail(bare)->getUserEmail(); });
    ASSERT_TRUE(unknown);
    EXPECT_TRUE(contains(unknown->what(), "Retry after unknowns"));
}

TEST(MailGmail, ErrorNeverContainsTheTokenOrTheBody) {
    FakeApi api([](const Seen&) { return answer(500, "test-token leaked"); });
    const auto err = caught<MailboxError>([&] { gmail(api)->getUserEmail(); });
    ASSERT_TRUE(err);
    EXPECT_FALSE(contains(err->what(), "test-token"));
    EXPECT_FALSE(contains(err->what(), "Bearer"));
    EXPECT_TRUE(contains(err->what(), "GET /users/me/profile returned 500."));
}

TEST(MailGmail, NonEnvelopeErrorBodiesStillGiveTheActionable403) {
    for (const std::string body : {"[1,2]", "{\"error\": \"denied\"}", "\"just a string\"",
                                   "{\"error\": {\"errors\": \"not-a-list\"}}",
                                   "{\"error\": {\"errors\": {\"reason\": \"nested-wrong\"}}}", "null", "{}",
                                   "{\"error\": null}", "<html>", ""}) {
        FakeApi api([&](const Seen&) { return answer(403, body); });
        const auto err = caught<MailboxAuthError>([&] { gmail(api)->listInbox(25, false); });
        ASSERT_TRUE(err) << body;
        EXPECT_TRUE(contains(err->what(), "needs reconnecting")) << body;
        EXPECT_TRUE(contains(err->what(), "reason: forbidden)")) << body;
        EXPECT_EQ(err->connect, std::vector<std::string>{"google"});
    }
}

TEST(MailGmail, AReasonThatIsNotATokenIsDropped) {
    FakeApi api([](const Seen&) {
        return answer(403, json({{"error", {{"errors", {{{"reason", "<html>go away</html>"}}}}}}}).dump());
    });
    const auto err = caught<MailboxAuthError>([&] { gmail(api)->getUserEmail(); });
    ASSERT_TRUE(err);
    EXPECT_FALSE(contains(err->what(), "<html>"));
    EXPECT_TRUE(contains(err->what(), "reason: forbidden)"));
}

TEST(MailGmail, StatusIsTheReasonWhenErrorsCarryNone) {
    FakeApi api([](const Seen&) { return answer(500, json({{"error", {{"status", "INTERNAL"}}}}).dump()); });
    const auto err = caught<MailboxError>([&] { gmail(api)->getUserEmail(); });
    ASSERT_TRUE(err);
    EXPECT_TRUE(contains(err->what(), "returned 500 (INTERNAL)."));
}

TEST(MailGmail, NetworkFailureIsActionable) {
    FakeApi api([](const Seen&) -> HttpResponse { throw std::runtime_error("no route to host"); });
    const auto err = caught<MailboxError>([&] { gmail(api)->getUserEmail(); });
    ASSERT_TRUE(err);
    EXPECT_TRUE(contains(err->what(), "Check network connectivity"));
}

TEST(MailGmail, AnAnswerThatIsNotJsonIsAnError) {
    FakeApi api([](const Seen&) { return answer(200, "<html>"); });
    EXPECT_THROW(gmail(api)->getUserEmail(), MailboxError);
}

TEST(MailGmail, TokenIsRemintedPerSingleRequest) {
    int calls = 0;
    FakeApi api([](const Seen&) { return ok({{"emailAddress", "me@example.com"}}); });
    auto backend = gmail(api, [&] { return "token-" + std::to_string(++calls); });
    backend->getUserEmail();
    backend->getUserEmail();
    const auto seen = api.seen();
    ASSERT_EQ(seen.size(), 2u);
    EXPECT_EQ(seen[0].auth, "Bearer token-1");
    EXPECT_EQ(seen[1].auth, "Bearer token-2");
}

// ===========================================================================
// Gmail: listing, fan-out, search, read
// ===========================================================================

TEST(MailGmail, ListInboxFiltersOnLabelIds) {
    FakeApi api(inboxHandler({"m1"}));
    gmail(api)->listInbox(10, true);
    const auto listing = api.where([](const Seen& s) { return endsWith(s.path, "/users/me/messages"); });
    ASSERT_EQ(listing.size(), 1u);
    EXPECT_EQ(listing[0].all("labelIds"), (std::vector<std::string>{"INBOX", "UNREAD"}));
    EXPECT_FALSE(listing[0].has("q"));
    EXPECT_EQ(listing[0].param("maxResults"), "10");

    FakeApi plain(inboxHandler({"m1"}));
    gmail(plain)->listInbox(25, false);
    const auto inboxOnly = plain.where([](const Seen& s) { return endsWith(s.path, "/users/me/messages"); });
    EXPECT_EQ(inboxOnly[0].all("labelIds"), std::vector<std::string>{"INBOX"});
}

TEST(MailGmail, ListingAsksForFullFormatUnderABodylessFieldMask) {
    FakeApi api(inboxHandler({"m1"}));
    const json out = gmail(api)->listInbox(25, false);
    const auto fetch = api.where([](const Seen& s) { return contains(s.path, "/users/me/messages/"); });
    ASSERT_EQ(fetch.size(), 1u);
    EXPECT_EQ(fetch[0].path, "/gmail/v1/users/me/messages/m1");
    EXPECT_EQ(fetch[0].param("format"), "full");
    EXPECT_FALSE(fetch[0].has("metadataHeaders"));
    const std::string fields = fetch[0].param("fields");
    EXPECT_EQ(fields.rfind("id,threadId,labelIds,snippet,internalDate,payload(mimeType,filename,headers,body/size,parts(", 0),
              0u);
    EXPECT_TRUE(contains(fields, "body/size"));
    std::string withoutSize = fields;
    for (std::size_t p; (p = withoutSize.find("body/size")) != std::string::npos;) withoutSize.erase(p, 9);
    EXPECT_FALSE(contains(withoutSize, "data"));
    // Eight nested part levels, balanced.
    EXPECT_EQ(std::count(fields.begin(), fields.end(), '('), 9);
    EXPECT_EQ(std::count(fields.begin(), fields.end(), ')'), 9);
    ASSERT_EQ(out.size(), 1u);
    EXPECT_FALSE(out[0].contains("body"));
}

TEST(MailGmail, FanOutFetchesEveryIdInOrderWithBoundedOverlap) {
    std::vector<std::string> ids;
    for (int i = 0; i < 25; ++i) ids.push_back("m" + std::to_string(i));
    std::atomic<int> inFlight{0}, peak{0};
    int tokens = 0;
    FakeApi api([&](const Seen& s) {
        if (endsWith(s.path, "/users/me/labels")) return ok(kLabels);
        if (endsWith(s.path, "/users/me/messages")) return inboxHandler(ids)(s);
        const int now = ++inFlight;
        for (int p = peak.load(); now > p && !peak.compare_exchange_weak(p, now);) {
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
        --inFlight;
        return ok(gmailMessage(s.path.substr(s.path.rfind('/') + 1)));
    });
    const json out = gmail(api, [&] { return "token-" + std::to_string(++tokens); })->listInbox(25, false);
    ASSERT_EQ(out.size(), 25u);
    for (int i = 0; i < 25; ++i) EXPECT_EQ(out[i]["id"], ids[i]);
    EXPECT_EQ(api.where([](const Seen& s) { return contains(s.path, "/users/me/messages/"); }).size(), 25u);
    EXPECT_GT(peak.load(), 1) << "fetches never overlapped";
    EXPECT_LE(peak.load(), 5);
    // The listing, the labels, and one token shared by the whole fan-out.
    EXPECT_EQ(tokens, 3);
    for (const auto& s : api.where([](const Seen& s) { return contains(s.path, "/users/me/messages/"); })) {
        EXPECT_EQ(s.auth, "Bearer token-3");
    }
}

TEST(MailGmail, ASubrequestFailureIsRaisedNotDropped) {
    FakeApi api([](const Seen& s) {
        if (endsWith(s.path, "/m7")) return answer(500, gmailError(500, "backendError").dump());
        std::vector<std::string> ids;
        for (int i = 0; i < 10; ++i) ids.push_back("m" + std::to_string(i));
        return inboxHandler(ids)(s);
    });
    const auto err = caught<MailboxError>([&] { gmail(api)->listInbox(10, false); });
    ASSERT_TRUE(err);
    EXPECT_TRUE(contains(err->what(), "GET /users/me/messages/m7 returned 500 (backendError)"));
}

TEST(MailGmail, EmptyMailboxReturnsNoMessages) {
    FakeApi api([](const Seen& s) {
        if (endsWith(s.path, "/users/me/labels")) return ok(kLabels);
        return ok({{"resultSizeEstimate", 0}});
    });
    EXPECT_EQ(gmail(api)->listInbox(25, false), json::array());
    EXPECT_EQ(api.seen().size(), 1u);
}

TEST(MailGmail, LabelsAreListedOncePerBackend) {
    FakeApi api(inboxHandler({"m1", "m2"}));
    auto backend = gmail(api);
    backend->listInbox(25, false);
    backend->listInbox(25, false);
    EXPECT_EQ(api.where([](const Seen& s) { return endsWith(s.path, "/users/me/labels"); }).size(), 1u);
}

TEST(MailGmail, UserLabelNamesReachTheSummary) {
    FakeApi api([](const Seen& s) {
        if (endsWith(s.path, "/users/me/labels")) return ok(kLabels);
        if (endsWith(s.path, "/users/me/messages")) return ok({{"messages", {{{"id", "m1"}, {"threadId", "m1"}}}}});
        return ok(gmailMessage("m1", "", kSeptember2, {"INBOX", "UNREAD", "IMPORTANT", "CATEGORY_PROMOTIONS", "Label_17",
                                                       "Label_99"}));
    });
    const json out = gmail(api)->listInbox(25, false);
    EXPECT_EQ(out[0]["categories"], json({"Receipts", "Label_99"}));
}

TEST(MailGmail, LimitIsClampedToTheGmailCeilingAndMustBePositive) {
    FakeApi api(inboxHandler({"m1"}));
    gmail(api)->listInbox(5000, false);
    EXPECT_EQ(api.where([](const Seen& s) { return endsWith(s.path, "/users/me/messages"); })[0].param("maxResults"),
              "100");
    for (const int bad : {0, -1}) {
        FakeApi none(inboxHandler({"m1"}));
        const auto err = caught<std::invalid_argument>([&] { gmail(none)->listInbox(bad, false); });
        ASSERT_TRUE(err);
        EXPECT_EQ(std::string(err->what()), "limit must be >= 1, got " + std::to_string(bad));
        EXPECT_TRUE(none.seen().empty());
    }
}

TEST(MailGmail, SearchSendsQAndSpansEveryFolder) {
    FakeApi api(inboxHandler({"m1"}));
    gmail(api)->search("invoice from acme", 25);
    const auto listing = api.where([](const Seen& s) { return endsWith(s.path, "/users/me/messages"); });
    EXPECT_EQ(listing[0].param("q"), "invoice from acme");
    EXPECT_FALSE(listing[0].has("labelIds"));
    for (const std::string bad : {"", "   "}) {
        const auto err = caught<std::invalid_argument>([&] { gmail(api)->search(bad, 25); });
        ASSERT_TRUE(err);
        EXPECT_TRUE(contains(err->what(), "non-empty search string"));
    }
}

TEST(MailGmail, GetMessageAsksForTheFullFormatAndReturnsABody) {
    FakeApi api(inboxHandler({}));
    const json out = gmail(api)->getMessage("m1");
    const auto fetch = api.where([](const Seen& s) { return endsWith(s.path, "/users/me/messages/m1"); });
    ASSERT_EQ(fetch.size(), 1u);
    EXPECT_EQ(fetch[0].params, (std::vector<std::pair<std::string, std::string>>{{"format", "full"}}));
    EXPECT_EQ(out["body"], kAsk);
    EXPECT_EQ(out["body_content_type"], "text");
}

TEST(MailGmail, BadMessageIdsAreRefusedBeforeAnyRequest) {
    FakeApi api(inboxHandler({}));
    for (const std::string bad : {"", "   "}) {
        const auto err = caught<std::invalid_argument>([&] { gmail(api)->getMessage(bad); });
        ASSERT_TRUE(err);
        EXPECT_TRUE(contains(err->what(), "non-empty message id"));
    }
    for (const std::string bad : std::vector<std::string>{"../labels/INBOX", "m1?alt=media", "m1/attachments", std::string(129, 'x')}) {
        const auto err = caught<std::invalid_argument>([&] { gmail(api)->getMessage(bad); });
        ASSERT_TRUE(err) << bad;
        EXPECT_TRUE(contains(err->what(), "not a Gmail message id"));
    }
    const auto quoted = caught<std::invalid_argument>([&] { gmail(api)->getMessage("m1/x"); });
    EXPECT_EQ(std::string(quoted->what()),
              "message_id 'm1/x' is not a Gmail message id. Use an id returned by list_inbox or search.");
    EXPECT_TRUE(api.seen().empty());
}

TEST(MailGmail, AnInvalidBase64BodyIsAnError) {
    json msg = gmailMessage("m1");
    msg["payload"]["parts"][0]["body"]["data"] = "a";
    FakeApi api([&](const Seen& s) { return endsWith(s.path, "/labels") ? ok(kLabels) : ok(msg); });
    const auto err = caught<MailboxError>([&] { gmail(api)->getMessage("m1"); });
    ASSERT_TRUE(err);
    EXPECT_TRUE(contains(err->what(), "not valid base64"));
}

TEST(MailGmail, BodyDecodesBase64UrlWithoutPaddingAndReplacesBadUtf8) {
    json msg = gmailMessage("m1");
    msg["payload"]["parts"][0]["body"]["data"] = b64url(std::string("ok \xFF\xFE ?>~ caf\xC3\xA9"));
    FakeApi api([&](const Seen& s) { return endsWith(s.path, "/labels") ? ok(kLabels) : ok(msg); });
    EXPECT_EQ(gmail(api)->getMessage("m1")["body"], "ok \xEF\xBF\xBD\xEF\xBF\xBD ?>~ caf\xC3\xA9");
}

// ===========================================================================
// Gmail: folders
// ===========================================================================

TEST(MailGmail, ListFoldersUsesLabelsGetForExactCounts) {
    const json labels = {{"labels",
                          {{{"id", "INBOX"}, {"name", "INBOX"}},
                           {{"id", "SENT"}, {"name", "SENT"}},
                           {{"id", "Label_17"}, {"name", "Receipts"}},
                           {{"name", "no id"}}}}};
    FakeApi api([&](const Seen& s) {
        if (endsWith(s.path, "/users/me/labels")) return ok(labels);
        const std::string id = s.path.substr(s.path.rfind('/') + 1);
        if (id == "INBOX") return ok({{"id", "INBOX"}, {"name", "INBOX"}, {"messagesTotal", 120}, {"messagesUnread", 4}});
        if (id == "SENT") return ok({{"id", "SENT"}, {"messagesTotal", 40}});
        return ok({{"id", id}, {"name", "Receipts"}, {"messagesTotal", 3}, {"messagesUnread", 1}});
    });
    const json folders = gmail(api)->listFolders(50);
    ASSERT_EQ(folders.size(), 3u);
    EXPECT_EQ(folders[0], json({{"id", "INBOX"}, {"name", "INBOX"}, {"unread", 4}, {"total", 120}}));
    EXPECT_EQ(folders[1], json({{"id", "SENT"}, {"name", "SENT"}, {"unread", 0}, {"total", 40}}));
    EXPECT_EQ(folders[2]["name"], "Receipts");
    EXPECT_EQ(api.where([](const Seen& s) { return contains(s.path, "/users/me/labels/"); }).size(), 3u);

    FakeApi capped([&](const Seen& s) { return endsWith(s.path, "/labels") ? ok(labels) : ok({{"id", "INBOX"}}); });
    EXPECT_EQ(gmail(capped)->listFolders(2).size(), 2u);
}

// ===========================================================================
// Gmail: threads
// ===========================================================================

TEST(MailGmail, GetThreadCallsThreadsGetAndSortsOldestFirst) {
    json draft = gmailMessage("d1", "t1", kSeptember2 + 3000, {"DRAFT"}, "half");
    FakeApi api([&](const Seen& s) {
        if (endsWith(s.path, "/users/me/labels")) return ok(kLabels);
        return ok({{"id", "t1"},
                   {"messages",
                    {gmailMessage("a2", "t1", kSeptember2 + 2000), draft, gmailMessage("a1", "t1", kSeptember2),
                     gmailMessage("s1", "t1", kSeptember2 + 1000, json::array({"SENT"}))}}});
    });
    const json thread = gmail(api)->getThread("t1");
    const auto sent = api.where([](const Seen& s) { return contains(s.path, "/threads/"); });
    ASSERT_EQ(sent.size(), 1u);
    EXPECT_EQ(sent[0].method, "GET");
    EXPECT_EQ(sent[0].path, "/gmail/v1/users/me/threads/t1");
    EXPECT_EQ(sent[0].param("format"), "full");
    std::vector<std::string> order;
    for (const auto& m : thread["messages"]) order.push_back(m["id"]);
    EXPECT_EQ(order, (std::vector<std::string>{"a1", "s1", "a2", "d1"}));
    EXPECT_EQ(thread["thread_id"], "t1");
    EXPECT_EQ(thread["truncated"], false);
    EXPECT_EQ(thread["messages"][0]["body"], kAsk);
    EXPECT_EQ(thread["messages"][3]["draft"], true);
    EXPECT_EQ(thread["messages"][0]["draft"], false);
    EXPECT_EQ(thread["messages"][3]["_label_ids"], json({"DRAFT"}));
    EXPECT_EQ(thread["messages"][1]["_label_ids"], json({"SENT"}));
}

TEST(MailGmail, GetThreadRefusesAMalformedIdBeforeAnyRequest) {
    FakeApi api(inboxHandler({}));
    for (const std::string bad : std::vector<std::string>{"", "t 1", "../labels", "t1/../../profile", "t1?x=1", std::string(129, 'x')}) {
        const auto err = caught<std::invalid_argument>([&] { gmail(api)->getThread(bad); });
        ASSERT_TRUE(err) << bad;
        EXPECT_TRUE(contains(err->what(), "thread_id"));
    }
    EXPECT_TRUE(api.seen().empty());
}

TEST(MailGmail, ListThreadIdsListsBySystemLabel) {
    for (const auto& [folder, label] : std::vector<std::pair<std::string, std::string>>{{"inbox", "INBOX"}, {"sent", "SENT"}}) {
        FakeApi api([](const Seen&) { return ok({{"messages", {{{"id", "m1"}, {"threadId", "t1"}}}}}); });
        const json out = gmail(api)->listThreadIds(folder, 7);
        const auto seen = api.seen();
        ASSERT_EQ(seen.size(), 1u);
        EXPECT_EQ(seen[0].path, "/gmail/v1/users/me/messages");
        EXPECT_EQ(seen[0].params, (std::vector<std::pair<std::string, std::string>>{{"labelIds", label}, {"maxResults", "7"}}));
        EXPECT_EQ(out, json({{"thread_ids", {"t1"}}, {"messages_listed", 1}, {"truncated", false}}));
    }
}

TEST(MailGmail, ListThreadIdsDedupesAndReportsTruncation) {
    FakeApi full([](const Seen&) {
        return ok({{"messages", {{{"id", "m0"}, {"threadId", "t0"}}, {{"id", "m1"}, {"threadId", "t1"}}, {{"id", "m2"}, {"threadId", "t0"}}}}});
    });
    const json out = gmail(full)->listThreadIds("inbox", 3);
    EXPECT_EQ(out["thread_ids"], json({"t0", "t1"}));
    EXPECT_EQ(out["messages_listed"], 3);
    EXPECT_EQ(out["truncated"], true);

    FakeApi paged([](const Seen&) { return ok({{"messages", {{{"id", "m0"}, {"threadId", "t0"}}}}, {"nextPageToken", "x"}}); });
    EXPECT_EQ(gmail(paged)->listThreadIds("inbox", 50)["truncated"], true);

    FakeApi empty([](const Seen&) { return ok({{"resultSizeEstimate", 0}}); });
    EXPECT_EQ(gmail(empty)->listThreadIds("sent", 50),
              json({{"thread_ids", json::array()}, {"messages_listed", 0}, {"truncated", false}}));
}

TEST(MailGmail, ListThreadIdsWithoutAThreadIdFailsLoudly) {
    FakeApi api([](const Seen&) { return ok({{"messages", {{{"id", "m1"}}}}}); });
    const auto err = caught<MailboxError>([&] { gmail(api)->listThreadIds("inbox", 50); });
    ASSERT_TRUE(err);
    EXPECT_TRUE(contains(err->what(), "Gmail listed message 'm1' without a threadId"));
}

TEST(MailGmail, ListThreadIdsRejectsAnUnknownFolder) {
    FakeApi api(inboxHandler({}));
    const auto err = caught<std::invalid_argument>([&] { gmail(api)->listThreadIds("archive", 50); });
    ASSERT_TRUE(err);
    EXPECT_EQ(std::string(err->what()), "folder must be one of ['inbox', 'sent'], got 'archive'");
}

// ===========================================================================
// Gmail: values the Python backend computes, replayed
// ===========================================================================

TEST(MailGmailParity, AddressHeadersMatchPython) {
    const json parity = loadParity();
    for (const auto& c : parity["address_list"]) {
        json msg = gmailMessage("m1");
        msg["payload"]["headers"] = {{{"name", "From"}, {"value", c[0]}}};
        FakeApi api([&](const Seen& s) { return endsWith(s.path, "/labels") ? ok(kLabels) : ok(msg); });
        EXPECT_EQ(gmail(api)->getMessage("m1")["from"], c[1]) << c[0];
    }
}

TEST(MailGmailParity, EncodedWordsMatchPython) {
    const json parity = loadParity();
    for (const auto& c : parity["decode"]) {
        json msg = gmailMessage("m1");
        msg["payload"]["headers"] = {{{"name", "Subject"}, {"value", c[0]}}, {{"name", "X-Autoreply"}, {"value", c[0]}}};
        FakeApi api([&](const Seen& s) { return endsWith(s.path, "/labels") ? ok(kLabels) : ok(msg); });
        const json out = gmail(api)->getMessage("m1");
        EXPECT_EQ(out["x_autoreply"], c[1]) << c[0];
    }
}

TEST(MailGmailParity, SnippetsMatchPython) {
    const json parity = loadParity();
    for (const auto& c : parity["preview"]) {
        json msg = gmailMessage("m1");
        msg["snippet"] = c[0];
        FakeApi api([&](const Seen& s) { return endsWith(s.path, "/labels") ? ok(kLabels) : ok(msg); });
        EXPECT_EQ(gmail(api)->getMessage("m1")["preview"], c[1]) << c[0];
    }
}

TEST(MailGmailParity, InternalDatesMatchPython) {
    const json parity = loadParity();
    for (const auto& c : parity["received"]) {
        json msg = gmailMessage("m1");
        msg["internalDate"] = c[0];
        FakeApi api([&](const Seen& s) { return endsWith(s.path, "/labels") ? ok(kLabels) : ok(msg); });
        EXPECT_EQ(gmail(api)->getMessage("m1")["received"], c[1]) << c[0].dump();
    }
}

TEST(MailGmailParity, WholeSummariesMatchPython) {
    const json parity = loadParity();
    ASSERT_FALSE(parity["gmail_summary"].empty());
    for (const auto& c : parity["gmail_summary"]) {
        const std::string name = c[0];
        const json& msg = c[1];
        const bool includeBody = c[3];
        const json labels = {{"labels", {{{"id", "Label_17"}, {"name", "Receipts"}}}}};
        const std::string id = msg["id"];
        FakeApi api([&](const Seen& s) {
            if (endsWith(s.path, "/users/me/labels")) return ok(labels);
            if (endsWith(s.path, "/users/me/messages")) return ok({{"messages", {{{"id", id}, {"threadId", id}}}}});
            return ok(msg);
        });
        const json out = includeBody ? gmail(api)->getMessage(id) : gmail(api)->listInbox(25, false)[0];
        EXPECT_EQ(out, c[4]) << name << " include_body=" << includeBody << "\n got: " << out.dump()
                             << "\nwant: " << c[4].dump();
    }
}

// ===========================================================================
// Graph: request validity
// ===========================================================================

TEST(MailGraph, ListInboxRequestsInboxFolderNewestFirst) {
    FakeApi api([](const Seen&) { return ok({{"value", {graphMessage()}}}); });
    const json messages = outlook(api)->listInbox(10, false);
    const auto seen = api.seen();
    ASSERT_EQ(seen.size(), 1u);
    EXPECT_EQ(seen[0].method, "GET");
    EXPECT_EQ(seen[0].path, "/v1.0/me/mailFolders/inbox/messages");
    EXPECT_EQ(seen[0].auth, "Bearer test-token");
    EXPECT_EQ(seen[0].param("$top"), "10");
    EXPECT_EQ(seen[0].param("$orderby"), "receivedDateTime desc");
    EXPECT_EQ(seen[0].param("$select"),
              "id,conversationId,subject,from,toRecipients,ccRecipients,receivedDateTime,isRead,isDraft,flag,"
              "categories,bodyPreview,hasAttachments");
    EXPECT_EQ(seen[0].param("$expand"), "attachments($select=name,contentType,size,isInline)");
    EXPECT_FALSE(seen[0].has("$filter"));
    ASSERT_EQ(messages.size(), 1u);
    EXPECT_FALSE(messages[0].contains("body"));
}

TEST(MailGraph, UnreadOnlyFilterLeadsWithTheSortedProperty) {
    FakeApi api([](const Seen&) { return ok({{"value", json::array()}}); });
    outlook(api)->listInbox(25, true);
    EXPECT_EQ(api.seen()[0].param("$filter"), "receivedDateTime ge 1900-01-01T00:00:00Z and isRead eq false");
    EXPECT_EQ(api.seen()[0].param("$orderby"), "receivedDateTime desc");
}

TEST(MailGraph, SearchQuotesTheTermAndOmitsOrderby) {
    FakeApi api([](const Seen&) { return ok({{"value", json::array()}}); });
    outlook(api)->search("invoice", 25);
    const Seen s = api.seen()[0];
    EXPECT_EQ(s.path, "/v1.0/me/messages");
    EXPECT_EQ(s.param("$search"), "\"invoice\"");
    EXPECT_FALSE(s.has("$orderby"));
    EXPECT_EQ(s.param("$expand"), "attachments($select=name,contentType,size,isInline)");
    for (const std::string bad : {"", "   "}) {
        const auto err = caught<std::invalid_argument>([&] { outlook(api)->search(bad, 25); });
        ASSERT_TRUE(err);
        EXPECT_TRUE(contains(err->what(), "non-empty search string"));
    }
}

TEST(MailGraph, GetMessageSelectsBodyAndHeaders) {
    FakeApi api([](const Seen&) { return ok(graphMessage()); });
    const json out = outlook(api)->getMessage("AAMk-1");
    const Seen s = api.seen()[0];
    EXPECT_EQ(s.path, "/v1.0/me/messages/AAMk-1");
    EXPECT_TRUE(endsWith(s.param("$select"), ",hasAttachments,internetMessageHeaders,body"));
    EXPECT_EQ(s.param("$expand"), "attachments($select=name,contentType,size,isInline)");
    EXPECT_EQ(out["body"], "<p>Can you confirm?</p>");
    EXPECT_EQ(out["body_content_type"], "html");
}

TEST(MailGraph, AnIdContainingASeparatorStaysOnePathSegment) {
    const std::string realId =
        "AAMkADYyMTBjZGZjLTNmNGEtNDU4Yy04MTIxLTgwZDRkZGI4ZmY0NABGAAAAAAB"
        "b1n5Ct_yWQ4XpZ0ueZLRLBwC0vORhuAAAAAAAEMAAC0vORhuAAACAQwAAA=";
    FakeApi api([](const Seen&) { return ok(graphMessage()); });
    outlook(api)->getMessage("AAMk/oQ+Dw==");
    EXPECT_EQ(api.seen()[0].rawPath, "/v1.0/me/messages/AAMk%2FoQ%2BDw%3D%3D");
    outlook(api)->getMessage(realId);
    EXPECT_EQ(api.seen()[1].path, "/v1.0/me/messages/" + realId);
}

TEST(MailGraph, BadMessageIdsAreRefusedBeforeAnyRequest) {
    FakeApi api([](const Seen&) { return ok(graphMessage()); });
    for (const std::string bad :
         {"../mailFolders/inbox", "AAMk-1/../../mailFolders", "AAMk-1?$select=body", "AAMk-1#frag", "AAMk 1"}) {
        const auto err = caught<std::invalid_argument>([&] { outlook(api)->getMessage(bad); });
        ASSERT_TRUE(err) << bad;
        EXPECT_TRUE(contains(err->what(), "not a Microsoft Graph message id")) << bad;
    }
    for (const std::string bad : {"", "   "}) {
        const auto err = caught<std::invalid_argument>([&] { outlook(api)->getMessage(bad); });
        ASSERT_TRUE(err);
        EXPECT_TRUE(contains(err->what(), "non-empty message id"));
    }
    EXPECT_TRUE(api.seen().empty());
}

TEST(MailGraph, TopIsClampedAndMustBePositive) {
    FakeApi api([](const Seen&) { return ok({{"value", json::array()}}); });
    outlook(api)->listInbox(5000, false);
    EXPECT_EQ(api.seen()[0].param("$top"), "999");
    for (const int bad : {0, -1}) {
        const auto err = caught<std::invalid_argument>([&] { outlook(api)->listInbox(bad, false); });
        ASSERT_TRUE(err);
        EXPECT_EQ(std::string(err->what()), "limit must be >= 1, got " + std::to_string(bad));
    }
}

TEST(MailGraph, TokenIsRemintedPerRequest) {
    int calls = 0;
    FakeApi api([](const Seen&) { return ok({{"value", json::array()}}); });
    auto backend = outlook(api, [&] { return "token-" + std::to_string(++calls); });
    backend->listInbox(25, false);
    backend->listInbox(25, false);
    EXPECT_EQ(api.seen()[0].auth, "Bearer token-1");
    EXPECT_EQ(api.seen()[1].auth, "Bearer token-2");
}

// ===========================================================================
// Graph: errors
// ===========================================================================

TEST(MailGraph, Status401NamesTheFix) {
    FakeApi api([](const Seen&) { return answer(401, "expired"); });
    const auto err = caught<MailboxAuthError>([&] { outlook(api)->listInbox(25, false); });
    ASSERT_TRUE(err);
    EXPECT_TRUE(contains(err->what(), "needs reconnecting"));
    EXPECT_TRUE(contains(err->what(), "(request: GET /me/mailFolders/inbox/messages)"));
    EXPECT_EQ(err->connect, std::vector<std::string>{"microsoft"});
    EXPECT_EQ(err->reconnect, std::vector<std::string>{"microsoft"});
}

TEST(MailGraph, Status403NamesTheScopeAndTheStructuredCode) {
    FakeApi coded([](const Seen&) {
        return answer(403, json({{"error", {{"code", "ErrorAccessDenied"}, {"message", "Access is denied."}}}}).dump());
    });
    const auto err = caught<MailboxAuthError>([&] { outlook(coded)->listInbox(25, false); });
    ASSERT_TRUE(err);
    EXPECT_TRUE(contains(err->what(), "Mail.ReadWrite"));
    EXPECT_TRUE(contains(err->what(), "code: ErrorAccessDenied)"));
    EXPECT_FALSE(contains(err->what(), "Access is denied"));
    EXPECT_EQ(err->connect, std::vector<std::string>{"microsoft"});

    for (const std::string body :
         {"<html><body>Blocked by proxy</body></html>", "[1,2]", "{\"error\": \"denied\"}", "\"just a string\"", "denied"}) {
        FakeApi api([&](const Seen&) { return answer(403, body); });
        const auto e = caught<MailboxAuthError>([&] { outlook(api)->listInbox(25, false); });
        ASSERT_TRUE(e) << body;
        EXPECT_TRUE(contains(e->what(), "code: forbidden)")) << body;
        EXPECT_FALSE(contains(e->what(), "Blocked by proxy"));
    }
}

TEST(MailGraph, Status429SurfacesRetryAfter) {
    FakeApi api([](const Seen&) { return answer(429, "slow down", {{"Retry-After", "30"}}); });
    const auto err = caught<MailboxError>([&] { outlook(api)->listInbox(25, false); });
    ASSERT_TRUE(err);
    EXPECT_TRUE(contains(err->what(), "Retry after 30s"));
}

TEST(MailGraph, GenericErrorSurfacesTheCodeNotTheBodyOrToken) {
    FakeApi api([](const Seen&) {
        return answer(500, json({{"error", {{"code", "InternalServerError"}, {"message", "test-token"}}}}).dump());
    });
    const auto err = caught<MailboxError>([&] { outlook(api)->listInbox(25, false); });
    ASSERT_TRUE(err);
    EXPECT_TRUE(contains(err->what(), "returned 500 (InternalServerError). Retry;"));
    EXPECT_FALSE(contains(err->what(), "test-token"));
    EXPECT_FALSE(contains(err->what(), "Bearer"));
    EXPECT_FALSE(contains(err->what(), "{\"error\""));
}

TEST(MailGraph, Status404NamesTheFix) {
    FakeApi api([](const Seen&) { return answer(404, json({{"error", {{"code", "ErrorItemNotFound"}}}}).dump()); });
    const auto err = caught<MailboxError>([&] { outlook(api)->getThread("conv-1"); });
    ASSERT_TRUE(err);
    EXPECT_TRUE(contains(err->what(), "(404)"));
    EXPECT_TRUE(contains(err->what(), "list_inbox"));
}

TEST(MailGraph, NetworkFailureIsActionable) {
    FakeApi api([](const Seen&) -> HttpResponse { throw std::runtime_error("no route to host"); });
    const auto err = caught<MailboxError>([&] { outlook(api)->listInbox(25, false); });
    ASSERT_TRUE(err);
    EXPECT_TRUE(contains(err->what(), "Could not reach Microsoft Graph for /me/mailFolders/inbox/messages: no route "
                                      "to host. Check network connectivity"));
}

TEST(MailGraph, UserEmailFallsBackToPrincipalNameAndFailsLoudlyWhenEmpty) {
    FakeApi upn([](const Seen&) { return ok({{"mail", nullptr}, {"userPrincipalName", "me@example.com"}}); });
    EXPECT_EQ(outlook(upn)->getUserEmail(), "me@example.com");
    EXPECT_EQ(upn.seen()[0].path, "/v1.0/me");
    EXPECT_EQ(upn.seen()[0].param("$select"), "mail,userPrincipalName");

    FakeApi empty([](const Seen&) { return ok({{"mail", nullptr}, {"userPrincipalName", ""}}); });
    const auto err = caught<MailboxError>([&] { outlook(empty)->getUserEmail(); });
    ASSERT_TRUE(err);
    EXPECT_TRUE(contains(err->what(), "unusable state"));
    EXPECT_EQ(err->connect, std::vector<std::string>{"microsoft"});
}

// ===========================================================================
// Graph: threads and folders
// ===========================================================================

TEST(MailGraph, GetThreadFiltersOnConversationWithoutOrderby) {
    json draft = graphMessage("d1", "conv-1", "2026-09-04T00:00:00Z");
    draft["isDraft"] = true;
    FakeApi api([&](const Seen&) {
        return ok({{"value",
                    {graphMessage("a2", "conv-1", "2026-09-03T00:00:00Z"), draft,
                     graphMessage("a1", "conv-1", "2026-09-01T00:00:00Z")}}});
    });
    const json thread = outlook(api)->getThread("conv-1");
    const Seen s = api.seen()[0];
    EXPECT_EQ(s.path, "/v1.0/me/messages");
    EXPECT_EQ(s.param("$filter"), "conversationId eq 'conv-1'");
    EXPECT_FALSE(s.has("$orderby"));
    EXPECT_EQ(s.param("$top"), "100");
    EXPECT_TRUE(endsWith(s.param("$select"), ",internetMessageHeaders,body"));
    EXPECT_TRUE(s.has("$expand"));
    std::vector<std::string> order;
    for (const auto& m : thread["messages"]) order.push_back(m["id"]);
    EXPECT_EQ(order, (std::vector<std::string>{"a1", "a2", "d1"}));
    EXPECT_EQ(thread["messages"][2]["draft"], true);
    EXPECT_EQ(thread["messages"][0]["_label_ids"], json::array());
    EXPECT_EQ(thread["truncated"], false);
    EXPECT_EQ(thread["thread_id"], "conv-1");
}

TEST(MailGraph, GetThreadReportsAConversationLongerThanOnePage) {
    FakeApi api([](const Seen&) { return ok({{"value", {graphMessage()}}, {"@odata.nextLink", "https://next"}}); });
    EXPECT_EQ(outlook(api)->getThread("conv")["truncated"], true);
}

TEST(MailGraph, GetThreadKeepsQuotesOutOfTheODataLiteral) {
    FakeApi api([](const Seen&) { return ok({{"value", json::array()}}); });
    for (const std::string bad : {"conv' or isRead eq true or conversationId eq '", "a b", "x'y", ""}) {
        const auto err = caught<std::invalid_argument>([&] { outlook(api)->getThread(bad); });
        ASSERT_TRUE(err) << bad;
        EXPECT_TRUE(contains(err->what(), "thread_id"));
    }
    EXPECT_TRUE(api.seen().empty());
    const std::string conv = "AAQkADAwATM0MDAAMS1iNWU2LTg0ZjYtMDACLTAwCgAQAJ+xZ/8==";
    outlook(api)->getThread(conv);
    EXPECT_EQ(api.seen()[0].param("$filter"), "conversationId eq '" + conv + "'");
}

TEST(MailGraph, ListThreadIdsUsesWellKnownFolders) {
    for (const auto& [folder, path] : std::vector<std::pair<std::string, std::string>>{
             {"inbox", "/v1.0/me/mailFolders/inbox/messages"}, {"sent", "/v1.0/me/mailFolders/sentitems/messages"}}) {
        FakeApi api([](const Seen&) { return ok({{"value", json::array()}}); });
        outlook(api)->listThreadIds(folder, 5);
        const Seen s = api.seen()[0];
        EXPECT_EQ(s.path, path);
        EXPECT_EQ(s.params, (std::vector<std::pair<std::string, std::string>>{
                                {"$top", "5"}, {"$select", "id,conversationId"}, {"$orderby", "receivedDateTime desc"}}));
    }
    FakeApi api([](const Seen&) { return ok({{"value", json::array()}}); });
    EXPECT_THROW(outlook(api)->listThreadIds("archive", 5), std::invalid_argument);
}

TEST(MailGraph, ListThreadIdsDedupesAndReportsANextPage) {
    FakeApi api([](const Seen&) {
        return ok({{"value", {{{"id", "m0"}, {"conversationId", "c0"}}, {{"id", "m1"}, {"conversationId", "c0"}}, {{"id", "solo"}}}},
                   {"@odata.nextLink", "https://next"}});
    });
    const json out = outlook(api)->listThreadIds("inbox", 50);
    EXPECT_EQ(out, json({{"thread_ids", {"c0", "solo"}}, {"messages_listed", 3}, {"truncated", true}}));

    FakeApi full([](const Seen&) { return ok({{"value", {{{"id", "m0"}, {"conversationId", "c0"}}, {{"id", "m1"}, {"conversationId", "c1"}}}}}); });
    EXPECT_EQ(outlook(full)->listThreadIds("inbox", 2)["truncated"], true);
    EXPECT_EQ(outlook(full)->listThreadIds("inbox", 3)["truncated"], false);
}

TEST(MailGraph, ListFoldersShape) {
    FakeApi api([](const Seen&) {
        return ok({{"value",
                    {{{"id", "f1"}, {"displayName", "Inbox"}, {"unreadItemCount", 4}, {"totalItemCount", 120}},
                     {{"id", "f2"}}}}});
    });
    const json folders = outlook(api)->listFolders(50);
    EXPECT_EQ(folders, json({{{"id", "f1"}, {"name", "Inbox"}, {"unread", 4}, {"total", 120}},
                             {{"id", "f2"}, {"name", ""}, {"unread", 0}, {"total", 0}}}));
    const Seen s = api.seen()[0];
    EXPECT_EQ(s.path, "/v1.0/me/mailFolders");
    EXPECT_EQ(s.params, (std::vector<std::pair<std::string, std::string>>{
                            {"$top", "50"}, {"$select", "id,displayName,unreadItemCount,totalItemCount"}}));
}

TEST(MailGraphParity, WholeSummariesMatchPython) {
    const json parity = loadParity();
    ASSERT_FALSE(parity["graph_summary"].empty());
    for (const auto& c : parity["graph_summary"]) {
        const json& msg = c[1];
        const bool includeBody = c[2];
        FakeApi api([&](const Seen& s) { return endsWith(s.path, "/messages") ? ok({{"value", {msg}}}) : ok(msg); });
        const json out = includeBody ? outlook(api)->getMessage(msg["id"]) : outlook(api)->listInbox(25, false)[0];
        EXPECT_EQ(out, c[3]) << c[0] << " include_body=" << includeBody << "\n got: " << out.dump()
                             << "\nwant: " << c[3].dump();
    }
}

// ===========================================================================
// Both backends
// ===========================================================================

TEST(MailBackends, SummaryAndThreadKeysAreIdenticalAcrossProviders) {
    FakeApi g([](const Seen& s) {
        if (endsWith(s.path, "/users/me/labels")) return ok(kLabels);
        if (contains(s.path, "/threads/")) return ok({{"messages", {gmailMessage("a1", "t1")}}});
        return ok(gmailMessage("m1"));
    });
    FakeApi o([](const Seen& s) { return endsWith(s.path, "/me/messages") ? ok({{"value", {graphMessage()}}}) : ok(graphMessage()); });
    const auto keys = [](const json& obj) {
        std::vector<std::string> out;
        for (auto it = obj.begin(); it != obj.end(); ++it) out.push_back(it.key());
        return out;
    };
    EXPECT_EQ(keys(gmail(g)->getMessage("m1")), keys(outlook(o)->getMessage("AAMk-1")));
    const json gt = gmail(g)->getThread("t1"), ot = outlook(o)->getThread("t1");
    EXPECT_EQ(keys(gt), keys(ot));
    EXPECT_EQ(keys(gt["messages"][0]), keys(ot["messages"][0]));
    EXPECT_EQ(gt["messages"][0]["from"], "Ruiz, Dana <dana@example.com>");
    EXPECT_EQ(gt["messages"][0]["received"], ot["messages"][0]["received"]);
}
