// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/json_utils.h>

using namespace gaia;

// ---- extractFirstJsonObject ----

TEST(JsonUtilsTest, ExtractFirstJsonObject_Simple) {
    std::string text = R"({"key": "value"})";
    EXPECT_EQ(extractFirstJsonObject(text), text);
}

TEST(JsonUtilsTest, ExtractFirstJsonObject_WithPrefix) {
    std::string text = R"(Some text before {"key": "value"} and after)";
    EXPECT_EQ(extractFirstJsonObject(text), R"({"key": "value"})");
}

TEST(JsonUtilsTest, ExtractFirstJsonObject_Nested) {
    std::string text = R"({"outer": {"inner": "value"}})";
    EXPECT_EQ(extractFirstJsonObject(text), text);
}

TEST(JsonUtilsTest, ExtractFirstJsonObject_WithStrings) {
    std::string text = R"({"key": "value with {braces}"})";
    EXPECT_EQ(extractFirstJsonObject(text), text);
}

TEST(JsonUtilsTest, ExtractFirstJsonObject_NoJson) {
    EXPECT_EQ(extractFirstJsonObject("no json here"), "");
}

TEST(JsonUtilsTest, ExtractFirstJsonObject_Incomplete) {
    EXPECT_EQ(extractFirstJsonObject("{incomplete"), "");
}

// ---- fixCommonJsonErrors ----

TEST(JsonUtilsTest, FixTrailingComma) {
    EXPECT_EQ(fixCommonJsonErrors(R"({"a": 1, })"), R"({"a": 1})");
    EXPECT_EQ(fixCommonJsonErrors(R"([1, 2, ])"), R"([1, 2])");
}

TEST(JsonUtilsTest, FixSingleQuotes) {
    // Only fixes if no double quotes present
    EXPECT_EQ(fixCommonJsonErrors("{'key': 'value'}"), R"({"key": "value"})");
}

TEST(JsonUtilsTest, FixLeadingText) {
    std::string result = fixCommonJsonErrors(R"(Sure, here's the JSON: {"key": "value"})");
    EXPECT_EQ(result, R"({"key": "value"})");
}

// ---- extractJsonFromResponse ----

TEST(JsonUtilsTest, ExtractFromCodeBlock) {
    std::string response = R"(Here's the result:
```json
{"thought": "analyzing", "answer": "42"}
```
That's it.)";

    auto result = extractJsonFromResponse(response);
    ASSERT_TRUE(result.has_value());
    EXPECT_EQ(result.value()["thought"], "analyzing");
    EXPECT_EQ(result.value()["answer"], "42");
}

TEST(JsonUtilsTest, ExtractFromCodeBlockNoTag) {
    std::string response = R"(```
{"thought": "test", "tool": "echo", "tool_args": {"msg": "hi"}}
```)";

    auto result = extractJsonFromResponse(response);
    ASSERT_TRUE(result.has_value());
    EXPECT_EQ(result.value()["tool"], "echo");
    // tool_args should be preserved
    EXPECT_EQ(result.value()["tool_args"]["msg"], "hi");
}

TEST(JsonUtilsTest, ExtractAutoFillToolArgs) {
    std::string response = R"({"thought": "testing", "tool": "echo"})";

    auto result = extractJsonFromResponse(response);
    ASSERT_TRUE(result.has_value());
    // tool_args should be auto-filled if tool is present
    EXPECT_TRUE(result.value().contains("tool_args"));
}

TEST(JsonUtilsTest, ExtractBracketMatch) {
    std::string response = R"(Let me think... {"thought": "deep thought", "answer": "yes"} done.)";

    auto result = extractJsonFromResponse(response);
    ASSERT_TRUE(result.has_value());
    EXPECT_EQ(result.value()["answer"], "yes");
}

TEST(JsonUtilsTest, ExtractNoJson) {
    EXPECT_FALSE(extractJsonFromResponse("Just plain text").has_value());
    EXPECT_FALSE(extractJsonFromResponse("").has_value());
}

// ---- validateJsonResponse ----

TEST(JsonUtilsTest, ValidateValidJson) {
    std::string response = R"({"thought": "test", "answer": "hello"})";
    json result = validateJsonResponse(response);
    EXPECT_EQ(result["thought"], "test");
    EXPECT_EQ(result["answer"], "hello");
}

TEST(JsonUtilsTest, ValidateWithCodeBlock) {
    std::string response = R"(```json
{"thought": "test", "tool": "echo", "tool_args": {"msg": "hi"}}
```)";
    json result = validateJsonResponse(response);
    EXPECT_EQ(result["tool"], "echo");
}

TEST(JsonUtilsTest, ValidateWithTrailingComma) {
    std::string response = R"({"thought": "test", "answer": "hello", })";
    json result = validateJsonResponse(response);
    EXPECT_EQ(result["answer"], "hello");
}

TEST(JsonUtilsTest, ValidateInvalidJson) {
    EXPECT_THROW(validateJsonResponse("not json at all"), std::runtime_error);
}

TEST(JsonUtilsTest, ValidateMissingThought) {
    // Answer without thought should throw
    EXPECT_THROW(validateJsonResponse(R"({"answer": "hello"})"), std::runtime_error);
}

// ---- parseLlmResponse ----

TEST(JsonUtilsTest, ParseEmptyResponse) {
    ParsedResponse parsed = parseLlmResponse("");
    ASSERT_TRUE(parsed.answer.has_value());
    EXPECT_TRUE(parsed.answer.value().find("empty response") != std::string::npos);
}

TEST(JsonUtilsTest, ParsePlainText) {
    ParsedResponse parsed = parseLlmResponse("Hello, I'm an assistant!");
    ASSERT_TRUE(parsed.answer.has_value());
    EXPECT_EQ(parsed.answer.value(), "Hello, I'm an assistant!");
    EXPECT_FALSE(parsed.toolName.has_value());
}

TEST(JsonUtilsTest, ParseValidToolCall) {
    std::string response = R"({"thought": "need to check", "goal": "gather info", "tool": "Shell", "tool_args": {"command": "dir"}})";
    ParsedResponse parsed = parseLlmResponse(response);

    EXPECT_EQ(parsed.thought, "need to check");
    EXPECT_EQ(parsed.goal, "gather info");
    ASSERT_TRUE(parsed.toolName.has_value());
    EXPECT_EQ(parsed.toolName.value(), "Shell");
    ASSERT_TRUE(parsed.toolArgs.has_value());
    EXPECT_EQ(parsed.toolArgs.value()["command"], "dir");
    EXPECT_FALSE(parsed.answer.has_value());
}

TEST(JsonUtilsTest, ParseValidAnswer) {
    std::string response = R"({"thought": "done", "goal": "completed", "answer": "The result is 42."})";
    ParsedResponse parsed = parseLlmResponse(response);

    EXPECT_EQ(parsed.thought, "done");
    ASSERT_TRUE(parsed.answer.has_value());
    EXPECT_EQ(parsed.answer.value(), "The result is 42.");
    EXPECT_FALSE(parsed.toolName.has_value());
}

TEST(JsonUtilsTest, ParseWithPlan) {
    std::string response = R"({
        "thought": "need multiple steps",
        "goal": "system check",
        "plan": [
            {"tool": "Shell", "tool_args": {"command": "mem"}},
            {"tool": "Shell", "tool_args": {"command": "disk"}}
        ],
        "tool": "Shell",
        "tool_args": {"command": "mem"}
    })";
    ParsedResponse parsed = parseLlmResponse(response);

    ASSERT_TRUE(parsed.plan.has_value());
    EXPECT_TRUE(parsed.plan.value().is_array());
    EXPECT_EQ(parsed.plan.value().size(), 2u);
    ASSERT_TRUE(parsed.toolName.has_value());
    EXPECT_EQ(parsed.toolName.value(), "Shell");
}

TEST(JsonUtilsTest, ParseMalformedJsonWithToolRegex) {
    // Simulate malformed JSON that needs regex extraction
    std::string response = R"({broken "thought": "testing", "tool": "echo", "tool_args": {"msg": "hi"}})";
    ParsedResponse parsed = parseLlmResponse(response);

    // Should fall back to regex extraction
    ASSERT_TRUE(parsed.toolName.has_value());
    EXPECT_EQ(parsed.toolName.value(), "echo");
}

TEST(JsonUtilsTest, ParseWhitespace) {
    ParsedResponse parsed = parseLlmResponse("   \n\t  ");
    ASSERT_TRUE(parsed.answer.has_value());
    EXPECT_TRUE(parsed.answer.value().find("empty response") != std::string::npos);
}
