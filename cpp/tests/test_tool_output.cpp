// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/tool_output.h>

#include <string>

using namespace gaia;

TEST(ToolOutputBudgetTest, ScalesWithTheContextWindowLikePython) {
    auto b32 = ToolOutputBudget::forContext(32768);
    EXPECT_EQ(b32.threshold, 30000u);
    EXPECT_EQ(b32.target, 20000u);
    auto b64 = ToolOutputBudget::forContext(65536);
    EXPECT_EQ(b64.threshold, 60000u);
    EXPECT_EQ(b64.target, 40000u);
}

TEST(ToolOutputTest, ResultUnderTheThresholdPassesThroughUnchanged) {
    ToolOutputStore store;
    const std::string small = R"({"status":"ok"})";
    EXPECT_EQ(condenseToolOutput("read_file", small, ToolOutputBudget::forContext(32768), store), small);
    EXPECT_EQ(store.size(), 0u);
}

TEST(ToolOutputTest, OversizedResultKeepsHeadTailAndAHandle) {
    ToolOutputStore store;
    std::string big = "HEAD" + std::string(50000, 'x') + "TAIL";
    const ToolOutputBudget budget{30000, 20000};
    const json view = json::parse(condenseToolOutput("read_file", big, budget, store));
    EXPECT_TRUE(view["truncated"].get<bool>());
    EXPECT_EQ(view["original_chars"].get<std::size_t>(), big.size());
    EXPECT_EQ(view["head"].get<std::string>().substr(0, 4), "HEAD");
    const std::string tail = view["tail"].get<std::string>();
    EXPECT_EQ(tail.substr(tail.size() - 4), "TAIL");
    EXPECT_EQ(view["continuation"], "read_tool_output");
    EXPECT_LE(view["head"].get<std::string>().size() + tail.size(), budget.target);

    // Entry 1 is exactly the omitted middle; head + middle + tail == original.
    const std::string handle = view["artifact"];
    const json part = store.read(handle, 1, 0, 0);
    ASSERT_FALSE(part.contains("error")) << part.dump();
    EXPECT_EQ(part["offset"].get<std::size_t>(), view["head"].get<std::string>().size());
    EXPECT_LE(part["content"].get<std::string>().size(), ToolOutputStore::kPageChars);
}

TEST(ToolOutputTest, PagingWithOffsetWalksTheWholeText) {
    ToolOutputStore store;
    std::string text;
    for (int i = 0; i < 3000; ++i) text += std::to_string(i % 10);
    const std::string h = store.put(text);
    std::string rebuilt;
    long long offset = 0;
    for (int guard = 0; guard < 10; ++guard) {
        json page = store.read(h, 0, offset, 1000);
        rebuilt += page["content"].get<std::string>();
        if (page["next_offset"].is_null()) break;
        offset = page["next_offset"].get<long long>();
    }
    EXPECT_EQ(rebuilt, text);
}

TEST(ToolOutputTest, ReadRejectsBadHandlesAndMixedArguments) {
    ToolOutputStore store;
    EXPECT_EQ(store.read("output_nope", 0, 0, 0)["status"], "error");
    const std::string h = store.put("abc", {{"all", 0, 3}});
    EXPECT_EQ(store.read(h, 1, 1, 0)["status"], "error");
    EXPECT_EQ(store.read(h, 2, 0, 0)["status"], "error");
    EXPECT_EQ(store.read(h, 1, 0, 0)["content"], "abc");
}

TEST(ToolOutputTest, PageCapIs8000Characters) {
    ToolOutputStore store;
    const std::string h = store.put(std::string(20000, 'z'));
    EXPECT_EQ(store.read(h, 0, 0, 100000)["content"].get<std::string>().size(), 8000u);
}

TEST(ToolOutputTest, AReadToolOutputPageIsNeverCutAgain) {
    ToolOutputStore store;
    const std::string big(100000, 'q');
    EXPECT_EQ(condenseToolOutput("read_tool_output", big, {10, 5}, store), big);
}

TEST(ToolOutputTest, ToolCoercesStringNumbers) {
    ToolOutputStore store;
    const std::string h = store.put("hello world");
    ToolInfo tool = ToolOutputStore::readTool(store);
    json r = tool.callback({{"artifact", h}, {"offset", "6"}});
    EXPECT_EQ(r["content"], "world");
    EXPECT_EQ(tool.callback({{"artifact", h}, {"offset", "six"}})["status"], "error");
}

TEST(ToolOutputTest, PagesNeverSplitACharacter) {
    ToolOutputStore store;
    // "é" is two bytes, "😀" four; byte 1 is the middle of the first é.
    const std::string text = "\xC3\xA9\xF0\x9F\x98\x80z";
    const std::string handle = store.put(text, {});
    json mid = store.read(handle, 0, 1, 0);
    EXPECT_EQ(mid["offset"], 0);
    EXPECT_EQ(mid["content"].get<std::string>().substr(0, 2), "\xC3\xA9");
    json one = store.read(handle, 0, 2, 1);
    EXPECT_EQ(one["content"], "\xF0\x9F\x98\x80");
    EXPECT_EQ(one["next_offset"], 6);
    json last = store.read(handle, 0, 4, 1);  // inside the emoji: backs up to its start
    EXPECT_EQ(last["content"], "\xF0\x9F\x98\x80");
    EXPECT_NO_THROW(mid.dump());
}
