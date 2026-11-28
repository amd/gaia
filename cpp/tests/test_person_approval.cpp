// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// person_approval against the Python module's own answers for the same text.

#include <gtest/gtest.h>

#include <fstream>
#include <stdexcept>
#include <string>

#include <nlohmann/json.hpp>

#include <gaia/person_approval.h>

#include <gaia/idna.h>
#include "../src/mail_text.h"

using namespace gaia::approval;

namespace {

std::string refusal(const std::string& address) {
    try {
        sendableAddress(address, "notify_attendees");
    } catch (const std::invalid_argument& e) {
        return e.what();
    }
    return "";
}

}  // namespace

TEST(PersonApproval, SendsAPlainAddressAsWritten) {
    EXPECT_EQ(sendableAddress("dana@example.com", "notify_attendees"), "dana@example.com");
    EXPECT_EQ(sendableAddress("dana@example..com", "notify_attendees"), "dana@example..com");
}

TEST(PersonApproval, SendsAnInternationalDomainInItsAsciiForm) {
    EXPECT_EQ(sendableAddress("bob@b\xC3\xBC" "cher.example", "notify_attendees"), "bob@xn--bcher-kva.example");
    EXPECT_EQ(sendableAddress("ann@\xE4\xBE\x8B\xE3\x81\x88.\xE3\x83\x86\xE3\x82\xB9\xE3\x83\x88", "notify_attendees"),
              "ann@xn--r8jz45g.xn--zckzah");
}

TEST(PersonApproval, RefusesAnAddressThatCantBeSentInPythonsWords) {
    EXPECT_EQ(refusal("a@b@c.example"),
              "`notify_attendees` has an address that can't be used: 'a@b@c.example'. Give a plain address like "
              "name@example.com.");
    EXPECT_EQ(refusal("Eve <eve@x.example>"),
              "`notify_attendees` has an address that can't be used: 'Eve <eve@x.example>'. Give a plain address "
              "like name@example.com.");
    EXPECT_EQ(refusal("zo\xC3\xAB@example.com"),
              "`notify_attendees` has non-ASCII characters before the @ in 'zo\xC3\xAB@example.com', which GAIA "
              "can't send to. Check the address with its owner.");
    EXPECT_EQ(refusal("pay\xE2\x80\xAE@example.com"),
              "`notify_attendees` has an address with an invisible or control character (U+202E), which could "
              "disguise who receives it: 'pay[U+202E]@example.com'. Give the plain address.");
}

TEST(PersonApproval, ShowsHiddenCharactersAsCodes) {
    EXPECT_EQ(visible("Pay\xE2\x80\xAEroll"), "Pay[U+202E]roll");
    EXPECT_EQ(visible("a\tb\nc"), "a\tb\nc");
    EXPECT_EQ(visibleLine("a\tb\nc"), "a\tb[U+000A]c");
    EXPECT_TRUE(hasUnseen("x\xE2\x80\x8By"));
    EXPECT_FALSE(hasUnseen("a\tb\nc"));
    EXPECT_STREQ(kHiddenNote,
                 "This text holds characters that don't show. Those that hide or reorder text appear as [U+XXXX]; "
                 "ideograph variant marks are kept as written.");
}

namespace {

nlohmann::json addressParity() {
    std::ifstream in(std::string(GAIA_TEST_FIXTURES_DIR) + "/calendar/address_parity.json");
    EXPECT_TRUE(in) << "cpp/tests/fixtures/calendar/address_parity.json";
    return nlohmann::json::parse(in);
}

}  // namespace

TEST(PersonApproval, EveryAddressIsSentOrRefusedAsPythonDoes) {
    const auto parity = addressParity();
    ASSERT_GT(parity["addresses"].size(), 3000u);
    size_t mismatches = 0;
    for (const auto& row : parity["addresses"]) {
        const std::string address = row["address"];
        std::string got;
        try {
            got = "sent " + sendableAddress(address, "notify_attendees");
        } catch (const std::invalid_argument& e) {
            got = std::string("error ") + e.what();
        }
        const std::string want =
            row.contains("sent") ? "sent " + row["sent"].get<std::string>() : "error " + row["error"].get<std::string>();
        if (got != want && ++mismatches <= 20) ADD_FAILURE() << address << "\n  got:  " << got << "\n  want: " << want;
    }
    EXPECT_EQ(mismatches, 0u);
}

TEST(PersonApproval, NormalizesToFormCAsPythonDoes) {
    const auto parity = addressParity();
    for (const auto& row : parity["nfc"]) {
        const std::string in = row[0], want = row[1];
        EXPECT_EQ(gaia::mail::text::encode(gaia::idna::nfc(gaia::mail::text::decode(in))), want) << in;
    }
}
