// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Mailbox backends for the email tools: Gmail and Microsoft Graph behind one
// provider-neutral shape, ported from src/gaia/agents/tools/_email/. A message
// is a JSON object with the same keys the Python backends return, so the tools
// and the model see the same thing whichever runtime answered. Keys that start
// with "_" are backend-private (the waiting-on-you detector reads them) and are
// stripped before a message reaches the model.

#pragma once

#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

#include "gaia/export.h"
#include "gaia/http_client.h"
#include "gaia/types.h"

namespace gaia::mail {

constexpr const char* kGoogle = "google";
constexpr const char* kMicrosoft = "microsoft";

// ---- errors (_email/errors.py) ----

/// A mailbox request failed in a way the caller should surface verbatim.
/// `connect` names the connectors signing in fixes, `reconnect` those of them
/// already connected, `scopes` the access a failed call needs when that is more
/// than reading, and `setup` marks a call stopped before it reached the mailbox.
class GAIA_API MailboxError : public std::runtime_error {
public:
    explicit MailboxError(const std::string& message) : std::runtime_error(message) {}
    std::vector<std::string> connect;
    std::vector<std::string> reconnect;
    std::map<std::string, std::vector<std::string>> scopes;
    bool setup = false;
};

/// The mailbox rejected the credentials or refused the requested scope. The
/// tools re-resolve the mailbox once on this before surfacing it.
class GAIA_API MailboxAuthError : public MailboxError {
public:
    using MailboxError::MailboxError;
};

/// A write went out and no clear answer came back, so it may have taken effect.
class GAIA_API MailboxOutcomeUnknown : public MailboxError {
public:
    using MailboxError::MailboxError;
};

/// What a Transport throws when no answer arrived. `kind` names the failure as
/// httpx does (ConnectError, ReadTimeout, ...); `neverSent` means the request
/// never left this PC, so a write certainly did not happen.
class GAIA_API TransportError : public std::runtime_error {
public:
    TransportError(std::string kind, bool neverSent, const std::string& message)
        : std::runtime_error(message), kind(std::move(kind)), neverSent(neverSent) {}
    std::string kind;
    bool neverSent;
};

// ---- a message on its way out (_email/outgoing.py) ----

struct GAIA_API Recipient {
    std::string name;
    std::string address;
    /// `"name" <address>`, or the bare address when the name adds nothing.
    std::string str() const;
    bool operator==(const Recipient& o) const { return name == o.name && address == o.address; }
};

using Recipients = std::vector<Recipient>;

/// What both backends send and the approval card shows, so the two can't differ.
struct GAIA_API OutgoingMessage {
    Recipients to, cc, bcc;
    std::string subject;
    std::string body;
    std::string bodyType = "text";  // "text" or "html"
    std::optional<std::string> threadId;
    std::optional<std::string> inReplyTo;
    std::optional<std::string> references;
    /// Graph threads a reply through its source message, not through headers.
    std::optional<std::string> replyToId;
    /// Files a saved draft already carries.
    std::vector<std::string> attachments;
    /// A forward carries text only; these files on the original stay behind.
    std::vector<std::string> attachmentsLeftOut;
    /// The HTML half of a multipart draft, which most mail apps show first.
    std::optional<std::string> htmlAlternative;
    /// The saved draft an edit overwrites. Shown on its card, never sent.
    std::shared_ptr<const OutgoingMessage> replaces;
    /// The account it goes out from. Not part of equality: a fetched draft doesn't carry it.
    std::optional<std::string> sender;

    bool operator==(const OutgoingMessage& o) const;
    bool operator!=(const OutgoingMessage& o) const { return !(*this == o); }
};

/// What a reply or forward is built from, read once from the mailbox.
struct GAIA_API SourceMessage {
    std::string id;
    std::optional<Recipient> sender;
    Recipients replyTo, to, cc;
    std::string subject;
    std::string body;
    std::string bodyType = "text";
    std::optional<std::string> threadId;
    std::optional<std::string> internetMessageId;
    std::optional<std::string> references;
    std::string received;
    std::vector<std::string> attachments;
};

// ---- HTTP ----

struct HttpCall {
    std::string method;  // GET, POST, PUT, PATCH, DELETE
    std::string url;     // absolute, query string included
    HttpHeaders headers;
    std::string body;
};

/// Sends one call and returns whatever status came back; throws only when no
/// answer arrived (network down, TLS missing). Tests substitute a fake.
using Transport = std::function<HttpResponse(const HttpCall&)>;

/// The real HTTPS transport (OpenSSL), 15 s per request like the Python backends.
GAIA_API Transport httpsTransport();

/// `?a=1&b=2` from ordered pairs, percent-encoded. A key may repeat.
GAIA_API std::string queryString(const std::vector<std::pair<std::string, std::string>>& params);

/// Returns a bearer access token for the mailbox, minted on each call.
using TokenSource = std::function<std::string()>;

// ---- backends (_email/gmail.py, _email/graph.py) ----

/// The read half of a mailbox. Every method throws MailboxError (or
/// MailboxAuthError) with the provider's own wording on failure.
class GAIA_API ReadBackend {
public:
    virtual ~ReadBackend() = default;
    virtual std::string getUserEmail() = 0;
    /// [{name, unread, total, ...}]
    virtual json listFolders(int limit) = 0;
    /// Message summaries, newest first.
    virtual json listInbox(int limit, bool unreadOnly) = 0;
    /// Message summaries in relevance order.
    virtual json search(const std::string& query, int limit) = 0;
    /// One message with its body.
    virtual json getMessage(const std::string& messageId) = 0;
    /// {messages: [...oldest first, bodies included], truncated: bool}
    virtual json getThread(const std::string& threadId) = 0;
    /// {thread_ids: [...], messages_listed: int, truncated: bool}; folder is "inbox" or "sent".
    virtual json listThreadIds(const std::string& folder, int limit) = 0;
    /// [{draft_id, thread_id, to, subject, preview}], bodies left out.
    virtual json listDrafts(int limit) = 0;
};

/// Drafting and sending. Each call is made once; a failure is reported, not
/// retried, and MailboxOutcomeUnknown means it may have happened.
class GAIA_API WriteBackend {
public:
    virtual ~WriteBackend() = default;
    virtual std::string getUserEmail() = 0;
    /// Whether rewriting a draft keeps the files it carries.
    virtual bool editKeepsAttachments() const = 0;
    /// One draft, exactly as the mailbox would send it now.
    virtual OutgoingMessage getDraft(const std::string& draftId) = 0;
    virtual SourceMessage getSourceMessage(const std::string& messageId) = 0;
    /// The new draft's id.
    virtual std::string createDraft(const OutgoingMessage& message) = 0;
    virtual void updateDraft(const std::string& draftId, const OutgoingMessage& message) = 0;
    virtual void discardDraft(const std::string& draftId) = 0;
    /// {message_id, thread_id}, null where the provider names none.
    virtual json sendDraft(const std::string& draftId) = 0;
    virtual json send(const OutgoingMessage& message) = 0;
};

GAIA_API std::unique_ptr<ReadBackend> makeGmailReadBackend(TokenSource token, Transport transport);
GAIA_API std::unique_ptr<ReadBackend> makeOutlookReadBackend(TokenSource token, Transport transport);
GAIA_API std::unique_ptr<WriteBackend> makeGmailWriteBackend(TokenSource token, Transport transport);
GAIA_API std::unique_ptr<WriteBackend> makeOutlookWriteBackend(TokenSource token, Transport transport);

// ---- phishing screen (_email/phishing.py) ----

/// What the model is told when any message in a result is flagged.
GAIA_API extern const char* const kSuspiciousGuidance;

/// `message` with `suspicious: true` and `suspicious_reasons` added when it
/// looks like a phishing lure; unchanged otherwise.
GAIA_API json annotateSuspicious(const json& message);

/// `body` inside the <<<UNTRUSTED_EMAIL_BODY_*>>> markers.
GAIA_API std::string wrapUntrustedBody(const std::string& body);

// ---- waiting on you (_email/waiting.py) ----

constexpr int kDefaultMaxInboxScan = 50;
constexpr int kMaxInboxScanCap = 200;

/// {waiting_on_you: [...], latest_messages: [...], inbox_scanned, threads_checked,
///  scan_truncated, threads_unchecked: [...]}; `latest_messages` lines up with
/// `waiting_on_you` and still carries the private keys.
GAIA_API json detectWaitingOnYou(ReadBackend& backend, int maxInbox, int minAgeHours);

}  // namespace gaia::mail
