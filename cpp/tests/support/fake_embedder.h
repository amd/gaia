// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// A deterministic stand-in for Lemonade's /embeddings: hashed bag of words, so
// texts sharing words are close. Lets retrieval be tested offline.

#pragma once

#include <cctype>
#include <cmath>
#include <functional>
#include <string>
#include <vector>

namespace gaia_test {

inline std::vector<std::vector<float>> fakeEmbed(const std::vector<std::string>& texts) {
    std::vector<std::vector<float>> out;
    for (const auto& t : texts) {
        std::vector<float> v(256, 0.0f);
        std::string word;
        auto flush = [&] {
            if (word.size() > 2) v[std::hash<std::string>{}(word) % v.size()] += 1.0f;
            word.clear();
        };
        for (char c : t) {
            if (std::isalnum(static_cast<unsigned char>(c))) word.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(c))));
            else flush();
        }
        flush();
        v[0] += 0.01f;  // never an all-zero vector
        out.push_back(v);
    }
    return out;
}

}  // namespace gaia_test
