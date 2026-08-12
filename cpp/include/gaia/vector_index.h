// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Flat (brute-force) vector index with persistence.
//
// Mirrors the only vector-search shapes the Python SDK actually uses --
// faiss.IndexFlatL2 (RAG, code index) and faiss.IndexFlatIP over L2-normalized
// vectors (procedural memory). Both are exhaustive scans, so the results here
// are exact, not approximate, and match Python vector-for-vector.

#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#include "gaia/export.h"

namespace gaia {

/// Similarity metric used by VectorIndex.
enum class Metric {
    /// Squared Euclidean distance, ranked ascending. Score = 1 / (1 + d²),
    /// the same convention as `faiss.IndexFlatL2` + GAIA's Python scoring.
    L2,
    /// Raw dot product, ranked descending. Combined with normalizeOnAdd this
    /// is cosine similarity, matching `faiss.IndexFlatIP` in procedural memory.
    InnerProduct,
};

/// Construction options for VectorIndex.
struct GAIA_API VectorIndexOptions {
    /// Metric used for ranking. Persisted in the .vec file.
    Metric metric = Metric::L2;

    /// L2-normalize every vector on add() -- and the query on search().
    /// With Metric::InnerProduct this makes search() return cosine similarity.
    bool normalizeOnAdd = false;

    /// Embedding model that produced the vectors (e.g. "nomic-embed-text-v1-GGUF").
    /// Persisted in the .vec file. When set, load() raises unless the file
    /// carries exactly this tag -- including when the file carries none at all,
    /// since an untagged file cannot back the provenance claim. Querying an
    /// index built by a different embedder returns confidently wrong rankings.
    /// Leave empty to accept whatever tag the file carries.
    std::string embeddingModel;

    /// Vector dimension. 0 means "infer from the first add() or load()".
    /// Once set, every add()/search() vector must match it exactly.
    size_t dimension = 0;
};

/// Flat vector index over float32 vectors, with save/load persistence.
///
/// Exhaustive scan on every search -- no IVF/HNSW/PQ. That is deliberate: the
/// Python SDK is flat-only too, so this returns identical rankings without
/// pulling in a BLAS-linked dependency.
///
/// Scores follow the Python convention exactly:
///   - Metric::L2           -> `1 / (1 + d²)` where d² is the *squared*
///                             Euclidean distance (what FAISS reports).
///   - Metric::InnerProduct -> the raw dot product.
/// Higher is always better, and results are sorted best-first. Ties are broken
/// by insertion order so results are deterministic across runs and platforms.
///
/// Nothing here is thread-safe; guard concurrent access externally.
///
/// ## Binary `.vec` file format (version 1)
///
/// All multi-byte integers are little-endian regardless of host byte order;
/// floats are IEEE-754 binary32 written little-endian.
///
/// ```text
/// Header:
///   offset  size  field
///   0       8     magic          "GAIAVEC\0"
///   8       4     uint32  version           (currently 1)
///   12      4     uint32  metric            (0 = L2, 1 = InnerProduct)
///   16      4     uint32  normalizeOnAdd    (0 or 1)
///   20      4     uint32  dimension
///   24      8     uint64  count             (number of vectors)
///   32      4     uint32  modelLength       (bytes, may be 0)
///   36      M     char[]  embeddingModel    (UTF-8, not NUL-terminated)
///
/// Then `count` records, back to back:
///   4               uint32   idLength (bytes, >= 1)
///   idLength        char[]   id (UTF-8, not NUL-terminated)
///   dimension * 4   float32  vector payload
/// ```
///
/// Records appear in insertion order, so a save/load round-trip preserves
/// ranking (including tie-breaks) exactly. `dimension` may be 0 only when
/// `count` is 0, ids must be unique, and payload floats must be finite --
/// load() rejects any file that breaks those rules rather than indexing it.
///
/// Usage:
/// @code
///   VectorIndexOptions opts;
///   opts.dimension = 768;
///   opts.embeddingModel = "nomic-embed-text-v1-GGUF";
///   VectorIndex index(opts);
///   index.add("chunk-0", embedding);
///   auto hits = index.search(queryEmbedding, 5);   // [(id, score), ...]
///   index.save("/path/to/index.vec");
/// @endcode
class GAIA_API VectorIndex {
public:
    /// Identifier type. Strings cover both callers: opaque record ids in
    /// procedural memory and stringified chunk offsets in RAG / code index.
    using Id = std::string;

    /// Magic bytes at the head of every .vec file.
    static constexpr const char* kMagic = "GAIAVEC";  ///< 7 chars + implicit NUL = 8 bytes.
    /// Current .vec format version written by save().
    static constexpr uint32_t kFormatVersion = 1;

    /// Construct an index. Defaults to Metric::L2 with an inferred dimension.
    explicit VectorIndex(VectorIndexOptions options = {});

    /// Convenience: fixed dimension, chosen metric, no normalization.
    VectorIndex(size_t dimension, Metric metric);

    /// Add a vector under a new id.
    /// @param id Non-empty identifier, unique within this index.
    /// @param vector Vector of exactly dimension() floats (or any non-empty
    ///        size if the dimension is still unset -- it is fixed from here on).
    /// @throws std::invalid_argument if the id is empty or already present, if
    ///         the vector is empty, has the wrong dimension, contains NaN/inf,
    ///         or is all-zero while normalizeOnAdd is enabled.
    void add(const Id& id, const std::vector<float>& vector);

    /// Add, or replace the vector already stored under @p id.
    /// Replacement keeps the id's original insertion position.
    /// @throws std::invalid_argument under the same conditions as add(),
    ///         except that an existing id is allowed.
    void upsert(const Id& id, const std::vector<float>& vector);

    /// Search for the @p k best matches, sorted best-first.
    /// @param query Vector of exactly dimension() floats.
    /// @param k Maximum results. Larger than size() returns everything.
    /// @return (id, score) pairs; empty if the index is empty or k is 0.
    /// @throws std::invalid_argument if the query dimension does not match, if
    ///         it contains NaN/inf, or if it is all-zero while normalizeOnAdd
    ///         is enabled. Never returns garbage rankings instead.
    std::vector<std::pair<Id, float>> search(const std::vector<float>& query, size_t k) const;

    /// Remove the vector stored under @p id.
    /// @return true if it was removed, false if the id was not present.
    /// @note O(size()) -- the remaining vectors keep their relative order.
    bool remove(const Id& id);

    /// Whether @p id is present in the index.
    bool contains(const Id& id) const;

    /// Copy the vector stored under @p id (post-normalization, as indexed).
    /// @throws std::out_of_range if the id is not present.
    std::vector<float> get(const Id& id) const;

    /// Ids in insertion order.
    const std::vector<Id>& ids() const { return ids_; }

    /// Number of vectors currently indexed.
    size_t size() const { return ids_.size(); }

    /// Vector dimension, or 0 if still unset (empty index, inferred dimension).
    size_t dimension() const { return dimension_; }

    /// Metric used for ranking.
    Metric metric() const { return metric_; }

    /// Whether vectors are L2-normalized on add and query.
    bool normalizeOnAdd() const { return normalize_; }

    /// Embedding model tag persisted alongside the vectors ("" if unset).
    const std::string& embeddingModel() const { return embeddingModel_; }

    /// Drop every vector. Dimension, metric, normalization and model tag are kept.
    void clear();

    /// Persist the index to @p path in the documented .vec format.
    /// Writes to `<path>.tmp` and renames, so a process that dies mid-write
    /// leaves the previous file intact. The temp file is not fsynced, so this
    /// does not survive a power loss, and the fixed `.tmp` name means two
    /// processes must not save to the same path concurrently.
    /// @throws std::runtime_error if the file cannot be written.
    void save(const std::string& path) const;

    /// Replace this index's contents with the .vec file at @p path.
    /// The file is authoritative for metric, normalization and dimension, so a
    /// loaded index ranks exactly as the saved one did -- construction-time
    /// metric/normalization are defaults for a *fresh* index, not assertions
    /// about the file. Check metric() afterwards if that matters. A fixed
    /// dimension or a configured embeddingModel that the file disagrees with is
    /// rejected rather than adopted. On any failure the index is left untouched.
    /// @throws std::runtime_error if the file is missing, truncated, has bad
    ///         magic, an unsupported version, or was built with a different
    ///         embedding model than this index is configured for.
    /// @throws std::invalid_argument if this index has a fixed dimension that
    ///         disagrees with the file's.
    void load(const std::string& path);

private:
    void validateVector(const std::vector<float>& vector, const char* what) const;
    std::vector<float> prepare(const std::vector<float>& vector, const char* what) const;
    std::vector<float> prepareQuery(const std::vector<float>& query) const;

    Metric metric_ = Metric::L2;
    bool normalize_ = false;
    std::string embeddingModel_;
    size_t dimension_ = 0;

    std::vector<Id> ids_;                              ///< Insertion order.
    std::vector<float> data_;                          ///< size() * dimension_ floats.
    std::unordered_map<Id, size_t> positions_;         ///< id -> row in ids_/data_.
};

}  // namespace gaia
