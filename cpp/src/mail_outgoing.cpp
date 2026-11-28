// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Ported from src/gaia/agents/tools/_email/outgoing.py. Lengths are code
// points, as Python counts them.

#include "mail_outgoing.h"

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <set>
#include <stdexcept>

#include "gaia/idna.h"
#include "mail_html.h"
#include "mail_mime.h"
#include "mail_text.h"

namespace gaia::mail {

using text::Cps;
using text::isAscii;
using text::join;

namespace {

bool isCategoryCc(char32_t c) { return c < 0x20 || (c >= 0x7F && c <= 0x9F); }

// unicodedata.category(c) == "Cf", Unicode 15.0 (Python 3.12).
bool isCategoryCf(char32_t c) {
    static const std::pair<char32_t, char32_t> kCf[] = {
        {0xAD, 0xAD},       {0x600, 0x605},     {0x61C, 0x61C},     {0x6DD, 0x6DD},   {0x70F, 0x70F},
        {0x890, 0x891},     {0x8E2, 0x8E2},     {0x180E, 0x180E},   {0x200B, 0x200F}, {0x202A, 0x202E},
        {0x2060, 0x2064},   {0x2066, 0x206F},   {0xFEFF, 0xFEFF},   {0xFFF9, 0xFFFB}, {0x110BD, 0x110BD},
        {0x110CD, 0x110CD}, {0x13430, 0x1343F}, {0x1BCA0, 0x1BCA3}, {0x1D173, 0x1D17A}, {0xE0001, 0xE0001},
        {0xE0020, 0xE007F}};
    for (const auto& [lo, hi] : kCf) {
        if (c >= lo && c <= hi) return true;
    }
    return false;
}

std::string repr(const std::string& s) { return text::pyRepr(json(s)); }

std::string hex4(char32_t c) {
    static const char* kHex = "0123456789ABCDEF";
    std::string out;
    unsigned v = static_cast<unsigned>(c);
    int digits = 4;
    while ((v >> (4 * digits)) != 0) ++digits;
    for (int i = digits - 1; i >= 0; --i) out += kHex[(v >> (4 * i)) & 0xF];
    return out;
}

std::string replaceAll(std::string s, const std::string& from, const std::string& to) {
    size_t at = 0;
    while ((at = s.find(from, at)) != std::string::npos) {
        s.replace(at, from.size(), to);
        at += to.size();
    }
    return s;
}

// ---- checks ----

void refuseControls(const std::string& value, const std::string& field) {
    for (char32_t c : text::decode(value)) {
        if (c != U'\t' && (isCategoryCc(c) || c == 0x2028 || c == 0x2029)) {
            throw std::invalid_argument("`" + field + "` contains a line break or control character (U+" + hex4(c) +
                                        "), which could disguise what is sent. Remove it and try again.");
        }
    }
}

// _ADDRESS_RE: [^@\s<>",;]+@[^@\s<>",;]+
bool looksLikeAddress(const std::string& address) {
    const Cps cps = text::decode(address);
    size_t at = 0, ats = 0;
    for (size_t i = 0; i < cps.size(); ++i) {
        const char32_t c = cps[i];
        if (c == U'@') {
            ++ats;
            at = i;
        } else if (text::isSpace(c) || c == U'<' || c == U'>' || c == U'"' || c == U',' || c == U';') {
            return false;
        }
    }
    return ats == 1 && at > 0 && at + 1 < cps.size();
}

// RFC 5322 dot-atom: atext runs joined by single dots.
bool isDotAtom(const std::string& part) {
    static const std::string kAtextPunct = "!#$%&'*+-/=?^_`{|}~";
    if (part.empty() || part.front() == '.' || part.back() == '.' || part.find("..") != std::string::npos) return false;
    return std::all_of(part.begin(), part.end(), [](char c) {
        return std::isalnum(static_cast<unsigned char>(c)) || c == '.' || kAtextPunct.find(c) != std::string::npos;
    });
}

// Python's MIME refuses these at send time; GAIA refuses them before the card.
void refuseUnparseable(const std::string& address, const std::string& local, const std::string& domain,
                       const std::string& field) {
    const char* defect = nullptr;
    if (!isDotAtom(local) || mime::startsWithEncodedWord(address)) {
        defect = "local-part is not dot-atom, quoted-string, or obs-local-part";
    } else if (!isDotAtom(domain) || mime::startsWithEncodedWord(domain)) {
        defect = "domain is not a dot-atom";
    }
    if (defect) {
        throw std::invalid_argument("`" + field + "` has an address that can't be sent to: " + repr(address) + " (" +
                                    defect + "). Use name@example.com, separating several with commas.");
    }
}

std::string sendableAddress(const std::string& address, const std::string& field) {
    if (!looksLikeAddress(address)) {
        throw std::invalid_argument("`" + field + "` has an address that can't be sent to: " + repr(address) +
                                    ". Use name@example.com, separating several with commas.");
    }
    const size_t at = address.rfind('@');
    const std::string local = address.substr(0, at), domain = address.substr(at + 1);
    if (!isAscii(local)) {
        throw std::invalid_argument("`" + field + "` has non-ASCII characters before the @ in " + repr(address) +
                                    ", which GAIA can't send. Check the address with the recipient.");
    }
    if (isAscii(domain)) {
        refuseUnparseable(address, local, domain, field);
        return address;
    }
    std::string ascii;
    try {
        ascii = idna::encodeUts46(text::decode(domain));
    } catch (const idna::IdnaError& e) {
        throw std::invalid_argument("`" + field + "` has a domain that can't be sent to: " + repr(domain) + " (" +
                                    e.what() + "). Check the spelling.");
    }
    refuseUnparseable(address, local, ascii, field);
    return local + "@" + ascii;
}

Recipient sendableRecipient(const Recipient& recipient, const std::string& field) {
    std::string name = recipient.name;
    refuseControls(name, field);
    refuseControls(recipient.address, field);
    if (text::lower(name) == text::lower(recipient.address)) name.clear();
    const Cps cps = text::decode(name);
    if (std::any_of(cps.begin(), cps.end(), isCategoryCf)) {
        throw std::invalid_argument("`" + field +
                                    "` has a display name with invisible or direction-changing characters, so it "
                                    "may not read as it looks: " +
                                    recipient.address + ". Give the bare address instead.");
    }
    if (name.find_first_of("@<>") != std::string::npos) {
        throw std::invalid_argument("`" + field +
                                    "` has a display name that looks like an address but isn't the one receiving "
                                    "the mail, " +
                                    recipient.address + ". Give the bare address instead.");
    }
    return {name, sendableAddress(recipient.address, field)};
}

Recipients sendableAll(const Recipients& recipients, const std::string& field) {
    Recipients out;
    for (const auto& r : recipients) out.push_back(sendableRecipient(r, field));
    return out;
}

std::string prefixed(const std::string& prefix, const std::string& subject) {
    const std::string lowered = text::lower(subject), p = text::lower(prefix);
    if (lowered.compare(0, p.size(), p) == 0) return subject;
    return text::strip(prefix + " " + subject);
}

Recipients bare(const Recipients& recipients) {
    Recipients out;
    for (const auto& r : recipients) out.push_back({"", r.address});
    return out;
}

Recipients without(const Recipients& recipients, std::set<std::string> seen) {
    Recipients kept;
    for (const auto& r : recipients) {
        const std::string key = text::lower(r.address);
        if (seen.insert(key).second) kept.push_back(r);
    }
    return kept;
}

std::string plainText(const SourceMessage& source) {
    return source.bodyType == "html" ? htmlText(source.body) : source.body;
}

// ---- the preview ----

// _HIDDEN_RE: controls but tab and newline, the line and paragraph
// separators, and the bidi embeddings and isolates.
bool isHidden(char32_t c) {
    return (c <= 0x08) || (c >= 0x0B && c <= 0x1F) || (c >= 0x7F && c <= 0x9F) || c == 0x2028 || c == 0x2029 ||
           (c >= 0x202A && c <= 0x202E) || (c >= 0x2066 && c <= 0x2069);
}

const char* const kHiddenNote = "Hidden characters are shown as [U+XXXX]. They are sent as they are.";

std::string visible(const std::string& s) {
    std::string out;
    for (char32_t c : text::decode(replaceAll(s, "\r\n", "\n"))) {
        out += isHidden(c) ? "[U+" + hex4(c) + "]" : text::encode(c);
    }
    return out;
}

std::string visibleLine(const std::string& s) { return replaceAll(visible(s), "\n", "[U+000A]"); }

bool hasHidden(const std::string& s) {
    const Cps cps = text::decode(replaceAll(s, "\r\n", "\n"));
    return std::any_of(cps.begin(), cps.end(), isHidden);
}

std::string names(const Recipients& recipients) {
    std::vector<std::string> parts;
    for (const auto& r : recipients) parts.push_back(r.str());
    return visibleLine(join(parts, ", "));
}

json strings(const Recipients& recipients) {
    json out = json::array();
    for (const auto& r : recipients) out.push_back(r.str());
    return out;
}

json optionalString(const std::optional<std::string>& s) { return s ? json(*s) : json(); }

}  // namespace

std::string Recipient::str() const {
    if (name.empty() || text::lower(name) == text::lower(address)) return address;
    return "\"" + name + "\" <" + address + ">";
}

bool OutgoingMessage::operator==(const OutgoingMessage& o) const {
    const bool sameReplaces = (!replaces && !o.replaces) || (replaces && o.replaces && *replaces == *o.replaces);
    return to == o.to && cc == o.cc && bcc == o.bcc && subject == o.subject && body == o.body &&
           bodyType == o.bodyType && threadId == o.threadId && inReplyTo == o.inReplyTo &&
           references == o.references && replyToId == o.replyToId && attachments == o.attachments &&
           attachmentsLeftOut == o.attachmentsLeftOut && htmlAlternative == o.htmlAlternative && sameReplaces;
}

namespace outgoing {

OutgoingMessage checked(OutgoingMessage message) {
    refuseControls(message.subject, "subject");
    refuseControls(message.inReplyTo.value_or(""), "in_reply_to");
    refuseControls(message.references.value_or(""), "references");
    message.to = sendableAll(message.to, "to");
    message.cc = sendableAll(message.cc, "cc");
    message.bcc = sendableAll(message.bcc, "bcc");
    return message;
}

Recipients parseRecipients(const std::string& raw, const std::string& field) {
    const std::string value = text::strip(raw);
    if (value.empty()) return {};
    refuseControls(value, field);
    Recipients parsed;
    for (const auto& a : text::getaddresses(value)) parsed.push_back(sendableRecipient({text::strip(a.name), a.addr}, field));
    if (parsed.empty()) throw std::invalid_argument("`" + field + "` has no usable address: " + repr(value) + ".");
    return parsed;
}

void requireRecipient(const OutgoingMessage& message) {
    if (message.to.empty() && message.cc.empty() && message.bcc.empty()) {
        throw std::invalid_argument("This message has no recipients, so it can't be sent. Add one first.");
    }
}

OutgoingMessage newMessage(const std::string& to, const std::string& subject, const std::string& body,
                           const std::string& cc, const std::string& bcc) {
    OutgoingMessage m;
    m.to = parseRecipients(to, "to");
    m.cc = parseRecipients(cc, "cc");
    m.bcc = parseRecipients(bcc, "bcc");
    m.subject = subject;
    m.body = body;
    return m;
}

OutgoingMessage replyMessage(const SourceMessage& source, const std::string& body, const std::string& ownAddress,
                             bool replyAll) {
    const std::string own = text::lower(ownAddress);
    const bool ourselves = source.sender && text::lower(source.sender->address) == own;
    Recipients to;
    if (!source.replyTo.empty()) {
        to = source.replyTo;
    } else if (ourselves) {
        to = source.to;
    } else if (source.sender) {
        to = {*source.sender};
    }
    Recipients cc;
    if (replyAll) {
        if (!ourselves) to.insert(to.end(), source.to.begin(), source.to.end());
        to = without(to, {own});
        std::set<std::string> excluded{own};
        for (const auto& r : to) excluded.insert(text::lower(r.address));
        cc = without(source.cc, excluded);
    }
    if (to.empty() && cc.empty()) {
        throw std::invalid_argument("Message " + repr(source.id) +
                                    " names nobody to reply to. Use send-email/send_now with the address you want.");
    }
    std::vector<std::string> chain;
    for (const auto& part : {source.references, source.internetMessageId}) {
        if (part && !part->empty()) chain.push_back(*part);
    }
    OutgoingMessage m;
    m.to = bare(to);
    m.cc = bare(cc);
    m.subject = prefixed("Re:", source.subject);
    m.body = body;
    m.threadId = source.threadId;
    m.inReplyTo = source.internetMessageId;
    if (!chain.empty()) m.references = join(chain, " ");
    m.replyToId = source.id;
    return m;
}

OutgoingMessage forwardMessage(const SourceMessage& source, const std::string& to, const std::string& note,
                               const std::string& cc, const std::string& bcc) {
    const std::string sender = source.sender ? source.sender->str() : "(unknown sender)";
    std::vector<std::string> recipients;
    for (const auto& r : source.to) recipients.push_back(r.str());
    const std::string quoted = "----- Forwarded message -----\nFrom: " + sender + "\nDate: " + source.received +
                               "\nSubject: " + source.subject + "\nTo: " + join(recipients, ", ") + "\n\n" +
                               plainText(source);
    OutgoingMessage m = newMessage(to, prefixed("Fwd:", source.subject), note.empty() ? quoted : note + "\n\n" + quoted,
                                   cc, bcc);
    m.attachmentsLeftOut = source.attachments;
    return m;
}

std::string renderPreview(const OutgoingMessage& message) {
    std::vector<std::string> lines;
    if (message.sender && !message.sender->empty()) lines.push_back("From: " + visibleLine(*message.sender));
    lines.push_back("To: " + names(message.to));
    if (!message.cc.empty()) lines.push_back("Cc: " + names(message.cc));
    if (!message.bcc.empty()) lines.push_back("Bcc: " + names(message.bcc));
    lines.push_back("Subject: " + visibleLine(message.subject));
    if (!message.attachments.empty()) lines.push_back("Attachments: " + visibleLine(join(message.attachments, ", ")));
    if (!message.attachmentsLeftOut.empty()) {
        lines.push_back("Attachments left out, not sent: " + visibleLine(join(message.attachmentsLeftOut, ", ")));
    }
    if (message.bodyType == "html") lines.push_back("Format: HTML");
    const std::string html = message.htmlAlternative.value_or("");
    std::string shown = join(lines, "\n") + "\n\n" + visible(message.body);
    if (!html.empty()) {
        shown += "\n\n----- HTML version, which most mail apps show instead of the text above -----\n" + visible(html);
    }
    if (hasHidden(message.subject) || hasHidden(message.body) || hasHidden(html)) {
        shown = std::string(kHiddenNote) + "\n\n" + shown;
    }
    if (message.replaces) shown += "\n\n----- This replaces the saved draft -----\n" + renderPreview(*message.replaces);
    return shown;
}

json describe(const OutgoingMessage& message) {
    return {{"to", strings(message.to)},
            {"cc", strings(message.cc)},
            {"bcc", strings(message.bcc)},
            {"subject", message.subject},
            {"body", message.body},
            {"thread_id", optionalString(message.threadId)},
            {"attachments", message.attachments},
            {"attachments_left_out", message.attachmentsLeftOut}};
}

}  // namespace outgoing

}  // namespace gaia::mail
