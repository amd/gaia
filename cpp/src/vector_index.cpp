// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/vector_index.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <limits>
#include <stdexcept>
#include <string>
#include <system_error>

namespace fs = std::filesystem;

namespace gaia {

static_assert(sizeof(float) == 4, "VectorIndex .vec format requires 32-bit float");
static_assert(std::numeric_limits<float>::is_iec559,
              "VectorIndex .vec format requires IEEE-754 binary32 floats");

namespace {

// magic(8) + version(4) + metric(4) + normalize(4) + dimension(4) + count(8) + modelLength(4)
constexpr size_t kHeaderBytes = 36;

// --- little-endian primitives ------------------------------------------------
// Written byte by byte so a file produced on a big-endian host still reads back
// on a little-endian one.

void putU32(std::string& out, uint32_t v) {
    out.push_back(static_cast<char>(v & 0xFFu));
    out.push_back(static_cast<char>((v >> 8) & 0xFFu));
    out.push_back(static_cast<char>((v >> 16) & 0xFFu));
    out.push_back(static_cast<char>((v >> 24) & 0xFFu));
}

void putU64(std::string& out, uint64_t v) {
    for (int i = 0; i < 8; ++i) {
        out.push_back(static_cast<char>((v >> (8 * i)) & 0xFFu));
    }
}

void putFloat(std::string& out, float v) {
    uint32_t bits = 0;
    std::memcpy(&bits, &v, sizeof(bits));
    putU32(out, bits);
}

uint32_t getU32(const std::string& in, size_t offset) {
    return static_cast<uint32_t>(static_cast<unsigned char>(in[offset])) |
           (static_cast<uint32_t>(static_cast<unsigned char>(in[offset + 1])) << 8) |
           (static_cast<uint32_t>(static_cast<unsigned char>(in[offset + 2])) << 16) |
           (static_cast<uint32_t>(static_cast<unsigned char>(in[offset + 3])) << 24);
}

uint64_t getU64(const std::string& in, size_t offset) {
    uint64_t v = 0;
    for (int i = 0; i < 8; ++i) {
        v |= static_cast<uint64_t>(static_cast<unsigned char>(in[offset + static_cast<size_t>(i)]))
             << (8 * i);
    }
    return v;
}

float getFloat(const std::string& in, size_t offset) {
    uint32_t bits = getU32(in, offset);
    float v = 0.0f;
    std::memcpy(&v, &bits, sizeof(v));
    return v;
}

}  // namespace

// ---------------------------------------------------------------------------
// Construction
// ---------------------------------------------------------------------------

VectorIndex::VectorIndex(VectorIndexOptions options)
    : metric_(options.metric),
      normalize_(options.normalizeOnAdd),
      embeddingModel_(std::move(options.embeddingModel)),
      dimension_(options.dimension) {}

VectorIndex::VectorIndex(size_t dimension, Metric metric) : metric_(metric), dimension_(dimension) {}

// ---------------------------------------------------------------------------
// Validation helpers
// ---------------------------------------------------------------------------

void VectorIndex::validateVector(const std::vector<float>& vector, const char* what) const {
    if (vector.empty()) {
        throw std::invalid_argument(std::string("VectorIndex: ") + what +
                                    " vector is empty. Pass a vector of " +
                                    (dimension_ == 0 ? std::string("at least one float")
                                                     : std::to_string(dimension_) + " floats") +
                                    ".");
    }
    if (dimension_ != 0 && vector.size() != dimension_) {
        throw std::invalid_argument("VectorIndex: dimension mismatch on " + std::string(what) +
                                    " -- index expects " + std::to_string(dimension_) +
                                    " floats, got " + std::to_string(vector.size()) +
                                    ". Re-embed with the model this index was built from (" +
                                    (embeddingModel_.empty() ? "unspecified" : embeddingModel_) +
                                    "), or build a new index at dimension " +
                                    std::to_string(vector.size()) + ".");
    }
    for (size_t i = 0; i < vector.size(); ++i) {
        if (!std::isfinite(vector[i])) {
            throw std::invalid_argument("VectorIndex: " + std::string(what) +
                                        " vector contains a non-finite value at index " +
                                        std::to_string(i) +
                                        ". Check the embedding response before indexing.");
        }
    }
}

std::vector<float> VectorIndex::prepareQuery(const std::vector<float>& query) const {
    validateVector(query, "query");
    if (!normalize_) {
        return query;
    }
    double sumSq = 0.0;
    for (float v : query) {
        sumSq += static_cast<double>(v) * static_cast<double>(v);
    }
    if (sumSq <= 0.0) {
        throw std::invalid_argument(
            "VectorIndex: cannot L2-normalize an all-zero query vector. This index was created "
            "with normalizeOnAdd=true; supply a non-zero embedding.");
    }
    const float inv = static_cast<float>(1.0 / std::sqrt(sumSq));
    std::vector<float> out(query.size());
    for (size_t i = 0; i < query.size(); ++i) {
        out[i] = query[i] * inv;
    }
    return out;
}

std::vector<float> VectorIndex::prepare(const std::vector<float>& vector, const char* what) const {
    validateVector(vector, what);
    std::vector<float> out = vector;
    if (normalize_) {
        double sumSq = 0.0;
        for (float v : vector) {
            sumSq += static_cast<double>(v) * static_cast<double>(v);
        }
        if (sumSq <= 0.0) {
            throw std::invalid_argument(
                "VectorIndex: cannot L2-normalize an all-zero vector. This index was created with "
                "normalizeOnAdd=true; supply a non-zero embedding.");
        }
        const float inv = static_cast<float>(1.0 / std::sqrt(sumSq));
        for (float& v : out) {
            v *= inv;
        }
    }
    return out;
}

// ---------------------------------------------------------------------------
// Mutation
// ---------------------------------------------------------------------------

void VectorIndex::add(const Id& id, const std::vector<float>& vector) {
    if (id.empty()) {
        throw std::invalid_argument("VectorIndex: id must not be empty.");
    }
    if (positions_.count(id) != 0) {
        throw std::invalid_argument("VectorIndex: id '" + id +
                                    "' is already indexed. Use upsert() to replace it, or "
                                    "remove() it first.");
    }
    const std::vector<float> prepared = prepare(vector, "added");

    // Commit all three containers together: a throw partway through would
    // otherwise leave ids_ describing a row data_ does not have, and the next
    // search() would read past the buffer.
    const size_t dataBefore = data_.size();
    data_.insert(data_.end(), prepared.begin(), prepared.end());
    bool idPushed = false;
    try {
        ids_.push_back(id);
        idPushed = true;
        positions_.emplace(id, ids_.size() - 1);
    } catch (...) {
        if (idPushed) {
            ids_.pop_back();
        }
        data_.resize(dataBefore);
        throw;
    }
    dimension_ = prepared.size();
}

void VectorIndex::upsert(const Id& id, const std::vector<float>& vector) {
    if (id.empty()) {
        throw std::invalid_argument("VectorIndex: id must not be empty.");
    }
    auto it = positions_.find(id);
    if (it == positions_.end()) {
        add(id, vector);
        return;
    }
    const std::vector<float> prepared = prepare(vector, "upserted");
    const auto row = data_.begin() + static_cast<std::ptrdiff_t>(it->second * dimension_);
    std::copy(prepared.begin(), prepared.end(), row);
}

bool VectorIndex::remove(const Id& id) {
    auto it = positions_.find(id);
    if (it == positions_.end()) {
        return false;
    }
    const size_t pos = it->second;
    const auto first = data_.begin() + static_cast<std::ptrdiff_t>(pos * dimension_);
    data_.erase(first, first + static_cast<std::ptrdiff_t>(dimension_));
    ids_.erase(ids_.begin() + static_cast<std::ptrdiff_t>(pos));
    positions_.erase(it);
    for (auto& entry : positions_) {
        if (entry.second > pos) {
            --entry.second;
        }
    }
    return true;
}

bool VectorIndex::contains(const Id& id) const {
    return positions_.count(id) != 0;
}

std::vector<float> VectorIndex::get(const Id& id) const {
    auto it = positions_.find(id);
    if (it == positions_.end()) {
        throw std::out_of_range("VectorIndex: no vector indexed under id '" + id + "'.");
    }
    const auto first = data_.begin() + static_cast<std::ptrdiff_t>(it->second * dimension_);
    return std::vector<float>(first, first + static_cast<std::ptrdiff_t>(dimension_));
}

void VectorIndex::clear() {
    ids_.clear();
    data_.clear();
    positions_.clear();
}

// ---------------------------------------------------------------------------
// Search
// ---------------------------------------------------------------------------

std::vector<std::pair<VectorIndex::Id, float>> VectorIndex::search(const std::vector<float>& query,
                                                                   size_t k) const {
    // Validate before the empty-index shortcut: a wrong-sized query is a caller
    // bug either way, and returning [] would hide it until the index fills up.
    const std::vector<float> q = prepareQuery(query);

    if (ids_.empty() || k == 0) {
        return {};
    }

    std::vector<std::pair<float, size_t>> scored;
    scored.reserve(ids_.size());
    for (size_t row = 0; row < ids_.size(); ++row) {
        const float* vec = data_.data() + row * dimension_;
        float score = 0.0f;
        if (metric_ == Metric::L2) {
            // Squared Euclidean distance -- what faiss.IndexFlatL2 reports --
            // then Python's 1/(1+d²) similarity conversion.
            double sumSq = 0.0;
            for (size_t i = 0; i < dimension_; ++i) {
                const double diff = static_cast<double>(q[i]) - static_cast<double>(vec[i]);
                sumSq += diff * diff;
            }
            score = static_cast<float>(1.0 / (1.0 + sumSq));
        } else {
            double dot = 0.0;
            for (size_t i = 0; i < dimension_; ++i) {
                dot += static_cast<double>(q[i]) * static_cast<double>(vec[i]);
            }
            score = static_cast<float>(dot);
        }
        scored.emplace_back(score, row);
    }

    const size_t take = std::min(k, scored.size());
    // Higher score first; equal scores keep insertion order, so ties are stable
    // across runs and platforms.
    const auto better = [](const std::pair<float, size_t>& a, const std::pair<float, size_t>& b) {
        if (a.first != b.first) {
            return a.first > b.first;
        }
        return a.second < b.second;
    };
    std::partial_sort(scored.begin(), scored.begin() + static_cast<std::ptrdiff_t>(take),
                      scored.end(), better);

    std::vector<std::pair<Id, float>> results;
    results.reserve(take);
    for (size_t i = 0; i < take; ++i) {
        results.emplace_back(ids_[scored[i].second], scored[i].first);
    }
    return results;
}

// ---------------------------------------------------------------------------
// Persistence
// ---------------------------------------------------------------------------

void VectorIndex::save(const std::string& path) const {
    // The format stores these as uint32; refuse rather than silently truncate.
    if (dimension_ > std::numeric_limits<uint32_t>::max()) {
        throw std::runtime_error("VectorIndex::save: dimension " + std::to_string(dimension_) +
                                 " exceeds the .vec format limit of 4294967295.");
    }
    if (embeddingModel_.size() > std::numeric_limits<uint32_t>::max()) {
        throw std::runtime_error("VectorIndex::save: embedding-model name is too long for the "
                                 ".vec format (limit 4294967295 bytes).");
    }

    std::string buf;
    buf.reserve(kHeaderBytes + embeddingModel_.size() +
                ids_.size() * (4 + 8 + dimension_ * sizeof(float)));

    buf.append(kMagic, 7);
    buf.push_back('\0');
    putU32(buf, kFormatVersion);
    putU32(buf, metric_ == Metric::L2 ? 0u : 1u);
    putU32(buf, normalize_ ? 1u : 0u);
    putU32(buf, static_cast<uint32_t>(dimension_));
    putU64(buf, static_cast<uint64_t>(ids_.size()));
    putU32(buf, static_cast<uint32_t>(embeddingModel_.size()));
    buf.append(embeddingModel_);

    for (size_t row = 0; row < ids_.size(); ++row) {
        if (ids_[row].size() > std::numeric_limits<uint32_t>::max()) {
            throw std::runtime_error("VectorIndex::save: id at row " + std::to_string(row) +
                                     " is too long for the .vec format (limit 4294967295 bytes).");
        }
        putU32(buf, static_cast<uint32_t>(ids_[row].size()));
        buf.append(ids_[row]);
        const float* vec = data_.data() + row * dimension_;
        for (size_t i = 0; i < dimension_; ++i) {
            putFloat(buf, vec[i]);
        }
    }

    const fs::path target(path);
    std::error_code ec;
    if (target.has_parent_path() && !target.parent_path().empty()) {
        fs::create_directories(target.parent_path(), ec);
        if (ec) {
            throw std::runtime_error("VectorIndex::save: cannot create directory '" +
                                     target.parent_path().string() + "': " + ec.message());
        }
    }

    // Write to a sibling temp file and rename, so an interrupted save never
    // leaves a half-written index where a good one used to be.
    const fs::path tmp = target.string() + ".tmp";
    {
        std::ofstream out(tmp, std::ios::binary | std::ios::trunc);
        if (!out) {
            throw std::runtime_error("VectorIndex::save: cannot open '" + tmp.string() +
                                     "' for writing. Check the path exists and is writable.");
        }
        out.write(buf.data(), static_cast<std::streamsize>(buf.size()));
        out.flush();
        if (!out) {
            throw std::runtime_error("VectorIndex::save: failed writing " +
                                     std::to_string(buf.size()) + " bytes to '" + tmp.string() +
                                     "'. The disk may be full.");
        }
    }
    fs::rename(tmp, target, ec);
    if (ec) {
        std::error_code cleanupEc;
        fs::remove(tmp, cleanupEc);  // best effort -- the rename error is the one worth reporting
        throw std::runtime_error("VectorIndex::save: cannot move '" + tmp.string() + "' onto '" +
                                 target.string() + "': " + ec.message());
    }
}

void VectorIndex::load(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        throw std::runtime_error("VectorIndex::load: no such file '" + path +
                                 "'. Build and save() the index before loading it.");
    }
    std::string buf((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    if (!in.eof() && in.fail()) {
        throw std::runtime_error("VectorIndex::load: failed reading '" + path + "'.");
    }

    const auto truncated = [&path](const std::string& what) {
        return std::runtime_error("VectorIndex::load: '" + path + "' is truncated or corrupt (" +
                                  what + "). Delete it and rebuild the index.");
    };

    if (buf.size() < kHeaderBytes) {
        throw truncated("header shorter than 36 bytes");
    }
    if (std::memcmp(buf.data(), kMagic, 7) != 0 || buf[7] != '\0') {
        throw std::runtime_error("VectorIndex::load: '" + path +
                                 "' is not a GAIA .vec file (bad magic bytes). Note that C++ "
                                 "cannot read Python's FAISS index.faiss files.");
    }

    const uint32_t version = getU32(buf, 8);
    if (version != kFormatVersion) {
        throw std::runtime_error("VectorIndex::load: '" + path + "' has .vec format version " +
                                 std::to_string(version) + ", but this build reads version " +
                                 std::to_string(kFormatVersion) + ". Rebuild the index.");
    }

    const uint32_t metricCode = getU32(buf, 12);
    if (metricCode > 1) {
        throw std::runtime_error("VectorIndex::load: '" + path + "' declares unknown metric code " +
                                 std::to_string(metricCode) + " (expected 0=L2 or 1=InnerProduct).");
    }
    const Metric fileMetric = metricCode == 0 ? Metric::L2 : Metric::InnerProduct;
    const bool fileNormalize = getU32(buf, 16) != 0;
    const size_t fileDim = getU32(buf, 20);
    const uint64_t count = getU64(buf, 24);
    const size_t modelLen = getU32(buf, 32);

    // dimension 0 is legal only for an index that was saved while still empty.
    if (fileDim == 0 && count != 0) {
        throw truncated("dimension is 0 but the header declares " + std::to_string(count) +
                        " vectors");
    }
    if (buf.size() < kHeaderBytes + modelLen) {
        throw truncated("embedding-model name runs past end of file");
    }
    const std::string fileModel = buf.substr(kHeaderBytes, modelLen);

    // Querying an index built by a different embedder returns confidently wrong
    // rankings, so refuse rather than "work" -- same guard as Python's
    // CodeIndexSDK.search().
    if (!embeddingModel_.empty() && fileModel != embeddingModel_) {
        const std::string built =
            fileModel.empty() ? std::string("carries no embedding-model tag")
                              : ("was built with '" + fileModel + "'");
        throw std::runtime_error("VectorIndex::load: embedding-model mismatch -- '" + path + "' " +
                                 built + ", but this index is configured for '" + embeddingModel_ +
                                 "'. Rebuild the index with the current model, or construct "
                                 "VectorIndex without an embeddingModel to accept the file's.");
    }
    if (dimension_ != 0 && fileDim != 0 && fileDim != dimension_) {
        throw std::invalid_argument("VectorIndex::load: dimension mismatch -- '" + path +
                                    "' holds " + std::to_string(fileDim) +
                                    "-dimensional vectors, but this index is fixed at " +
                                    std::to_string(dimension_) +
                                    ". Rebuild the index, or construct VectorIndex with "
                                    "dimension=" + std::to_string(fileDim) + ".");
    }

    // Sanity-check the declared count against the bytes that actually follow
    // *before* reserving anything -- otherwise a corrupt header asking for 2^60
    // vectors turns into a multi-terabyte allocation (or a bare std::length_error
    // that names nothing) instead of a readable error.
    const uint64_t payloadBytes = buf.size() - (kHeaderBytes + modelLen);
    const uint64_t minRecordBytes = 4 + 1 + static_cast<uint64_t>(fileDim) * sizeof(float);
    if (count > payloadBytes / minRecordBytes) {
        throw truncated("header declares " + std::to_string(count) + " vectors but only " +
                        std::to_string(payloadBytes) + " bytes of records follow");
    }

    std::vector<Id> ids;
    std::vector<float> data;
    std::unordered_map<Id, size_t> positions;
    ids.reserve(static_cast<size_t>(count));
    data.reserve(static_cast<size_t>(count) * fileDim);

    size_t offset = kHeaderBytes + modelLen;
    for (uint64_t row = 0; row < count; ++row) {
        if (buf.size() - offset < 4) {
            throw truncated("record header past end of file");
        }
        const uint64_t idLen = getU32(buf, offset);
        offset += 4;
        if (idLen == 0) {
            throw truncated("record with an empty id");
        }
        // 64-bit arithmetic so the guard still holds on a 32-bit target, where
        // fileDim * 4 could otherwise wrap and let the read walk off the buffer.
        const uint64_t need = idLen + static_cast<uint64_t>(fileDim) * sizeof(float);
        if (buf.size() - offset < need) {
            throw truncated("record payload past end of file");
        }
        Id id = buf.substr(offset, static_cast<size_t>(idLen));
        offset += static_cast<size_t>(idLen);
        if (positions.count(id) != 0) {
            throw std::runtime_error("VectorIndex::load: '" + path + "' contains duplicate id '" +
                                     id + "'. Delete it and rebuild the index.");
        }
        for (size_t i = 0; i < fileDim; ++i) {
            const float value = getFloat(buf, offset);
            // Same guard add() applies: a NaN slipping in would poison every
            // ranking (and break the search comparator's ordering).
            if (!std::isfinite(value)) {
                throw truncated("vector for id '" + id + "' has a non-finite value at index " +
                                std::to_string(i));
            }
            data.push_back(value);
            offset += sizeof(float);
        }
        positions.emplace(id, ids.size());
        ids.push_back(std::move(id));
    }
    if (offset != buf.size()) {
        throw std::runtime_error("VectorIndex::load: '" + path + "' has " +
                                 std::to_string(buf.size() - offset) +
                                 " trailing bytes after " + std::to_string(count) +
                                 " vectors -- file is corrupt. Delete it and rebuild the index.");
    }

    // Commit only once everything parsed; a failed load leaves the index as-is.
    metric_ = fileMetric;
    normalize_ = fileNormalize;
    if (fileDim != 0) {
        dimension_ = fileDim;
    }
    if (embeddingModel_.empty()) {
        embeddingModel_ = fileModel;
    }
    ids_ = std::move(ids);
    data_ = std::move(data);
    positions_ = std::move(positions);
}

}  // namespace gaia
