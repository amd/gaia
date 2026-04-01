// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/sse_parser.h>

#include <string>
#include <vector>

using namespace gaia;

// ---------------------------------------------------------------------------
// Helper: feed a std::string as raw bytes
// ---------------------------------------------------------------------------
static bool feedString(SseParser& parser, const std::string& s) {
    return parser.feed(s.data(), s.size());
}

// ---------------------------------------------------------------------------
// 1. Single complete event
// ---------------------------------------------------------------------------

TEST(SseParserTest, SingleEvent) {
    std::vector<std::string> tokens;
    SseParser parser([&](const std::string& tok) { tokens.push_back(tok); });

    feedString(parser, "data: {\"choices\":[{\"delta\":{\"content\":\"Hello\"}}]}\n\n");

    ASSERT_EQ(tokens.size(), 1u);
    EXPECT_EQ(tokens[0], "Hello");
    EXPECT_FALSE(parser.done());
    EXPECT_TRUE(parser.hasTokens());
}

// ---------------------------------------------------------------------------
// 2. Multiple events in one chunk
// ---------------------------------------------------------------------------

TEST(SseParserTest, MultipleEventsOneChunk) {
    std::vector<std::string> tokens;
    SseParser parser([&](const std::string& tok) { tokens.push_back(tok); });

    feedString(parser,
        "data: {\"choices\":[{\"delta\":{\"content\":\"Foo\"}}]}\n"
        "data: {\"choices\":[{\"delta\":{\"content\":\"Bar\"}}]}\n");

    ASSERT_EQ(tokens.size(), 2u);
    EXPECT_EQ(tokens[0], "Foo");
    EXPECT_EQ(tokens[1], "Bar");
}

// ---------------------------------------------------------------------------
// 3. Line split across two feed() calls
// ---------------------------------------------------------------------------

TEST(SseParserTest, ChunkedDelivery) {
    std::vector<std::string> tokens;
    SseParser parser([&](const std::string& tok) { tokens.push_back(tok); });

    // Split the data line in the middle
    const std::string full = "data: {\"choices\":[{\"delta\":{\"content\":\"Hi\"}}]}\n";
    const std::string half1 = full.substr(0, full.size() / 2);
    const std::string half2 = full.substr(full.size() / 2);

    feedString(parser, half1);
    EXPECT_TRUE(tokens.empty()) << "No token expected mid-line";

    feedString(parser, half2);
    ASSERT_EQ(tokens.size(), 1u);
    EXPECT_EQ(tokens[0], "Hi");
}

// ---------------------------------------------------------------------------
// 4. [DONE] sentinel (with space after 'data:')
// ---------------------------------------------------------------------------

TEST(SseParserTest, DoneSentinelWithSpace) {
    std::vector<std::string> tokens;
    SseParser parser([&](const std::string& tok) { tokens.push_back(tok); });

    bool cont = feedString(parser, "data: [DONE]\n");

    EXPECT_FALSE(cont);
    EXPECT_TRUE(parser.done());
    EXPECT_TRUE(tokens.empty());
}

// ---------------------------------------------------------------------------
// 5. [DONE] sentinel (no space after 'data:')
// ---------------------------------------------------------------------------

TEST(SseParserTest, DoneSentinelNoSpace) {
    std::vector<std::string> tokens;
    SseParser parser([&](const std::string& tok) { tokens.push_back(tok); });

    bool cont = feedString(parser, "data:[DONE]\n");

    EXPECT_FALSE(cont);
    EXPECT_TRUE(parser.done());
    EXPECT_TRUE(tokens.empty());
}

// ---------------------------------------------------------------------------
// 6. Empty delta.content field
// ---------------------------------------------------------------------------

TEST(SseParserTest, EmptyDeltaContent) {
    std::vector<std::string> tokens;
    SseParser parser([&](const std::string& tok) { tokens.push_back(tok); });

    feedString(parser, "data: {\"choices\":[{\"delta\":{\"content\":\"\"}}]}\n");

    EXPECT_TRUE(tokens.empty());
}

// ---------------------------------------------------------------------------
// 7. delta without content key (role-only chunk)
// ---------------------------------------------------------------------------

TEST(SseParserTest, RoleOnlyChunk) {
    std::vector<std::string> tokens;
    SseParser parser([&](const std::string& tok) { tokens.push_back(tok); });

    feedString(parser, "data: {\"choices\":[{\"delta\":{\"role\":\"assistant\"}}]}\n");

    EXPECT_TRUE(tokens.empty());
}

// ---------------------------------------------------------------------------
// 8. Missing delta entirely
// ---------------------------------------------------------------------------

TEST(SseParserTest, MissingDelta) {
    std::vector<std::string> tokens;
    SseParser parser([&](const std::string& tok) { tokens.push_back(tok); });

    feedString(parser, "data: {\"choices\":[{}]}\n");

    EXPECT_TRUE(tokens.empty());
}

// ---------------------------------------------------------------------------
// 9. Malformed JSON — no crash, no token
// ---------------------------------------------------------------------------

TEST(SseParserTest, MalformedJson) {
    std::vector<std::string> tokens;
    SseParser parser([&](const std::string& tok) { tokens.push_back(tok); });

    EXPECT_NO_THROW(feedString(parser, "data: {not valid json\n"));
    EXPECT_TRUE(tokens.empty());
}

// ---------------------------------------------------------------------------
// 10. SSE comment lines (: prefix) are ignored
// ---------------------------------------------------------------------------

TEST(SseParserTest, SseCommentIgnored) {
    std::vector<std::string> tokens;
    SseParser parser([&](const std::string& tok) { tokens.push_back(tok); });

    feedString(parser, ": this is a heartbeat\n");

    EXPECT_TRUE(tokens.empty());
    EXPECT_FALSE(parser.done());
}

// ---------------------------------------------------------------------------
// 11. Empty lines (SSE event separators) are ignored
// ---------------------------------------------------------------------------

TEST(SseParserTest, EmptyLineIgnored) {
    std::vector<std::string> tokens;
    SseParser parser([&](const std::string& tok) { tokens.push_back(tok); });

    feedString(parser, "\n\n\n");

    EXPECT_TRUE(tokens.empty());
    EXPECT_FALSE(parser.done());
}

// ---------------------------------------------------------------------------
// 12. CRLF line endings (\r\n)
// ---------------------------------------------------------------------------

TEST(SseParserTest, CrLfLineEndings) {
    std::vector<std::string> tokens;
    SseParser parser([&](const std::string& tok) { tokens.push_back(tok); });

    feedString(parser, "data: {\"choices\":[{\"delta\":{\"content\":\"X\"}}]}\r\n");

    ASSERT_EQ(tokens.size(), 1u);
    EXPECT_EQ(tokens[0], "X");
}

// ---------------------------------------------------------------------------
// 13. hasTokens() flag
// ---------------------------------------------------------------------------

TEST(SseParserTest, HasTokensFlag) {
    std::vector<std::string> tokens;
    SseParser parser([&](const std::string& tok) { tokens.push_back(tok); });

    EXPECT_FALSE(parser.hasTokens());
    feedString(parser, "data: {\"choices\":[{\"delta\":{\"content\":\"Y\"}}]}\n");
    EXPECT_TRUE(parser.hasTokens());
}

// ---------------------------------------------------------------------------
// 14. Accumulation across multiple token events
// ---------------------------------------------------------------------------

TEST(SseParserTest, Accumulation) {
    std::string accumulated;
    SseParser parser([&](const std::string& tok) { accumulated += tok; });

    feedString(parser, "data: {\"choices\":[{\"delta\":{\"content\":\"The \"}}]}\n");
    feedString(parser, "data: {\"choices\":[{\"delta\":{\"content\":\"quick \"}}]}\n");
    feedString(parser, "data: {\"choices\":[{\"delta\":{\"content\":\"fox\"}}]}\n");

    EXPECT_EQ(accumulated, "The quick fox");
}

// ---------------------------------------------------------------------------
// 15. Tokens before [DONE] are emitted, feed() returns false after [DONE]
// ---------------------------------------------------------------------------

TEST(SseParserTest, TokensThenDone) {
    std::vector<std::string> tokens;
    SseParser parser([&](const std::string& tok) { tokens.push_back(tok); });

    feedString(parser, "data: {\"choices\":[{\"delta\":{\"content\":\"Hello\"}}]}\n");
    feedString(parser, "data: {\"choices\":[{\"delta\":{}}]}\n");  // finish_reason chunk
    bool cont = feedString(parser, "data: [DONE]\n");

    ASSERT_EQ(tokens.size(), 1u);
    EXPECT_EQ(tokens[0], "Hello");
    EXPECT_FALSE(cont);
    EXPECT_TRUE(parser.done());
}

// ---------------------------------------------------------------------------
// 16. feed() after done() returns false immediately
// ---------------------------------------------------------------------------

TEST(SseParserTest, FeedAfterDoneIsNoop) {
    std::vector<std::string> tokens;
    SseParser parser([&](const std::string& tok) { tokens.push_back(tok); });

    feedString(parser, "data: [DONE]\n");
    EXPECT_TRUE(parser.done());

    bool cont = feedString(parser, "data: {\"choices\":[{\"delta\":{\"content\":\"Ghost\"}}]}\n");
    EXPECT_FALSE(cont);
    EXPECT_TRUE(tokens.empty()) << "Should not emit tokens after [DONE]";
}

// ---------------------------------------------------------------------------
// 17. null delta.content is treated as empty (no token)
// ---------------------------------------------------------------------------

TEST(SseParserTest, NullDeltaContent) {
    std::vector<std::string> tokens;
    SseParser parser([&](const std::string& tok) { tokens.push_back(tok); });

    feedString(parser, "data: {\"choices\":[{\"delta\":{\"content\":null}}]}\n");

    EXPECT_TRUE(tokens.empty());
}

// ---------------------------------------------------------------------------
// 18. event: and id: lines are silently ignored
// ---------------------------------------------------------------------------

TEST(SseParserTest, NonDataFieldsIgnored) {
    std::vector<std::string> tokens;
    SseParser parser([&](const std::string& tok) { tokens.push_back(tok); });

    feedString(parser,
        "event: message\n"
        "id: 42\n"
        "retry: 3000\n"
        "data: {\"choices\":[{\"delta\":{\"content\":\"Z\"}}]}\n");

    ASSERT_EQ(tokens.size(), 1u);
    EXPECT_EQ(tokens[0], "Z");
}
