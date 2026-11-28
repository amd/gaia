// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Ported from src/gaia/agents/tools/_calendar/google.py and graph.py.

#include <algorithm>

#include "calendar_internal.h"
#include "mail_internal.h"
#include "mail_text.h"

namespace gaia::calendar {

namespace {

using mail::HttpCall;
using mail::detail::field;
using mail::detail::Params;
using mail::detail::percentEncode;
using mail::detail::truthy;

constexpr const char* kGoogle = "google";
constexpr const char* kMicrosoft = "microsoft";

std::string str(const json& v) { return v.is_string() ? v.get<std::string>() : ""; }

std::string repr(const json& v) { return mail::text::pyRepr(v); }

std::optional<std::string> optStr(const json& v) {
    const std::string s = str(v);
    return s.empty() ? std::nullopt : std::optional<std::string>(s);
}

json parsedBody(const HttpResponse& response) {
    json parsed = json::parse(response.body, nullptr, false);
    return parsed.is_discarded() ? json() : parsed;
}

std::string eventPathSegment(const std::string& eventId) {
    if (eventId == "." || eventId == "..") {
        throw CalendarError(repr(json(eventId)) + " is not an event id. Use the id from list_calendar_events.");
    }
    return percentEncode(eventId);
}

std::string utcNoZone(const Moment& m) { return isoformat(withOffset(m, 0), true).substr(0, 19); }

// ---- Google (google.py) ----

const std::string kGoogleBase = "https://www.googleapis.com/calendar/v3";
const std::string kEventsPath = "/calendars/primary/events";
constexpr const char* kEnableUrl = "https://console.cloud.google.com/apis/library/calendar-json.googleapis.com";
constexpr int kGoogleMaxPage = 250;
constexpr int kMaxPages = 50;

std::string googleReason(const HttpResponse& response) {
    const json parsed = parsedBody(response);
    const json& errors = field(field(parsed, "error"), "errors");
    if (!errors.is_array() || errors.empty()) return "";
    return str(field(errors[0], "reason"));
}

Event parseGoogleEvent(const json& raw) {
    const json& startObj = field(raw, "start");
    const json& endObj = field(raw, "end");
    const json start = truthy(startObj) ? startObj : json::object();
    const json end = truthy(endObj) ? endObj : json::object();
    const bool allDay = start.is_object() && start.contains("date");
    const std::string startValue = truthy(field(start, "date")) ? str(field(start, "date")) : str(field(start, "dateTime"));
    const std::string endValue = truthy(field(end, "date")) ? str(field(end, "date")) : str(field(end, "dateTime"));
    if (!truthy(field(raw, "id")) || startValue.empty() || endValue.empty()) {
        const std::string id = truthy(field(raw, "id")) ? mail::detail::pyStr(raw["id"]) : "none";
        throw CalendarError(
            "Google Calendar returned an event with no id or times, so it can't be read safely (event id: " + id + ").");
    }
    const json& listed = field(raw, "attendees");
    const json attendees = listed.is_array() ? listed : json::array();
    json me = json::object();
    for (const auto& a : attendees) {
        if (truthy(field(a, "self"))) {
            me = a;
            break;
        }
    }
    const bool cancelled = str(field(raw, "status")) == "cancelled";
    Event e;
    e.id = str(raw["id"]);
    e.title = str(field(raw, "summary"));
    e.start = startValue;
    e.end = endValue;
    e.allDay = allDay;
    e.timeZone = optStr(field(start, "timeZone"));
    e.location = optStr(field(raw, "location"));
    for (const auto& a : attendees) {
        if (truthy(field(a, "email")) && !truthy(field(a, "self"))) e.attendees.push_back(str(a["email"]));
    }
    e.organizerIsSelf = field(field(raw, "organizer"), "self") == json(true);
    e.htmlLink = optStr(field(raw, "htmlLink"));
    e.recurringInstance = truthy(field(raw, "recurringEventId"));
    e.seriesMaster = truthy(field(raw, "recurrence"));
    e.blocksTime = str(field(raw, "transparency")) != "transparent" &&
                   str(field(me, "responseStatus")) != "declined" && !cancelled;
    e.cancelled = cancelled;
    return e;
}

json googleSlotFields(const EventSlot& slot) {
    // Explicit nulls: PATCH merges these objects, so the other kind must be cleared.
    if (slot.allDay) {
        return {{"start", {{"date", slot.start}, {"dateTime", nullptr}}},
                {"end", {{"date", slot.end}, {"dateTime", nullptr}}}};
    }
    return {{"start", {{"dateTime", slot.start}, {"date", nullptr}}},
            {"end", {{"dateTime", slot.end}, {"date", nullptr}}}};
}

Params notifyParams(bool anyone) { return anyone ? Params{{"sendUpdates", "all"}} : Params{}; }

class GoogleBackend : public Backend {
public:
    GoogleBackend(mail::TokenSource token, mail::Transport transport)
        : token_(std::move(token)), transport_(std::move(transport)) {}

    Page listEvents(const Window& window, int limit) override {
        Page page;
        std::string next;
        for (int i = 0; i < kMaxPages; ++i) {
            Params params{{"timeMin", window.startUtc()},
                          {"timeMax", window.endUtc()},
                          {"singleEvents", "true"},
                          {"orderBy", "startTime"},
                          {"maxResults", std::to_string(std::min(limit - static_cast<int>(page.events.size()),
                                                                 kGoogleMaxPage))}};
            if (!next.empty()) params.emplace_back("pageToken", next);
            const json data = parsedBody(request("GET", kEventsPath, params));
            const json& items = field(data, "items");
            if (items.is_array()) {
                for (const auto& item : items) page.events.push_back(parseGoogleEvent(item));
            }
            next = str(field(data, "nextPageToken"));
            if (next.empty()) return page;
            if (static_cast<int>(page.events.size()) >= limit) break;
        }
        page.truncated = true;
        return page;
    }

    Event getEvent(const std::string& eventId) override {
        Event event = parseGoogleEvent(parsedBody(request("GET", eventPath(eventId))));
        // Google keeps a deleted event readable with this status.
        if (event.cancelled) throw CalendarEventGone("Google Calendar event " + eventId + " was cancelled.");
        return event;
    }

    Event createEvent(const NewEvent& event) override {
        json body = googleSlotFields(event.slot);
        body["summary"] = event.title;
        if (!event.attendees.empty()) {
            body["attendees"] = json::array();
            for (const auto& a : event.attendees) body["attendees"].push_back({{"email", a}});
        }
        if (event.location && !event.location->empty()) body["location"] = *event.location;
        if (event.description && !event.description->empty()) body["description"] = *event.description;
        return written(request("POST", kEventsPath, notifyParams(!event.attendees.empty()), &body));
    }

    Event updateEvent(const Event& event, const EventPatch& patch) override {
        json body = json::object();
        if (patch.title) body["summary"] = *patch.title;
        if (patch.slot) body.update(googleSlotFields(*patch.slot));
        if (patch.location) body["location"] = *patch.location;
        if (patch.description) body["description"] = *patch.description;
        return written(request("PATCH", eventPath(event.id), notifyParams(!event.attendees.empty()), &body));
    }

    std::string cancelEvent(const Event& event) override {
        const bool notifies = event.organizerIsSelf && !event.attendees.empty();
        request("DELETE", eventPath(event.id), notifyParams(notifies));
        return event.organizerIsSelf ? kCancelledForEveryone : kRemovedFromYourCalendar;
    }

private:
    mail::TokenSource token_;
    mail::Transport transport_;

    static std::string eventPath(const std::string& id) { return kEventsPath + "/" + eventPathSegment(id); }

    static Event written(const HttpResponse& response) {
        try {
            const json parsed = json::parse(response.body);
            if (!parsed.is_object()) throw CalendarError("'" + std::string(parsed.type_name()) + "' object has no attribute 'get'");
            return parseGoogleEvent(parsed);
        } catch (const std::exception& e) {
            throw CalendarWriteUnreadable(
                std::string("Google Calendar accepted the change but its reply couldn't be read (") + e.what() + ").");
        }
    }

    [[noreturn]] void raise(const HttpResponse& response, const std::string& where) {
        const int code = response.status;
        const std::string reason = googleReason(response);
        if (code == 401) {
            throw CalendarAuthError(
                "Google Calendar rejected the access token (401), so the Google connection needs reconnecting. "
                "(request: " + where + ")",
                {kGoogle}, {kGoogle});
        }
        if (code == 404) throw CalendarEventNotFound("Google Calendar found no event (" + where + ").");
        if (code == 410) throw CalendarEventGone("Google Calendar deleted this event (" + where + ").");
        if (code == 403 && reason == "accessNotConfigured") {
            throw CalendarError(
                "The Google Calendar API is not enabled for the Google Cloud project behind this connection. Enable "
                "it, wait a minute, then retry: " + std::string(kEnableUrl));
        }
        if (code == 403 && (reason == "insufficientPermissions" || reason == "insufficientAuthenticationScopes")) {
            throw CalendarAuthError(
                "Google refused the calendar request because the connection lacks calendar access, so it needs "
                "reconnecting with calendar access ticked. (request: " + where + ")",
                {kGoogle}, {kGoogle});
        }
        const std::string lowered = mail::text::lower(reason);
        const std::string suffix = "ratelimitexceeded";
        if (code == 429 || (code == 403 && lowered.size() >= suffix.size() &&
                            lowered.compare(lowered.size() - suffix.size(), suffix.size(), suffix) == 0)) {
            throw CalendarError("Google Calendar is rate-limiting this account. Retry after " +
                                response.header("Retry-After", "a short wait") + ". (request: " + where + ")");
        }
        throw CalendarError("Google Calendar request failed: " + where + " returned " + std::to_string(code) +
                            (reason.empty() ? "" : " (" + reason + ")") + ". Nothing was confirmed as changed.");
    }

    HttpResponse request(const std::string& method, const std::string& path, const Params& params = {},
                         const json* body = nullptr) {
        const std::string where = method + " " + path;
        HttpCall call{method, kGoogleBase + path + mail::queryString(params), {}, body ? body->dump() : ""};
        HttpResponse response;
        // Outside the try: a token or vault failure keeps its own cause, as in Python.
        call.headers["Authorization"] = "Bearer " + token_();
        if (body) call.headers["Content-Type"] = "application/json";
        try {
            response = transport_(call);
        } catch (const mail::TransportError& e) {
            throw CalendarError("Couldn't reach Google Calendar (" + e.kind + ") on " + where +
                                ". If this was a change, check the calendar before retrying: it may have gone "
                                "through.");
        }
        if (response.status >= 300) raise(response, where);
        return response;
    }
};

// ---- Microsoft Graph (graph.py) ----

const std::string kGraphBase = "https://graph.microsoft.com/v1.0";
// Without this Graph returns each event's own wall clock, unlabelled in practice.
constexpr const char* kPreferUtc = "outlook.timezone=\"UTC\"";
constexpr const char* kSelect =
    "id,subject,start,end,isAllDay,location,attendees,isOrganizer,organizer,webLink,"
    "type,showAs,responseStatus,isCancelled,originalStartTimeZone,originalEndTimeZone";
constexpr int kGraphMaxPage = 100;

std::string graphCode(const HttpResponse& response) {
    return str(field(field(parsedBody(response), "error"), "code"));
}

std::string reprOrNone(const json& v) { return v.is_null() ? "None" : repr(v); }

std::string graphUtcValue(const json& endpointRaw, const std::string& eventId) {
    const json endpoint = truthy(endpointRaw) ? endpointRaw : json::object();
    const json& value = field(endpoint, "dateTime");
    const json& zone = field(endpoint, "timeZone");
    if (truthy(value) && zone == json("UTC")) return isoformat(parseInstant(str(value) + "Z"));
    // Graph ignores the UTC preference on write replies and uses the event's own zone.
    const auto named = namedZone(str(zone));
    if (!truthy(value) || !named) {
        throw CalendarError("Microsoft Graph returned event " + eventId + " with time " + reprOrNone(value) +
                            " in zone " + reprOrNone(zone) + " instead of UTC, so its real time can't be known.");
    }
    const Moment wall = parseInstant(str(value) + "Z");
    return isoformat(withOffset(fromWall(wall.utc, wall.micro, *named), 0));
}

std::string graphAllDayValue(const json& endpointRaw, const json& originalZone, const std::string& eventId) {
    const json endpoint = truthy(endpointRaw) ? endpointRaw : json::object();
    const json& value = field(endpoint, "dateTime");
    const json& statedZone = field(endpoint, "timeZone");
    if (truthy(value) && truthy(statedZone)) {
        // The clock reading is in the stated zone, so midnight there names the date.
        Moment moment = parseInstant(str(value) + "Z");
        auto atMidnight = [](const Moment& m) { return (m.utc + m.offset) % 86400 == 0 && m.micro == 0; };
        if (atMidnight(moment)) return isoformat(dateOf(moment));
        const auto zone = statedZone == json("UTC") ? namedZone(str(originalZone)) : nullptr;
        if (zone) {
            moment = inZone(moment, *zone);
            if (atMidnight(moment)) return isoformat(dateOf(moment));
        }
    }
    throw CalendarError("Microsoft Graph returned all-day event " + eventId + " with time " + reprOrNone(value) +
                        " in zone " + reprOrNone(field(endpoint, "timeZone")) + ", originally " +
                        reprOrNone(originalZone) +
                        ". That isn't midnight in either, so its date can't be known. Check it in Outlook.");
}

Event parseGraphEvent(const json& raw) {
    if (!truthy(field(raw, "id"))) throw CalendarError("Microsoft Graph returned an event with no id.");
    const std::string id = mail::detail::pyStr(raw["id"]);
    Event e;
    e.id = str(raw["id"]);
    e.allDay = truthy(field(raw, "isAllDay"));
    if (e.allDay) {
        const json& startZone = field(raw, "originalStartTimeZone");
        const json& endZone = truthy(field(raw, "originalEndTimeZone")) ? field(raw, "originalEndTimeZone") : startZone;
        e.start = graphAllDayValue(field(raw, "start"), startZone, id);
        e.end = graphAllDayValue(field(raw, "end"), endZone, id);
    } else {
        e.start = graphUtcValue(field(raw, "start"), id);
        e.end = graphUtcValue(field(raw, "end"), id);
    }
    const std::string response = str(field(field(raw, "responseStatus"), "response"));
    const std::string organizer = mail::text::lower(str(field(field(field(raw, "organizer"), "emailAddress"), "address")));
    const bool isOrganizer = field(raw, "isOrganizer") == json(true);
    const bool cancelled = field(raw, "isCancelled") == json(true);
    const json& listed = field(raw, "attendees");
    if (listed.is_array()) {
        for (const auto& a : listed) {
            const std::string address = str(field(field(a, "emailAddress"), "address"));
            if (!address.empty() && !(isOrganizer && mail::text::lower(address) == organizer)) {
                e.attendees.push_back(address);
            }
        }
    }
    e.title = str(field(raw, "subject"));
    e.timeZone = optStr(field(raw, "originalStartTimeZone"));
    e.location = optStr(field(field(raw, "location"), "displayName"));
    e.organizerIsSelf = isOrganizer;
    e.htmlLink = optStr(field(raw, "webLink"));
    const std::string type = str(field(raw, "type"));
    e.recurringInstance = type == "occurrence" || type == "exception";
    e.seriesMaster = type == "seriesMaster";
    e.blocksTime = str(field(raw, "showAs")) != "free" && response != "declined" && !cancelled;
    e.cancelled = cancelled;
    return e;
}

std::string thisComputersZone() {
    const std::string name = localZoneName();
    if (name.empty()) {
        throw CalendarError(
            "This computer's time zone can't be named, so an all-day Outlook event might land on the wrong day. "
            "Nothing was changed. Set the system time zone, or set TZ to an IANA name such as America/New_York, "
            "then retry.");
    }
    return name;
}

json graphEndpoint(const std::string& value, bool allDay) {
    if (allDay) return {{"dateTime", value + "T00:00:00"}, {"timeZone", thisComputersZone()}};
    return {{"dateTime", utcNoZone(parseInstant(value))}, {"timeZone", "UTC"}};
}

json graphSlotFields(const EventSlot& slot) {
    return {{"start", graphEndpoint(slot.start, slot.allDay)},
            {"end", graphEndpoint(slot.end, slot.allDay)},
            {"isAllDay", slot.allDay}};
}

class GraphBackend : public Backend {
public:
    GraphBackend(mail::TokenSource token, mail::Transport transport)
        : token_(std::move(token)), transport_(std::move(transport)) {}

    Page listEvents(const Window& window, int limit) override {
        Page page;
        std::string url = kGraphBase + "/me/calendarView";
        Params params{{"startDateTime", window.startUtc()},
                      {"endDateTime", window.endUtc()},
                      {"$orderby", "start/dateTime"},
                      {"$select", kSelect},
                      {"$top", std::to_string(std::min(limit, kGraphMaxPage))}};
        for (int i = 0; i < kMaxPages; ++i) {
            const json data = parsedBody(request("GET", url, params));
            const json& items = field(data, "value");
            if (items.is_array()) {
                for (const auto& item : items) page.events.push_back(parseGraphEvent(item));
            }
            const std::string nextLink = str(field(data, "@odata.nextLink"));
            if (static_cast<int>(page.events.size()) >= limit) {
                page.truncated = !nextLink.empty() || static_cast<int>(page.events.size()) > limit;
                page.events.resize(static_cast<size_t>(limit));
                return page;
            }
            if (nextLink.empty()) return page;
            // The bearer token follows this link, so it must stay on Graph.
            if (nextLink.rfind(kGraphBase + "/", 0) != 0) {
                throw CalendarError(
                    "Microsoft Graph returned a next-page link outside Graph, so the rest of this calendar window "
                    "was not read.");
            }
            url = nextLink;
            params.clear();
        }
        page.truncated = true;
        return page;
    }

    Event getEvent(const std::string& eventId) override {
        return parseGraphEvent(parsedBody(request("GET", eventUrl(eventId), {{"$select", kSelect}})));
    }

    Event createEvent(const NewEvent& event) override {
        json body = graphSlotFields(event.slot);
        body["subject"] = event.title;
        if (!event.attendees.empty()) {
            body["attendees"] = json::array();
            for (const auto& a : event.attendees) {
                body["attendees"].push_back({{"emailAddress", {{"address", a}}}, {"type", "required"}});
            }
        }
        if (event.location && !event.location->empty()) body["location"] = {{"displayName", *event.location}};
        if (event.description && !event.description->empty()) {
            body["body"] = {{"contentType", "text"}, {"content", *event.description}};
        }
        return written(request("POST", kGraphBase + "/me/events", {}, &body));
    }

    Event updateEvent(const Event& event, const EventPatch& patch) override {
        json body = json::object();
        if (patch.title) body["subject"] = *patch.title;
        if (patch.slot) body.update(graphSlotFields(*patch.slot));
        if (patch.location) body["location"] = {{"displayName", *patch.location}};
        if (patch.description) body["body"] = {{"contentType", "text"}, {"content", *patch.description}};
        return written(request("PATCH", eventUrl(event.id), {}, &body));
    }

    std::string cancelEvent(const Event& event) override {
        const std::string url = eventUrl(event.id);
        if (!event.organizerIsSelf) {
            request("DELETE", url);
            return kRemovedFromYourCalendar;
        }
        if (!event.attendees.empty()) {
            const json empty = json::object();
            request("POST", url + "/cancel", {}, &empty);
        } else {
            // No one to tell, and /cancel is documented only for meetings.
            request("DELETE", url);
        }
        return kCancelledForEveryone;
    }

private:
    mail::TokenSource token_;
    mail::Transport transport_;

    static std::string eventUrl(const std::string& id) { return kGraphBase + "/me/events/" + eventPathSegment(id); }

    static Event written(const HttpResponse& response) {
        try {
            const json parsed = json::parse(response.body);
            if (!parsed.is_object()) throw CalendarError("'" + std::string(parsed.type_name()) + "' object has no attribute 'get'");
            return parseGraphEvent(parsed);
        } catch (const std::exception& e) {
            throw CalendarWriteUnreadable(
                std::string("Microsoft Graph accepted the change but its reply couldn't be read (") + e.what() + ").");
        }
    }

    [[noreturn]] void raise(const HttpResponse& response, const std::string& where) {
        const int code = response.status;
        const std::string detail = graphCode(response);
        if (code == 401) {
            throw CalendarAuthError(
                "Microsoft Graph rejected the access token (401), so the Microsoft connection needs reconnecting. "
                "(request: " + where + ")",
                {kMicrosoft}, {kMicrosoft});
        }
        if (code == 403) {
            throw CalendarAuthError(
                "Microsoft Graph refused the calendar request (403). The connection may lack calendar access, so it "
                "needs reconnecting with calendar access ticked. (request: " + where + "; code: " +
                    (detail.empty() ? "forbidden" : detail) + ")",
                {kMicrosoft}, {kMicrosoft});
        }
        if (code == 404) throw CalendarEventNotFound("Outlook found no event (" + where + ").");
        if (code == 410) throw CalendarEventGone("Outlook deleted this event (" + where + ").");
        if (code == 429) {
            throw CalendarError("Microsoft Graph is rate-limiting this account. Retry after " +
                                response.header("Retry-After", "a short wait") + "s. (request: " + where + ")");
        }
        throw CalendarError("Microsoft Graph request failed: " + where + " returned " + std::to_string(code) +
                            (detail.empty() ? "" : " (" + detail + ")") + ". Nothing was confirmed as changed.");
    }

    HttpResponse request(const std::string& method, const std::string& url, const Params& params = {},
                         const json* body = nullptr) {
        std::string path = url.substr(0, url.find('?'));
        if (path.rfind(kGraphBase, 0) == 0) path = path.substr(kGraphBase.size());
        const std::string where = method + " " + path;
        HttpCall call{method, url + mail::queryString(params), {}, body ? body->dump() : ""};
        HttpResponse response;
        // Outside the try: a token or vault failure keeps its own cause, as in Python.
        call.headers["Authorization"] = "Bearer " + token_();
        call.headers["Prefer"] = kPreferUtc;
        if (body) call.headers["Content-Type"] = "application/json";
        try {
            response = transport_(call);
        } catch (const mail::TransportError& e) {
            throw CalendarError("Couldn't reach Microsoft Graph (" + e.kind + ") on " + where +
                                ". If this was a change, check the calendar before retrying: it may have gone "
                                "through.");
        }
        if (response.status >= 300) raise(response, where);
        return response;
    }
};

}  // namespace

std::unique_ptr<Backend> makeGoogleBackend(mail::TokenSource token, mail::Transport transport) {
    return std::make_unique<GoogleBackend>(std::move(token), std::move(transport));
}

std::unique_ptr<Backend> makeGraphBackend(mail::TokenSource token, mail::Transport transport) {
    return std::make_unique<GraphBackend>(std::move(token), std::move(transport));
}

}  // namespace gaia::calendar
