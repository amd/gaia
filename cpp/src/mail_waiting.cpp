// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Port of src/gaia/agents/tools/_email/waiting.py: which inbox threads wait on
// the user's reply. Same phrase lists in the same order, same thresholds, same
// output keys. The regexes are hand-written matchers over code points, since
// std::regex has neither Unicode classes nor Python's IGNORECASE folding.

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <exception>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include "gaia/mail.h"
#include "mail_internal.h"
#include "mail_screens_detail.h"
#include "mail_text.h"

namespace gaia::mail {

namespace {

using detail::field;
using text::Cps;

constexpr int kDefaultMaxSentLookback = 50;
constexpr size_t kThreadFetchConcurrency = 5;
constexpr int64_t kDayMs = 24LL * 60 * 60 * 1000;
constexpr const char* kSignalDirectAsk = "direct_ask";
constexpr const char* kSignalMeetingTime = "meeting_time";
constexpr const char* kCorroborationThreadReply = "thread_reply_history";

const std::vector<const char*> kDirectAskPhrases = {
    "can you", "could you", "would you", "will you", "any chance you", "any chance we",
    "any update on", "any updates on", "any thoughts on", "any feedback on", "let me know if",
    "let me know what", "let me know when", "let me know your", "what do you think",
    "what are your thoughts", "when works for you", "when do you", "when would you", "do you have",
    "do you know", "did you get a chance", "did you have a chance", "have you had a chance",
    "were you able to", "would you be able to", "checking in on", "circling back on",
    "following up on", "wanted to follow up", "still waiting on", "waiting on your",
    "need your input", "need your feedback", "need your thoughts", "need your approval",
    "need your sign-off", "need your ok", "could you please", "can you please", "please advise",
    "please confirm", "please let me know", "please review", "please take a look",
};

const std::vector<const char*> kMeetingVerbPhrases = {
    "chance to meet", "free to meet", "able to meet", "meet up", "meet for", "meet this",
    "meet next", "meet tomorrow", "grab some time", "grab time", "find time to meet",
    "find a time to meet",
};

const std::vector<const char*> kInvitePhrases = {
    "are you free", "are you available", "do you have time", "let's meet", "lets meet",
    "let's grab", "lets grab", "can we meet", "can we chat", "can we hop on", "hop on a call",
    "schedule a meeting", "schedule a call", "schedule a time", "set up a meeting",
    "set up a call", "setup a meeting", "book a meeting", "book a time", "calendar invite",
    "meeting invite", "meeting request", "invite you to", "would you like to meet",
    "want to meet", "let's connect", "lets connect", "let's set up", "lets set up",
    "does that time work", "does that work for you", "i'd like to schedule",
    "would like to schedule", "i want to schedule", "any chance to meet",
    "any chance we could meet", "any chance you could meet", "any chance you're free",
};

const std::vector<const char*> kMeetingNouns = {
    "meeting", "meet", "call", "1:1", "one-on-one", "sync", "huddle", "standup", "stand-up",
    "zoom", "google meet", "teams meeting",
};

// A noun and a time 82+ chars apart were two unrelated mentions in every
// measured marketing false positive; 60 keeps genuine same-clause phrasing.
constexpr size_t kNounTimeProximityChars = 60;

const std::vector<const char*> kSlotProposalPhrases = {
    "here are some times", "here are a few times", "here are some options",
    "propose several times", "propose a few times", "let me know what time works",
    "let me know which time works", "does that work for", "does this work for", "work for you",
    "i'm available", "i am available",
};

const std::vector<const char*> kOptOutPhrases = {
    "unsubscribe", "remove me from this list", "remove me from your list", "take me off this list",
    "take me off your list", "stop emailing me", "stop contacting me", "stop sending me",
    "please stop sending", "no longer interested", "not interested, please remove", "opt out",
    "opt-out", "do not contact me again", "do not email me again",
};

// "Please remove me from this list." is 6 words; a real reply is more.
constexpr size_t kMinSubstantiveWords = 8;

const std::vector<const char*> kAutomatedSenderKeywords = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "auto-confirm", "notifications@",
    "alerts@", "store-news",
};

const std::vector<const char*> kPromoSubjectKeywords = {
    "50% off", "sale ends", "limited time", "special offer", "deal of the day", "discount code",
    "coupon", "this week's deals",
};

// A real obligation buried under a promotions label must still reach the user.
const std::vector<const char*> kCommitmentSignalPatterns = {
    "action required", "response required", "responses are required", "respond by", "reply by",
    "rsvp by", "register by", "renew by", "confirm by", "due by", "payment due", "past due",
    "past-due", "overdue", "final notice", "final reminder", "deadline", "no later than",
    "attendance is required", "attendance required", "mandatory attendance", "required to attend",
    "must attend", "confirm your attendance", "failure to", "non-compliance", "noncompliance",
    "will be suspended", "will be cancelled", "will be canceled", "will be terminated",
    "will be revoked", "will be charged", "late fee", "avoid a late fee", "loss of access",
    "budget exceeded", "over budget", "you have exceeded", "you've exceeded", "exceeded your",
    "overdrawn",
};

// Gmail tabs that mean bulk mail. Outlook has no equivalent, so there the veto
// rests on the subject keywords alone.
const std::set<std::string> kPromoLabels = {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL"};
const std::set<std::string> kNonPromoLabels = {"CATEGORY_UPDATES", "CATEGORY_PERSONAL"};

const std::vector<const char*> kHtmlQuoteMarkers = {
    "divrplyfwdmsg", "appendonsend", "yahoo_quoted", "moz-cite-prefix",
};
constexpr const char* kQuoteMarkerLine = "\n-----Original Message-----\n";

bool anyIn(const std::string& text, const std::vector<const char*>& phrases) {
    return std::any_of(phrases.begin(), phrases.end(),
                       [&](const char* p) { return text.find(p) != std::string::npos; });
}

// ---- _VERB_TIME_RE / _TIME_RE ----

const std::vector<const char*> kVerbTimeWords = {
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "tomorrow", "tonight", "this afternoon", "this morning", "next week",
};
const std::vector<const char*> kTimeWords = {
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "tomorrow", "next week", "this week", "this afternoon", "this morning", "tonight",
    "noon", "midday",
};

struct TimePattern {
    const std::vector<const char*>& words;
    bool oclock;
};
const TimePattern kVerbTime{kVerbTimeWords, false};
const TimePattern kTime{kTimeWords, true};

constexpr size_t kNoMatch = static_cast<size_t>(-1);

size_t skipSpaces(const Cps& t, size_t k) {
    while (k < t.size() && text::isSpace(t[k])) ++k;
    return k;
}

bool digitsAt(const Cps& t, size_t at, size_t n) {
    if (at + n > t.size()) return false;
    for (size_t k = at; k < at + n; ++k) {
        if (!text::isDigit(t[k])) return false;
    }
    return true;
}

// The end of the pattern's match starting exactly at `i`, alternatives in order.
size_t timeMatchAt(const Cps& t, size_t i, const TimePattern& pattern) {
    if (!text::wordBoundary(t, i)) return kNoMatch;
    for (const char* word : pattern.words) {
        const size_t end = i + std::char_traits<char>::length(word);
        if (text::matchesIgnoreCase(t, i, word) && text::wordBoundary(t, end)) return end;
    }
    for (size_t n : {2, 1}) {  // \d{1,2}\s*(?:am|pm)\b
        if (!digitsAt(t, i, n)) continue;
        const size_t k = skipSpaces(t, i + n);
        if ((text::matchesIgnoreCase(t, k, "am") || text::matchesIgnoreCase(t, k, "pm")) &&
            text::wordBoundary(t, k + 2)) {
            return k + 2;
        }
    }
    for (size_t n : {2, 1}) {  // \d{1,2}:\d{2}\b
        if (!digitsAt(t, i, n)) continue;
        const size_t k = i + n;
        if (k < t.size() && t[k] == ':' && digitsAt(t, k + 1, 2) && text::wordBoundary(t, k + 3)) return k + 3;
    }
    if (pattern.oclock) {
        for (size_t n : {2, 1}) {  // \d{1,2}\s*o'clock\b
            if (!digitsAt(t, i, n)) continue;
            const size_t k = skipSpaces(t, i + n);
            if (text::matchesIgnoreCase(t, k, "o'clock") && text::wordBoundary(t, k + 7)) return k + 7;
        }
    }
    return kNoMatch;
}

std::vector<std::pair<size_t, size_t>> timeMatches(const Cps& t, const TimePattern& pattern, bool firstOnly) {
    std::vector<std::pair<size_t, size_t>> out;
    size_t i = 0;
    while (i <= t.size()) {
        const size_t end = timeMatchAt(t, i, pattern);
        if (end == kNoMatch) {
            ++i;
            continue;
        }
        out.emplace_back(i, end);
        if (firstOnly) break;
        i = end;
    }
    return out;
}

std::vector<std::pair<size_t, size_t>> spans(const char* term, const Cps& t) {
    const Cps needle = text::decode(term);
    std::vector<std::pair<size_t, size_t>> out;
    for (size_t idx = t.find(needle); idx != Cps::npos; idx = t.find(needle, idx + 1)) {
        out.emplace_back(idx, idx + needle.size());
    }
    return out;
}

bool nounNearTime(const Cps& t) {
    std::vector<std::pair<size_t, size_t>> nouns;
    for (const char* noun : kMeetingNouns) {
        for (const auto& span : spans(noun, t)) nouns.push_back(span);
    }
    if (nouns.empty()) return false;
    for (const auto& [tStart, tEnd] : timeMatches(t, kTime, false)) {
        for (const auto& [nStart, nEnd] : nouns) {
            size_t distance = 0;
            if (nEnd <= tStart) distance = tStart - nEnd;
            else if (tEnd <= nStart) distance = nStart - tEnd;
            if (distance <= kNounTimeProximityChars) return true;
        }
    }
    return false;
}

// ---- HTML ----

// The end of a `<[^<>]*>` (or `+` when `nonEmpty`) match at `i`, else kNoMatch.
size_t tagEnd(const Cps& t, size_t i, bool nonEmpty) {
    if (i >= t.size() || t[i] != '<') return kNoMatch;
    size_t j = i + 1;
    while (j < t.size() && t[j] != '<' && t[j] != '>') ++j;
    if (j >= t.size() || t[j] != '>') return kNoMatch;
    if (nonEmpty && j == i + 1) return kNoMatch;
    return j + 1;
}

// `<NAME\b[^<>]*>` once NAME has matched and ends at `k`.
size_t tailEnd(const Cps& t, size_t k) {
    if (!text::wordBoundary(t, k)) return kNoMatch;
    size_t j = k;
    while (j < t.size() && t[j] != '<' && t[j] != '>') ++j;
    return j < t.size() && t[j] == '>' ? j + 1 : kNoMatch;
}

struct BlockquoteTag {
    size_t start;
    size_t end;
    bool closing;
};

std::optional<BlockquoteTag> blockquoteAt(const Cps& t, size_t i) {
    if (t[i] != '<') return std::nullopt;
    size_t k = i + 1;
    const bool closing = k < t.size() && t[k] == '/';
    if (closing) ++k;
    if (!text::matchesIgnoreCase(t, k, "blockquote")) return std::nullopt;
    const size_t end = tailEnd(t, k + 10);
    if (end == kNoMatch) return std::nullopt;
    return BlockquoteTag{i, end, closing};
}

Cps dropBlockquotes(const Cps& body) {
    Cps kept;
    int depth = 0;
    size_t at = 0;
    size_t i = 0;
    while (i < body.size()) {
        const auto tag = blockquoteAt(body, i);
        if (!tag) {
            ++i;
            continue;
        }
        if (depth == 0) kept.append(body, at, tag->start - at);
        depth = tag->closing ? std::max(0, depth - 1) : depth + 1;
        at = tag->end;
        if (depth == 0) kept.push_back('\n');
        i = tag->end;
    }
    if (depth == 0) kept.append(body, at, Cps::npos);
    return kept;
}

size_t breakTagAt(const Cps& t, size_t i) {
    if (t[i] != '<') return kNoMatch;
    const size_t k = i + 1;
    if (text::matchesIgnoreCase(t, k, "br")) {
        const size_t end = tailEnd(t, k + 2);
        if (end != kNoMatch) return end;
    }
    const size_t n = k < t.size() && t[k] == '/' ? k + 1 : k;
    for (const char* name : {"div", "p", "li", "tr", "hr", "h", "table", "ul", "ol"}) {
        if (!text::matchesIgnoreCase(t, n, name)) continue;
        size_t after = n + std::char_traits<char>::length(name);
        if (name[0] == 'h' && name[1] == '\0') {
            if (after >= t.size() || t[after] < '1' || t[after] > '6') continue;
            ++after;
        }
        const size_t end = tailEnd(t, after);
        if (end != kNoMatch) return end;
    }
    return kNoMatch;
}

template <typename Replace>
Cps substitute(const Cps& t, size_t (*matchAt)(const Cps&, size_t), Replace replace) {
    Cps out;
    size_t i = 0;
    while (i < t.size()) {
        const size_t end = matchAt(t, i);
        if (end == kNoMatch) {
            out.push_back(t[i++]);
            continue;
        }
        out += replace(t.substr(i, end - i));
        i = end;
    }
    return out;
}

size_t anyTagAt(const Cps& t, size_t i) { return tagEnd(t, i, false); }
size_t nonEmptyTagAt(const Cps& t, size_t i) { return tagEnd(t, i, true); }

bool isHtml(const json& message) {
    std::string kind = text::textField(message, "body_content_type");
    if (kind.empty()) kind = "text";
    return text::lower(kind) == "html";
}

Cps stripTags(const Cps& body) {
    const Cps space = text::decode(" ");
    return substitute(body, nonEmptyTagAt, [&](const Cps&) { return space; });
}

std::vector<std::string> textLines(const json& message, bool dropQuotes) {
    std::string body = text::textField(message, "body");
    if (isHtml(message)) {
        Cps t = text::decode(body);
        if (dropQuotes) t = dropBlockquotes(t);
        const Cps marker = text::decode(kQuoteMarkerLine);
        t = substitute(t, anyTagAt, [&](const Cps& tag) {
            const std::string lowered = text::lower(text::encode(tag));
            return anyIn(lowered, kHtmlQuoteMarkers) ? marker : tag;
        });
        const Cps newline = text::decode("\n");
        t = substitute(t, breakTagAt, [&](const Cps&) { return newline; });
        body = text::htmlUnescape(text::encode(stripTags(t)));
    }
    return text::splitlines(body);
}

std::string norm(const std::string& line) {
    size_t start = 0;
    while (start < line.size() && (line[start] == '>' || line[start] == ' ')) ++start;
    std::string out;
    for (const auto& word : text::split(text::lower(line.substr(start)))) {
        if (!out.empty()) out += ' ';
        out += word;
    }
    return out;
}

// ---- quote-marker lines ----

bool isAttribution(const Cps& t) {  // ^On\b.*wrote:\s*$
    if (!text::matchesIgnoreCase(t, 0, "on") || !text::wordBoundary(t, 2)) return false;
    size_t end = t.size();
    while (end > 0 && text::isSpace(t[end - 1])) --end;
    return end >= 8 && text::matchesIgnoreCase(t, end - 6, "wrote:");
}

bool dashes(const Cps& t, size_t& k) {
    const size_t start = k;
    while (k < t.size() && t[k] == '-') ++k;
    return k - start >= 2;
}

bool isOriginalMessage(const Cps& t) {  // ^-{2,}\s*(Original|Forwarded) Message\s*-{2,}
    size_t k = 0;
    if (!dashes(t, k)) return false;
    k = skipSpaces(t, k);
    if (text::matchesIgnoreCase(t, k, "original")) k += 8;
    else if (text::matchesIgnoreCase(t, k, "forwarded")) k += 9;
    else return false;
    if (k >= t.size() || t[k] != ' ' || !text::matchesIgnoreCase(t, k + 1, "message")) return false;
    k = skipSpaces(t, k + 8);
    return dashes(t, k);
}

bool headerWithValue(const Cps& t, std::initializer_list<const char*> names) {  // ^(Name):\s*\S
    for (const char* name : names) {
        if (!text::matchesIgnoreCase(t, 0, name)) continue;
        return skipSpaces(t, std::char_traits<char>::length(name)) < t.size();
    }
    return false;
}

bool isHeaderField(const Cps& t) {  // ^(Sent|Date|To|Cc|Subject):
    for (const char* name : {"sent:", "date:", "to:", "cc:", "subject:"}) {
        if (text::matchesIgnoreCase(t, 0, name)) return true;
    }
    return false;
}

// ---- timestamps (datetime.fromisoformat, Python 3.11+) ----

bool asciiDigit(char c) { return c >= '0' && c <= '9'; }

const char* parseDigits(const char* p, int* var, size_t n) {
    *var = 0;
    for (size_t i = 0; i < n; ++i, ++p) {
        if (!asciiDigit(*p)) return nullptr;
        *var = *var * 10 + (*p - '0');
    }
    return p;
}

// CPython's parse_hh_mm_ss_ff: 0 at the end, 1 stopped early, negative on error.
int parseHhMmSsFf(const char* tstr, const char* tend, int* hour, int* minute, int* second, int* micro) {
    const char* p = tstr;
    int* vals[3] = {hour, minute, second};
    bool hasSeparator = true;
    for (size_t i = 0; i < 3; ++i) {
        p = parseDigits(p, vals[i], 2);
        if (!p) return -3;
        const char c = *(p++);
        if (i == 0) hasSeparator = c == ':';
        if (p >= tend) return c != '\0';
        if (hasSeparator && c == ':') continue;
        if (c == '.' || c == ',') break;
        if (!hasSeparator) --p;
        else return -4;
    }
    const size_t remains = static_cast<size_t>(tend - p);
    const size_t toParse = std::min<size_t>(remains, 6);
    if (toParse == 0) return -3;  // CPython reads out of bounds here.
    p = parseDigits(p, micro, toParse);
    if (!p) return -3;
    static const int kCorrection[] = {100000, 10000, 1000, 100, 10};
    if (toParse < 6) *micro *= kCorrection[toParse - 1];
    while (asciiDigit(*p)) ++p;
    return *p != '\0';
}

int64_t daysFromCivil(int64_t y, int m, int d) {
    y -= m <= 2;
    const int64_t era = (y >= 0 ? y : y - 399) / 400;
    const int64_t yoe = y - era * 400;
    const int64_t doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
    const int64_t doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    return era * 146097 + doe - 719468;
}

int daysInMonth(int y, int m) {
    static const int kDays[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
    const bool leap = (y % 4 == 0 && y % 100 != 0) || y % 400 == 0;
    return m == 2 && leap ? 29 : kDays[m - 1];
}

// Epoch millis the way `int(datetime.fromisoformat(s).timestamp() * 1000)`
// computes them, naive read as UTC. Week dates are not supported.
std::optional<int64_t> isoToMs(const std::string& s) {
    if (text::decode(s).size() < 7) return std::nullopt;
    const char* dt = s.c_str();
    const size_t len = s.size();
    size_t sep = 7;
    if (len != 7) {
        if (dt[4] == '-' ? dt[5] == 'W' : dt[4] == 'W') return std::nullopt;
        sep = dt[4] == '-' ? 10 : 8;
    }
    int year = 0, month = 0, day = 0;
    const char* p = parseDigits(dt, &year, 4);
    if (!p) return std::nullopt;
    const bool usesSeparator = *p == '-';
    if (usesSeparator) ++p;
    if (*p == 'W') return std::nullopt;
    p = parseDigits(p, &month, 2);
    if (!p) return std::nullopt;
    if (usesSeparator && *(p++) != '-') return std::nullopt;
    if (!parseDigits(p, &day, 2)) return std::nullopt;

    int hour = 0, minute = 0, second = 0, micro = 0;
    int tzSeconds = 0, tzMicro = 0;
    if (len > sep) {
        const char* time = dt + sep + 1;
        const char* end = dt + len;
        const char* tz = time;
        do {
            if (*tz == 'Z' || *tz == '+' || *tz == '-') break;
        } while (++tz < end);
        const int rv = parseHhMmSsFf(time, tz, &hour, &minute, &second, &micro);
        if (rv < 0) return std::nullopt;
        if (tz == end) {
            if (rv == 1) return std::nullopt;
        } else if (*tz == 'Z') {
            if (*(tz + 1) != '\0') return std::nullopt;
        } else {
            const int sign = *tz == '-' ? -1 : 1;
            int tzh = 0, tzm = 0, tzs = 0;
            const int tzrv = parseHhMmSsFf(tz + 1, end, &tzh, &tzm, &tzs, &tzMicro);
            if (tzrv) return std::nullopt;
            tzSeconds = sign * (tzh * 3600 + tzm * 60 + tzs);
            tzMicro *= sign;
            // timezone() takes any offset strictly inside one day.
            const int64_t offsetUs = static_cast<int64_t>(tzSeconds) * 1000000LL + tzMicro;
            if (offsetUs <= -86400000000LL || offsetUs >= 86400000000LL) return std::nullopt;
        }
    }
    if (year < 1 || month < 1 || month > 12 || day < 1 || day > daysInMonth(year, month)) return std::nullopt;
    if (hour > 23 || minute > 59 || second > 59) return std::nullopt;
    const int64_t localUs =
        ((daysFromCivil(year, month, day) * 86400 + hour * 3600 + minute * 60 + second) * 1000000LL) + micro;
    const int64_t totalUs = localUs - (static_cast<int64_t>(tzSeconds) * 1000000LL + tzMicro);
    const double seconds = static_cast<double>(totalUs) / 1e6;
    return static_cast<int64_t>(seconds * 1000.0);
}

// raw.replace("Z", "+00:00") then _FRACTION_RE.sub(r"\1", ...).
std::string normalizeReceived(const std::string& raw) {
    std::string zulu;
    for (char c : raw) {
        if (c == 'Z') zulu += "+00:00";
        else zulu += c;
    }
    const Cps t = text::decode(zulu);
    Cps out;
    size_t i = 0;
    while (i < t.size()) {
        if (t[i] == '.' && digitsAt(t, i + 1, 7)) {
            out.append(t, i, 7);
            i += 7;
            while (i < t.size() && text::isDigit(t[i])) ++i;
            continue;
        }
        out.push_back(t[i++]);
    }
    return text::encode(out);
}

int64_t nowMs() {
    using namespace std::chrono;
    return duration_cast<milliseconds>(system_clock::now().time_since_epoch()).count();
}

// ---- the scan ----

std::vector<json> fetchThreads(ReadBackend& backend, const json& threadIds) {
    const size_t n = threadIds.size();
    std::vector<json> results(n);
    std::vector<std::exception_ptr> errors(n);
    if (n == 0) return results;
    std::atomic<size_t> next{0};
    const auto work = [&] {
        for (size_t i = next++; i < n; i = next++) {
            try {
                results[i] = backend.getThread(threadIds[i].get<std::string>());
            } catch (...) {
                errors[i] = std::current_exception();
            }
        }
    };
    std::vector<std::thread> pool;
    for (size_t w = 0; w < std::min(kThreadFetchConcurrency, n); ++w) pool.emplace_back(work);
    for (auto& t : pool) t.join();
    for (const auto& error : errors) {
        if (error) std::rethrow_exception(error);
    }
    return results;
}

std::vector<json> sentMessages(const json& thread) {
    std::vector<json> out;
    const json& messages = field(thread, "messages");
    if (!messages.is_array()) return out;
    for (const auto& m : messages) {
        if (!text::truthy(field(m, "draft"))) out.push_back(m);
    }
    return out;
}

std::set<std::string> scanOptedOutSenders(ReadBackend& backend, const std::string& userEmail, int maxSent) {
    const json listing = backend.listThreadIds("sent", maxSent);
    std::set<std::string> optedOut;
    for (const auto& thread : fetchThreads(backend, listing.at("thread_ids"))) {
        for (const auto& msg : sentMessages(thread)) {
            if (detail::senderAddress(text::textField(msg, "from")) != userEmail) continue;
            if (!detail::isOptOutReply(text::lower(detail::plainBody(msg)))) continue;
            for (const auto& a : detail::recipientAddresses(text::textField(msg, "to"))) {
                if (a != userEmail) optedOut.insert(detail::normalizeForSuppression(a));
            }
        }
    }
    return optedOut;
}

std::set<std::string> normalizedRecipients(const std::string& rendered) {
    std::set<std::string> out;
    for (const auto& a : detail::recipientAddresses(rendered)) out.insert(detail::normalizeForSuppression(a));
    return out;
}

std::vector<std::string> labelIds(const json& message) {
    std::vector<std::string> out;
    const json& labels = field(message, "_label_ids");
    if (!labels.is_array()) return out;
    for (const auto& label : labels) out.push_back(label.is_string() ? label.get<std::string>() : label.dump());
    return out;
}

std::optional<std::pair<json, const char*>> qualify(const json& thread, const std::string& userEmail,
                                                    const std::set<std::string>& optedOut) {
    std::vector<json> unsorted = sentMessages(thread);
    std::vector<std::pair<int64_t, size_t>> keyed;
    for (size_t i = 0; i < unsorted.size(); ++i) keyed.emplace_back(detail::receivedMs(unsorted[i]), i);
    std::stable_sort(keyed.begin(), keyed.end(),
                     [](const auto& a, const auto& b) { return a.first < b.first; });
    std::vector<json> messages;
    for (const auto& [ms, i] : keyed) messages.push_back(unsorted[i]);
    if (messages.empty()) return std::nullopt;

    const json& latest = messages.back();
    const std::string senderRaw = text::textField(latest, "from");
    const std::string sender = detail::senderAddress(senderRaw);
    if (sender == userEmail) return std::nullopt;  // The user spoke last.
    if (detail::isAutomatedSender(senderRaw)) return std::nullopt;
    if (optedOut.count(detail::normalizeForSuppression(sender))) return std::nullopt;
    if (detail::isAutoReply(latest)) return std::nullopt;

    const auto to = normalizedRecipients(text::textField(latest, "to"));
    const auto cc = normalizedRecipients(text::textField(latest, "cc"));
    const std::string userAddress = detail::normalizeForSuppression(userEmail);
    if (!to.count(userAddress) && cc.count(userAddress)) return std::nullopt;  // Asked of someone else.

    const std::vector<json> earlierMessages(messages.begin(), messages.end() - 1);
    const std::string subject = text::textField(latest, "subject");
    const std::string body = detail::newText(latest, detail::quotedLines(earlierMessages));
    if (detail::isConfidentlyPromotional(subject, body, labelIds(latest))) return std::nullopt;

    const std::string subjectLower = text::lower(subject);
    const std::string bodyLower = text::lower(body);
    const char* signal = nullptr;
    if (detail::hasDirectAskSignal(subjectLower, bodyLower)) {
        signal = kSignalDirectAsk;
    } else if (detail::hasMeetingTimeSignal(subjectLower, bodyLower) || detail::isMeetingRequest(subject, body)) {
        signal = kSignalMeetingTime;
    } else {
        return std::nullopt;
    }

    // Only the user's own earlier messages count toward the conversation bar.
    std::vector<std::string> priorBodies;
    for (const auto& msg : earlierMessages) {
        if (detail::senderAddress(text::textField(msg, "from")) != userEmail) continue;
        std::string plain = detail::plainBody(msg);
        if (detail::isOptOutReply(text::lower(plain))) return std::nullopt;  // Told to stop, in this thread.
        priorBodies.push_back(std::move(plain));
    }
    if (priorBodies.size() < 2 && !(!priorBodies.empty() && detail::isSubstantiveText(priorBodies[0]))) {
        return std::nullopt;
    }
    return std::make_pair(latest, signal);
}

json getOr(const json& obj, const char* key, const json& fallback) {
    if (obj.is_object()) {
        const auto it = obj.find(key);
        if (it != obj.end()) return *it;
    }
    return fallback;
}

int64_t floorDiv(int64_t a, int64_t b) {
    const int64_t q = a / b;
    return (a % b != 0 && ((a < 0) != (b < 0))) ? q - 1 : q;
}

}  // namespace

namespace detail {

bool hasDirectAskSignal(const std::string& subjectLower, const std::string& bodyLower) {
    return anyIn(subjectLower + "\n" + bodyLower, kDirectAskPhrases);
}

bool hasMeetingTimeSignal(const std::string& subjectLower, const std::string& bodyLower) {
    const std::string joined = subjectLower + "\n" + bodyLower;
    if (!anyIn(joined, kMeetingVerbPhrases)) return false;
    return !timeMatches(text::decode(joined), kVerbTime, true).empty();
}

bool isMeetingRequest(const std::string& subject, const std::string& body) {
    const std::string joined = text::lower(subject + "\n" + body);
    if (anyIn(joined, kInvitePhrases)) return true;
    const Cps t = text::decode(joined);
    if (nounNearTime(t)) return true;
    return anyIn(joined, kSlotProposalPhrases) && !timeMatches(t, kTime, true).empty();
}

bool isOptOutReply(const std::string& bodyLower) { return anyIn(bodyLower, kOptOutPhrases); }

bool isSubstantiveText(const std::string& body) { return text::split(body).size() >= kMinSubstantiveWords; }

bool isAutomatedSender(const std::string& sender) { return anyIn(text::lower(sender), kAutomatedSenderKeywords); }

bool isConfidentlyPromotional(const std::string& subject, const std::string& body,
                              const std::vector<std::string>& labelIds) {
    const std::set<std::string> labels(labelIds.begin(), labelIds.end());
    const std::string subjectLower = text::lower(subject);
    const std::string bodyLower = text::lower(body);
    const auto intersects = [&](const std::set<std::string>& set) {
        return std::any_of(labels.begin(), labels.end(), [&](const std::string& l) { return set.count(l) > 0; });
    };
    if (intersects(kPromoLabels)) {
        return !(anyIn(subjectLower, kCommitmentSignalPatterns) || anyIn(bodyLower, kCommitmentSignalPatterns));
    }
    if (intersects(kNonPromoLabels)) return false;
    return anyIn(subjectLower, kPromoSubjectKeywords);
}

std::string normalizeForSuppression(const std::string& addr) {
    const std::string a = text::lower(text::strip(addr));
    const size_t at = a.find('@');
    const std::string local = at == std::string::npos ? a : a.substr(0, at);
    const std::string rest = at == std::string::npos ? "" : a.substr(at);
    return local.substr(0, local.find('+')) + rest;
}

std::string senderAddress(const std::string& rendered) {
    std::string addr = text::parseaddr(rendered).addr;
    if (addr.empty()) addr = rendered;
    return text::lower(text::strip(addr));
}

std::vector<std::string> recipientAddresses(const std::string& rendered) {
    std::vector<std::string> out;
    for (const auto& parsed : text::getaddresses(rendered)) {
        const std::string addr = text::lower(text::strip(parsed.addr));
        if (!addr.empty() && std::find(out.begin(), out.end(), addr) == out.end()) out.push_back(addr);
    }
    return out;
}

std::string plainBody(const json& message) {
    const std::string body = text::textField(message, "body");
    if (!isHtml(message)) return body;
    return text::htmlUnescape(text::encode(stripTags(text::decode(body))));
}

std::set<std::string> quotedLines(const std::vector<json>& messages) {
    std::set<std::string> seen;
    for (const auto& message : messages) {
        for (const auto& line : textLines(message, false)) {
            std::string normalized = norm(line);
            if (!normalized.empty()) seen.insert(std::move(normalized));
        }
    }
    return seen;
}

std::string stripQuotedText(const std::string& body, const std::set<std::string>& earlier) {
    const auto lines = text::splitlines(body);
    std::vector<std::string> following(lines.size());
    std::string upcoming;
    for (size_t index = lines.size(); index-- > 0;) {
        following[index] = upcoming;
        std::string stripped = text::strip(lines[index]);
        if (!stripped.empty()) upcoming = std::move(stripped);
    }

    std::vector<std::string> kept;
    bool belowMarker = false;
    bool inHeader = false;
    for (size_t index = 0; index < lines.size(); ++index) {
        const std::string stripped = text::strip(lines[index]);
        if (!stripped.empty() && stripped[0] == '>') continue;
        const Cps t = text::decode(stripped);
        if (isAttribution(t) || isOriginalMessage(t)) {
            belowMarker = true;
            inHeader = false;
            continue;
        }
        if (headerWithValue(t, {"from:"}) && headerWithValue(text::decode(following[index]), {"sent:", "date:"})) {
            belowMarker = true;
            inHeader = true;
            continue;
        }
        if (inHeader) {
            if (isHeaderField(t)) continue;
            inHeader = false;
        }
        if (belowMarker && !stripped.empty() && earlier.count(norm(stripped))) continue;
        kept.push_back(lines[index]);
    }
    std::string joined;
    for (size_t i = 0; i < kept.size(); ++i) {
        if (i) joined += '\n';
        joined += kept[i];
    }
    return text::strip(joined);
}

std::string newText(const json& message, const std::set<std::string>& earlier) {
    std::string joined;
    const auto lines = textLines(message, true);
    for (size_t i = 0; i < lines.size(); ++i) {
        if (i) joined += '\n';
        joined += lines[i];
    }
    return stripQuotedText(joined, earlier);
}

bool isAutoReply(const json& message) {
    const std::string autoSubmitted = text::lower(text::strip(text::textField(message, "auto_submitted")));
    const std::string xAutoreply = text::lower(text::strip(text::textField(message, "x_autoreply")));
    return (!autoSubmitted.empty() && autoSubmitted != "no") || xAutoreply == "yes" || xAutoreply == "true";
}

int64_t receivedMs(const json& message) {
    const std::string raw = text::strip(text::textField(message, "received"));
    const std::string id = text::pyRepr(field(message, "id"));
    if (raw.empty()) {
        throw MailboxError("message " + id +
                           " has no received time, so its age cannot be computed. Retry; report it at "
                           "https://github.com/amd/gaia/issues if it repeats.");
    }
    const auto ms = isoToMs(normalizeReceived(raw));
    if (!ms) {
        throw MailboxError("message " + id + " has an unreadable received time " + text::pyRepr(raw) +
                           ". Report it at https://github.com/amd/gaia/issues.");
    }
    return *ms;
}

}  // namespace detail

json detail::detectWaitingOnYouAt(ReadBackend& backend, int maxInbox, int minAgeHours, int64_t now) {
    if (minAgeHours < 0) {
        throw std::invalid_argument("min_age_hours must be >= 0, got " + std::to_string(minAgeHours));
    }
    if (maxInbox < 1) throw std::invalid_argument("max_inbox must be >= 1, got " + std::to_string(maxInbox));

    const std::string userEmail = text::lower(text::strip(backend.getUserEmail()));
    const auto optedOut = scanOptedOutSenders(backend, userEmail, kDefaultMaxSentLookback);
    const json listing = backend.listThreadIds("inbox", maxInbox);
    const json& threadIds = listing.at("thread_ids");
    const auto threads = fetchThreads(backend, threadIds);

    const int64_t minAgeMs = static_cast<int64_t>(minAgeHours) * 60 * 60 * 1000;
    struct Flagged {
        int64_t ageMs;
        json item;
        json latest;
    };
    std::vector<Flagged> flagged;
    json unchecked = json::array();
    for (size_t i = 0; i < threads.size(); ++i) {
        const json& threadId = threadIds[i];
        if (text::truthy(field(threads[i], "truncated"))) {
            // Only part of the conversation came back, so its latest message is unknown.
            unchecked.push_back(threadId);
            continue;
        }
        auto verdict = qualify(threads[i], userEmail, optedOut);
        if (!verdict) continue;
        const auto& [latest, signal] = *verdict;
        const int64_t ageMs = now - detail::receivedMs(latest);
        if (ageMs < minAgeMs) continue;
        json item = {
            {"message_id", getOr(latest, "id", nullptr)},
            {"thread_id", threadId},
            {"from", getOr(latest, "from", "")},
            {"subject", getOr(latest, "subject", "")},
            {"received", getOr(latest, "received", "")},
            {"age_days", floorDiv(ageMs, kDayMs)},
            {"signal", signal},
            {"corroboration", kCorroborationThreadReply},
        };
        flagged.push_back({ageMs, std::move(item), latest});
    }

    std::stable_sort(flagged.begin(), flagged.end(), [](const Flagged& a, const Flagged& b) { return a.ageMs > b.ageMs; });
    json waiting = json::array();
    json latestMessages = json::array();
    for (auto& f : flagged) {
        waiting.push_back(std::move(f.item));
        latestMessages.push_back(std::move(f.latest));
    }
    return {
        {"waiting_on_you", waiting},
        {"latest_messages", latestMessages},
        {"inbox_scanned", listing.at("messages_listed")},
        {"threads_checked", static_cast<int64_t>(threadIds.size()) - static_cast<int64_t>(unchecked.size())},
        {"threads_unchecked", unchecked},
        {"scan_truncated", listing.at("truncated")},
    };
}

json detectWaitingOnYou(ReadBackend& backend, int maxInbox, int minAgeHours) {
    return detail::detectWaitingOnYouAt(backend, maxInbox, minAgeHours, nowMs());
}

}  // namespace gaia::mail
