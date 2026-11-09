// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Same cases as tests/unit/test_memory_keeps_no_secrets.py, so both runtimes
// agree on what never reaches memory.

#include <gtest/gtest.h>
#include <gaia/secrets.h>

#include <string>
#include <vector>

using namespace gaia;

namespace {

const std::vector<std::string> kSecrets = {
    "My bank PIN is 4821",
    "pin: 9921",
    "The CVV is 123",
    "my passcode is 778899",
    "My social security number is 123-45-6789",
    "card 4111 1111 1111 1111 exp 12/29",
    "online banking password is Blue-Heron-77",
    "The user's PIN for Chase is 4821",
    "User's PIN number for the ATM is 4821",
    "my pin was 4821",
    "Bank PIN \xE2\x80\x94 4821",
    "User's SSN is 123 45 6789",
    "User's social security number is 123456789",
    "4111\xE2\x80\x93" "1111\xE2\x80\x93" "1111\xE2\x80\x93" "1111",
    "4111.1111.1111.1111",
    "4111  1111  1111  1111",
    "card4111111111111111",
    "Amex 3782 822463 10005",
    "Card: 4111 1111 1111 1111 12/29 123",
    "4111111111111111 12/29",
    "call 5 4111 1111 1111 1111",
    "Amex 3782 822463 10005 0425",
    "sk-ant-api03-abcdefghijklmnop",
    "export OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456",
    "token ghp_abcdefghijklmnopqrstuvwxyz0123",
    "AKIAABCDEFGHIJKLMNOP",
    "Authorization: Bearer abcdefghijklmnopqrstuvwxyz.123",
    "the api key: hunter2",
    "HALIBUT-4417-ZULU",
};

const std::vector<std::string> kPinForms = {
    "User's PIN for their Chase debit card is 4821", "I changed my PIN to 4821", "PIN 4821",
    "My Chase card PIN is 4821", "Chase bank PIN: 4821", "My PIN is 4821", "PIN number 4821",
    "PIN code: 4821", "PIN code 4821", "PIN of my card is 4821", "PIN for my card 4821",
    "PIN for Chase 4821", "PIN on card 4821", "pin on chase 4821", "PIN at my bank 4821",
    "PIN of my chase card 4821", "PIN is 4821", "4821 is my PIN", "pin#4821", "PIN (4821)",
    "PIN number on my card 4821", "PIN number for Chase is 4821", "CVV for my Visa is 123",
    "CVV on my card: 123", "cvc for amex 1234", "security code on the back is 123",
    "passcode for the alarm is 4821", "Passcode number 4821", "pass code is 4821",
    "PIN code for Chase is 4821", "PIN code on my card: 4821", "PIN code at my bank 4821",
    "PIN code of my card is 4821", "PIN code is 48219", "PIN code is 4821937", "PIN code is 123456",
    "My PIN code: 123456", "PIN code for my Chase card is 123456", "My PIN code is 560001",
    "PIN code 560001", "PIN code for Chase is 123456", "PIN code of HDFC Bank: 482193",
    "PIN code for Wells Fargo 482193", "PIN code of Bangalore is 560001",
};

const std::vector<std::string> kNotSecrets = {
    "Call me at 555-123-4567",
    "Meeting on 2026-10-23 at 3pm",
    "Connect the wire to pin 3",
    "I pinned 12 tabs",
    "Order 1234567890123 shipped",
    "Tracking 1000000000009 is out for delivery",
    "Logged at 9000000000001 ms",
    "My zip code is 97201",
    "My phone's IMEI is 356938035643809",
    "IMEI 490154203237518",
    "Call 1-800-555-0199 or +1 (408) 555 0123",
    "See pages 4111-4122",
    "the security code review is 2026 Q4",
    "pin the tab to slot 2",
    "408-555-0123 408-555-0199",
    "IMEI 356938035643809 12",
    "IMEI 356938035643809 12/29",
    "GPIO pin 1023",
    "Pin on page 123",
    "Pin code on page 123",
    "+49 30 12345678 or +49 89 98765432",
    "9400 1000 0000 0000 0000 00",
    "4111 1111 1111 1112",  // fails the Luhn check
};

// Card numbers spaced with every kind of horizontal whitespace still count.
const std::vector<std::string> kSpacedCards = {
    "4111 - 1111 - 1111 - 1111",
    "4111   1111   1111   1111",
    "4111\t1111\t1111\t1111",
    "4111\xE2\x80\xAF" "1111\xE2\x80\xAF" "1111\xE2\x80\xAF" "1111",  // U+202F
    "4111\xE2\x80\x89" "1111\xE2\x80\x89" "1111\xE2\x80\x89" "1111",  // U+2009
    "4111\xE3\x80\x80" "1111\xE3\x80\x80" "1111\xE3\x80\x80" "1111",  // U+3000
    "4111\xC2\xA0" "1111\xC2\xA0" "1111\xC2\xA0" "1111",              // U+00A0
};

}  // namespace

TEST(SecretsTest, FlagsEveryKnownSecretShape) {
    for (const auto& s : kSecrets) EXPECT_TRUE(looksLikeCredential(s)) << s;
}

TEST(SecretsTest, FlagsEveryPinAndSecurityCodeForm) {
    for (const auto& s : kPinForms) EXPECT_TRUE(looksLikeCredential(s)) << s;
}

TEST(SecretsTest, LeavesOrdinaryNumbersAlone) {
    for (const auto& s : kNotSecrets) EXPECT_FALSE(looksLikeCredential(s)) << s;
}

TEST(SecretsTest, CardsSpacedWithUnicodeWhitespaceCount) {
    for (const auto& s : kSpacedCards) EXPECT_TRUE(looksLikeCredential(s)) << s;
}

TEST(SecretsTest, LineBreaksNeverJoinACardNumber) {
    for (const std::string nl : {"\n", "\r\n", "\xE2\x80\xA8", "\xC2\x85"}) {
        const std::string text = "4111 1111" + nl + "1111 1111";
        EXPECT_FALSE(looksLikeCredential(text)) << "separator bytes: " << nl.size();
    }
}

TEST(SecretsTest, MaskReplacesOnlyTheSecretSpans) {
    const std::string masked =
        maskCredentials("My bank PIN is 4821 and my card is 4111 1111 1111 1111. Remember them.");
    EXPECT_EQ(masked.find("4821"), std::string::npos);
    EXPECT_EQ(masked.find("4111"), std::string::npos);
    EXPECT_NE(masked.find("[secret removed]"), std::string::npos);
    EXPECT_NE(masked.find("Remember them."), std::string::npos);
    EXPECT_EQ(maskCredentials("nothing to hide here"), "nothing to hide here");
}

TEST(SecretsTest, LongInputStaysFastAndCorrect) {
    std::string big(200000, 'a');
    big += " password is swordfish ";
    big += std::string(200000, ' ');
    const auto spans = credentialSpans(big);
    ASSERT_EQ(spans.size(), 1u);
    EXPECT_EQ(big.substr(spans[0].first, spans[0].second - spans[0].first), "password is swordfish");
}

TEST(CredentialSpansTest, KeyCrossingTheScanWindowIsMaskedWhole) {
    // The token starts before the second window's start and ends past the first window's end.
    const std::string token = "sk-" + std::string(500, 'A');
    const std::string text = std::string(3699, 'x') + " " + token + " tail";
    const std::string masked = maskCredentials(text);
    EXPECT_EQ(masked.find("AAAA"), std::string::npos);
    EXPECT_NE(masked.find("[secret removed] tail"), std::string::npos);
}
