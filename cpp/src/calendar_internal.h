// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// The provider-neutral calendar shape and its time rules, ported from
// src/gaia/agents/tools/_calendar/ (model.py, errors.py, google.py, graph.py).
// A timed value always carries a UTC offset and an all-day value is a bare
// YYYY-MM-DD; an all-day end is exclusive, as both providers store it.

#pragma once

#include <cstdint>
#include <functional>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

#include "gaia/mail.h"
#include "gaia/types.h"

namespace gaia::calendar {

// ---- errors (errors.py) ----

class CalendarError : public std::runtime_error {
public:
    explicit CalendarError(const std::string& message, std::vector<std::string> connect = {},
                           std::vector<std::string> reconnect = {}, bool setup = false)
        : std::runtime_error(message),
          connect(std::move(connect)),
          reconnect(std::move(reconnect)),
          setup(setup) {}
    std::vector<std::string> connect;
    std::vector<std::string> reconnect;
    bool setup = false;
};

class CalendarAuthError : public CalendarError {
public:
    using CalendarError::CalendarError;
};
class CalendarEventGone : public CalendarError {
public:
    using CalendarError::CalendarError;
};
class CalendarEventNotFound : public CalendarError {
public:
    using CalendarError::CalendarError;
};
class CalendarWriteUnreadable : public CalendarError {
public:
    using CalendarError::CalendarError;
};

// ---- time ----

/// An aware datetime: a UTC instant and the offset it is read at.
struct Moment {
    int64_t utc = 0;  // seconds since 1970-01-01T00:00:00Z
    int micro = 0;
    int offset = 0;  // seconds east of UTC

    bool operator<(const Moment& o) const { return utc != o.utc ? utc < o.utc : micro < o.micro; }
    bool operator==(const Moment& o) const { return utc == o.utc && micro == o.micro; }
    bool operator!=(const Moment& o) const { return !(*this == o); }
    bool operator<=(const Moment& o) const { return !(o < *this); }
};

struct Date {
    int year = 1970, month = 1, day = 1;
    bool operator<(const Date& o) const {
        return year != o.year ? year < o.year : month != o.month ? month < o.month : day < o.day;
    }
};

/// A time zone's UTC offset at an instant.
class Zone {
public:
    virtual ~Zone() = default;
    virtual int offsetAt(int64_t utc) const = 0;
};

/// This computer's zone, as the C library reads it (the TZ variable or the system setting).
std::shared_ptr<const Zone> localZone();
/// A zone by IANA or Windows name, as graph.py's _named_zone; nullptr when unknown.
std::shared_ptr<const Zone> namedZone(const std::string& name);
/// The name of this computer's zone, as tzlocal names it; "" when it can't be named.
std::string localZoneName();

/// The current time, in UTC. Replaced in tests.
using Clock = std::function<Moment()>;
Moment systemNow();

std::string isoformat(const Moment& m, bool secondsOnly = false);
std::string isoformat(const Date& d);
Moment withOffset(Moment m, int offset);
Moment inZone(const Moment& m, const Zone& zone);
/// The moment a wall-clock reading names in `zone`, first of two when it repeats (fold=0).
Moment fromWall(int64_t wallSeconds, int micro, const Zone& zone);
Date dateOf(const Moment& m);
int weekday(const Date& d);  // Monday = 0
Date addDays(const Date& d, int days);
int64_t daysFromCivil(const Date& d);

bool isBareDate(const std::string& value);
Moment parseInstant(const std::string& value, const std::string& name = "time");
Date parseDate(const std::string& value, const std::string& name = "date");

struct EventSlot {
    std::string start;
    std::string end;
    bool allDay = false;
};
EventSlot parseSlot(const std::string& start, const std::string& end);
void checkDaylightOffset(const std::string& value, const std::string& name, const Moment& now);
/// The zone to read `anchor`'s span in: this computer's when anchor is in it, else its own offset.
std::shared_ptr<const Zone> wallClockZone(const Moment& anchor);
std::shared_ptr<const Zone> fixedZone(int offset);

// ---- events ----

struct Event {
    std::string id;
    std::string title;
    std::string start;
    std::string end;
    bool allDay = false;
    std::optional<std::string> timeZone;
    std::optional<std::string> location;
    std::vector<std::string> attendees;  // everyone but the person
    bool organizerIsSelf = false;
    std::optional<std::string> htmlLink;
    bool recurringInstance = false;
    bool seriesMaster = false;
    bool blocksTime = true;
    bool cancelled = false;
};

struct Window {
    Moment start;
    Moment end;
    Window(Moment s, Moment e);
    std::string startUtc() const;
    std::string endUtc() const;
};

struct Page {
    std::vector<Event> events;
    bool truncated = false;
};

struct NewEvent {
    std::string title;
    EventSlot slot;
    std::vector<std::string> attendees;
    std::optional<std::string> location;
    std::optional<std::string> description;
};

struct EventPatch {
    std::optional<std::string> title;
    std::optional<EventSlot> slot;
    std::optional<std::string> location;
    std::optional<std::string> description;
    bool empty() const { return !title && !slot && !location && !description; }
};

constexpr const char* kCancelledForEveryone = "cancelled_for_everyone";
constexpr const char* kRemovedFromYourCalendar = "removed_from_your_calendar";

class Backend {
public:
    virtual ~Backend() = default;
    virtual Page listEvents(const Window& window, int limit) = 0;
    virtual Event getEvent(const std::string& eventId) = 0;
    virtual Event createEvent(const NewEvent& event) = 0;
    virtual Event updateEvent(const Event& event, const EventPatch& patch) = 0;
    virtual std::string cancelEvent(const Event& event) = 0;
};

std::unique_ptr<Backend> makeGoogleBackend(mail::TokenSource token, mail::Transport transport);
std::unique_ptr<Backend> makeGraphBackend(mail::TokenSource token, mail::Transport transport);

}  // namespace gaia::calendar
