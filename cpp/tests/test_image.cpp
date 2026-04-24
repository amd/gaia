// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Unit tests for gaia::Image and gaia::base64Encode (Ttest2).

#include <gtest/gtest.h>
#include <gaia/types.h>

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

#ifndef GAIA_TEST_FIXTURES_DIR
#define GAIA_TEST_FIXTURES_DIR "cpp/tests/fixtures"
#endif

using namespace gaia;

static std::string fixturePath(const std::string& name) {
    return std::string(GAIA_TEST_FIXTURES_DIR) + "/" + name;
}

// ---- Base64 (AC-5) ----

TEST(ImageTest, Base64EncodeEmpty) {
    EXPECT_EQ(base64Encode(nullptr, 0), "");
    std::vector<std::uint8_t> empty;
    EXPECT_EQ(base64Encode(empty), "");
}
TEST(ImageTest, Base64EncodeOneByte) {
    std::vector<std::uint8_t> v = {'M'};
    EXPECT_EQ(base64Encode(v), "TQ==");
}
TEST(ImageTest, Base64EncodeTwoBytes) {
    std::vector<std::uint8_t> v = {'M','a'};
    EXPECT_EQ(base64Encode(v), "TWE=");
}
TEST(ImageTest, Base64EncodeThreeBytes) {
    std::vector<std::uint8_t> v = {'M','a','n'};
    EXPECT_EQ(base64Encode(v), "TWFu");
}
TEST(ImageTest, Base64EncodeKnownVectorRfc4648) {
    // RFC 4648 test vector: "foobar" -> "Zm9vYmFy"
    std::vector<std::uint8_t> v = {'f','o','o','b','a','r'};
    EXPECT_EQ(base64Encode(v), "Zm9vYmFy");
    // "foob" -> "Zm9vYg==", "fooba" -> "Zm9vYmE="
    EXPECT_EQ(base64Encode(std::vector<std::uint8_t>{'f','o','o','b'}), "Zm9vYg==");
    EXPECT_EQ(base64Encode(std::vector<std::uint8_t>{'f','o','o','b','a'}), "Zm9vYmE=");
}

// ---- Independent base64 decoder for AC-15h round-trip ----

static std::vector<std::uint8_t> independentBase64Decode(const std::string& s) {
    // Simple standard-alphabet decoder. Padding is optional.
    int table[256];
    for (int i = 0; i < 256; ++i) table[i] = -1;
    const char* alpha = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    for (int i = 0; i < 64; ++i) table[static_cast<unsigned char>(alpha[i])] = i;

    std::vector<std::uint8_t> out;
    int val = 0, bits = -8;
    for (unsigned char c : s) {
        if (c == '=') break;
        int d = table[c];
        if (d < 0) continue;
        val = (val << 6) | d;
        bits += 6;
        if (bits >= 0) {
            out.push_back(static_cast<std::uint8_t>((val >> bits) & 0xFF));
            bits -= 8;
        }
    }
    return out;
}

TEST(ImageTest, Base64RoundTripAgainstIndependentDecoder) {
    std::vector<std::uint8_t> original;
    for (int i = 0; i < 256; ++i) original.push_back(static_cast<std::uint8_t>(i));
    std::string encoded = base64Encode(original);
    auto decoded = independentBase64Decode(encoded);
    EXPECT_EQ(decoded, original);
}

TEST(ImageTest, Base64EncodeRoundTrip256Bytes) {
    std::vector<std::uint8_t> bytes(256);
    for (std::size_t i = 0; i < bytes.size(); ++i) bytes[i] = static_cast<std::uint8_t>(i);
    std::string enc = base64Encode(bytes);
    // Every 3 bytes → 4 base64 chars; for 256 bytes = 256/3 = 85 remainder 1
    // Expected length: ceil(256/3)*4 = 344
    EXPECT_EQ(enc.size(), 344u);
    EXPECT_EQ(enc.back(), '=');
}

// ---- Image::fromBytes (AC-1, AC-2, AC-15f) ----

TEST(ImageTest, ImageFromBytesDetectsAllFormats) {
    struct Case { std::vector<std::uint8_t> data; std::string expected; };
    std::vector<Case> cases = {
        {{0x89,'P','N','G',0x0D,0x0A,0x1A,0x0A,0,0,0,13}, "image/png"},
        {{0xFF,0xD8,0xFF,0xE0,0,0x10,'J','F','I','F',0,0}, "image/jpeg"},
        {{'G','I','F','8','9','a',0,0,0,0,0,0}, "image/gif"},
        {{'R','I','F','F',0x24,0,0,0,'W','E','B','P'}, "image/webp"},
        {{'B','M',0,0,0,0,0,0,0,0,0,0}, "image/bmp"},
    };
    for (auto& c : cases) {
        Image img = Image::fromBytes(c.data);
        EXPECT_EQ(img.mimeType(), c.expected);
    }
}

TEST(ImageTest, ImageFromBytesExplicitMime) {
    std::vector<std::uint8_t> bytes = {0x00, 0x01, 0x02, 0x03};
    Image img = Image::fromBytes(bytes, "image/jpeg");
    EXPECT_EQ(img.mimeType(), "image/jpeg");
}

TEST(ImageTest, ImageFromBytesEmptyThrows) {
    std::vector<std::uint8_t> empty;
    EXPECT_THROW(Image::fromBytes(empty), std::invalid_argument);
}

TEST(ImageTest, ImageFromBytesRejectsUnknownMime) {
    std::vector<std::uint8_t> bytes = {0x00, 0x01};
    EXPECT_THROW(Image::fromBytes(bytes, "text/html"), std::invalid_argument);
    EXPECT_THROW(Image::fromBytes(bytes, "application/octet-stream"),
                 std::invalid_argument);
}

TEST(ImageTest, ImageFromBytesAutoDetectUnrecognizedThrows) {
    // 12 zero bytes: no known magic pattern. detectImageMimeType returns "".
    // fromBytes (without explicit MIME) must throw std::invalid_argument.
    std::vector<std::uint8_t> bytes(12, 0x00);
    EXPECT_THROW(Image::fromBytes(bytes), std::invalid_argument);
    // 16 bytes of 0xFF: also unrecognized (not a JPEG — FF D8 FF needed)
    std::vector<std::uint8_t> bytes2(16, 0xFF);
    EXPECT_THROW(Image::fromBytes(bytes2), std::invalid_argument);
}

// ---- Image::fromFile (AC-3, AC-4, AC-15g) ----

TEST(ImageTest, ImageFromFileTinyPng) {
    std::string p = fixturePath("tiny.png");
    Image img = Image::fromFile(p);
    EXPECT_EQ(img.mimeType(), "image/png");
    EXPECT_GT(img.size(), 8u);
}

TEST(ImageTest, ImageFromFileTinyJpg) {
    std::string p = fixturePath("tiny.jpg");
    Image img = Image::fromFile(p);
    EXPECT_EQ(img.mimeType(), "image/jpeg");
}

TEST(ImageTest, ImageFromFileMissingThrows) {
    EXPECT_THROW(Image::fromFile("/nonexistent/path/doesnotexist.png"),
                 std::runtime_error);
}

TEST(ImageTest, ImageFromFileEmptyThrows) {
    std::string tmp = (std::filesystem::temp_directory_path() / "gaia_empty.png").string();
    { std::ofstream f(tmp, std::ios::binary); }
    EXPECT_THROW(Image::fromFile(tmp), std::invalid_argument);
    std::filesystem::remove(tmp);
}

TEST(ImageTest, ImageFromFileExceedsMaxThrows) {
    // Create an oversized file (GAIA_MAX_IMAGE_BYTES+1 bytes).
    std::string tmp = (std::filesystem::temp_directory_path() / "gaia_big.png").string();
    {
        std::ofstream f(tmp, std::ios::binary);
        std::vector<char> chunk(1024 * 1024, 'x');
        // Default cap 20 MiB — write 21 MiB.
        for (int i = 0; i < 21; ++i) f.write(chunk.data(), static_cast<std::streamsize>(chunk.size()));
    }
    EXPECT_THROW(Image::fromFile(tmp), std::invalid_argument);
    std::filesystem::remove(tmp);
}

TEST(ImageTest, ImageFromFileRejectsDirectory) {
    std::string dir = std::filesystem::temp_directory_path().string();
    EXPECT_THROW(Image::fromFile(dir), std::invalid_argument);
}

#ifndef _WIN32
TEST(ImageTest, ImageFromFileRejectsSymlink) {
    namespace fs = std::filesystem;
    std::string target = (fs::temp_directory_path() / "gaia_target.png").string();
    std::string link   = (fs::temp_directory_path() / "gaia_link.png").string();
    { std::ofstream f(target, std::ios::binary); f.put(static_cast<char>(0x89)); }
    fs::remove(link);
    std::error_code ec;
    fs::create_symlink(target, link, ec);
    if (ec) {
        GTEST_SKIP() << "symlink creation not permitted: " << ec.message();
    }
    EXPECT_THROW(Image::fromFile(link), std::invalid_argument);
    fs::remove(link);
    fs::remove(target);
}
#endif

// ---- toContentPart / toDataUri (AC-9 helpers) ----

TEST(ImageTest, ImageToContentPartDataUri) {
    std::vector<std::uint8_t> pngBytes = {0x89,'P','N','G',0x0D,0x0A,0x1A,0x0A,'A','B','C','D'};
    Image img = Image::fromBytes(pngBytes);
    ContentPart p = img.toContentPart();
    EXPECT_EQ(p.kind, ContentPart::Kind::IMAGE_URL);
    EXPECT_EQ(p.imageUrl.rfind("data:image/png;base64,", 0), 0u);
}
