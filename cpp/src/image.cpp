// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Image loading, MIME detection, and RFC 4648 base64 encoding for VLM support.

#include "gaia/types.h"

#include <algorithm>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <string>
#include <sys/stat.h>
#include <system_error>

#if defined(_WIN32)
  #include <io.h>
#else
  #include <fcntl.h>
  #include <unistd.h>
#endif

namespace gaia {

// ---- RFC 4648 base64 (standard alphabet, padded) ----

static const char kB64Alphabet[] =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

std::string base64Encode(const std::uint8_t* data, std::size_t size) {
    if (size == 0 || data == nullptr) {
        return {};
    }
    std::string out;
    out.reserve(((size + 2) / 3) * 4);

    std::size_t i = 0;
    while (i + 3 <= size) {
        std::uint32_t n = (static_cast<std::uint32_t>(data[i]) << 16) |
                          (static_cast<std::uint32_t>(data[i + 1]) << 8) |
                          static_cast<std::uint32_t>(data[i + 2]);
        out.push_back(kB64Alphabet[(n >> 18) & 0x3F]);
        out.push_back(kB64Alphabet[(n >> 12) & 0x3F]);
        out.push_back(kB64Alphabet[(n >> 6)  & 0x3F]);
        out.push_back(kB64Alphabet[ n        & 0x3F]);
        i += 3;
    }
    std::size_t rem = size - i;
    if (rem == 1) {
        std::uint32_t n = static_cast<std::uint32_t>(data[i]) << 16;
        out.push_back(kB64Alphabet[(n >> 18) & 0x3F]);
        out.push_back(kB64Alphabet[(n >> 12) & 0x3F]);
        out.push_back('=');
        out.push_back('=');
    } else if (rem == 2) {
        std::uint32_t n = (static_cast<std::uint32_t>(data[i]) << 16) |
                          (static_cast<std::uint32_t>(data[i + 1]) << 8);
        out.push_back(kB64Alphabet[(n >> 18) & 0x3F]);
        out.push_back(kB64Alphabet[(n >> 12) & 0x3F]);
        out.push_back(kB64Alphabet[(n >> 6)  & 0x3F]);
        out.push_back('=');
    }
    return out;
}

// ---- MIME whitelist ----

static bool isAllowedMime(const std::string& m) {
    return m == "image/png"  || m == "image/jpeg" || m == "image/gif" ||
           m == "image/webp" || m == "image/bmp";
}

// ---- Image factories ----

Image Image::fromBytes(std::vector<std::uint8_t> bytes, const std::string& mimeType) {
    if (bytes.empty()) {
        throw std::invalid_argument("Image::fromBytes: empty bytes");
    }
    std::string mime;
    if (!mimeType.empty()) {
        if (!isAllowedMime(mimeType)) {
            throw std::invalid_argument(
                "Image::fromBytes: mimeType not in whitelist (png/jpeg/gif/webp/bmp)");
        }
        mime = mimeType;
    } else {
        mime = detectImageMimeType(bytes.data(), bytes.size());
        if (mime.empty()) {
            throw std::invalid_argument(
                "Image::fromBytes: unrecognized image format — "
                "pass an explicit mimeType (png/jpeg/gif/webp/bmp)");
        }
    }
    Image img;
    img.bytes_ = std::move(bytes);
    img.mimeType_ = std::move(mime);
    return img;
}

static std::vector<std::uint8_t> readFileBytesSecure(const std::string& path) {
    namespace fs = std::filesystem;
    std::error_code ec;
    fs::file_status st = fs::symlink_status(path, ec);
    if (ec) {
        throw std::runtime_error("Image::fromFile: cannot stat path");
    }
    if (!fs::exists(st)) {
        throw std::runtime_error("Image::fromFile: file does not exist");
    }
    // Reject symlinks, directories, FIFOs, devices — regular files only.
    if (!fs::is_regular_file(st)) {
        throw std::invalid_argument("Image::fromFile: not a regular file");
    }

    // Open with no-follow where supported (POSIX); on Windows plain fstream.
#if defined(O_NOFOLLOW) && !defined(_WIN32)
    int fd = ::open(path.c_str(), O_RDONLY | O_NOFOLLOW);
    if (fd < 0) {
        // ELOOP means the path is (or became) a symlink — surface as security rejection.
        throw std::runtime_error("Image::fromFile: cannot open file");
    }
    // RAII guard — closes fd on any early-exit (exception or return).
    struct FdGuard {
        int fd;
        ~FdGuard() noexcept { if (fd >= 0) ::close(fd); }
    } fdGuard{fd};

    struct stat sb;
    if (::fstat(fd, &sb) != 0) {
        throw std::runtime_error("Image::fromFile: fstat failed");
    }
    if (!S_ISREG(sb.st_mode)) {
        throw std::invalid_argument("Image::fromFile: not a regular file (post-open)");
    }
    auto sz = static_cast<std::uintmax_t>(sb.st_size);
    if (sz == 0) {
        throw std::invalid_argument("Image::fromFile: empty file");
    }
    if (sz > static_cast<std::uintmax_t>(GAIA_MAX_IMAGE_BYTES)) {
        throw std::invalid_argument("Image::fromFile: file exceeds GAIA_MAX_IMAGE_BYTES");
    }
    // Allocation after the cap check (≤ 20 MiB): vector ctor may throw
    // std::bad_alloc; FdGuard ensures fd is closed even in that case.
    std::vector<std::uint8_t> bytes(static_cast<std::size_t>(sz));
    std::size_t total = 0;
    while (total < bytes.size()) {
        ssize_t n = ::read(fd, bytes.data() + total, bytes.size() - total);
        if (n <= 0) {
            throw std::runtime_error("Image::fromFile: read failed");
        }
        total += static_cast<std::size_t>(n);
    }
    return bytes;
#else
    std::uintmax_t sz = fs::file_size(path, ec);
    if (ec) throw std::runtime_error("Image::fromFile: file_size failed");
    if (sz == 0) throw std::invalid_argument("Image::fromFile: empty file");
    if (sz > static_cast<std::uintmax_t>(GAIA_MAX_IMAGE_BYTES)) {
        throw std::invalid_argument("Image::fromFile: file exceeds GAIA_MAX_IMAGE_BYTES");
    }
    std::ifstream f(path, std::ios::binary);
    if (!f.is_open()) {
        throw std::runtime_error("Image::fromFile: cannot open file");
    }
    std::vector<std::uint8_t> bytes(static_cast<std::size_t>(sz));
    f.read(reinterpret_cast<char*>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
    if (!f) throw std::runtime_error("Image::fromFile: read failed");
    return bytes;
#endif
}

Image Image::fromFile(const std::string& path) {
    auto bytes = readFileBytesSecure(path);
    std::string mime = detectImageMimeType(bytes.data(), bytes.size());
    if (mime.empty()) {
        throw std::invalid_argument(
            "Image::fromFile: unrecognized image format — "
            "supported: png, jpeg, gif, webp, bmp");
    }
    Image img;
    img.bytes_ = std::move(bytes);
    img.mimeType_ = std::move(mime);
    return img;
}

std::string Image::toDataUri() const {
    std::string uri = "data:";
    uri += mimeType_;
    uri += ";base64,";
    uri += base64Encode(bytes_.data(), bytes_.size());
    return uri;
}

ContentPart Image::toContentPart() const {
    return ContentPart::makeImageUrl(toDataUri());
}

} // namespace gaia
