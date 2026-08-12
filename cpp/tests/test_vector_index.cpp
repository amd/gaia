// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/vector_index.h>

#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

using namespace gaia;
namespace fs = std::filesystem;

namespace {

/// Read a whole file as raw bytes (used to assert the documented .vec layout).
std::string readAll(const fs::path& p) {
    std::ifstream in(p, std::ios::binary);
    return std::string((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
}

uint32_t leU32(const std::string& s, size_t off) {
    return static_cast<uint32_t>(static_cast<unsigned char>(s[off])) |
           (static_cast<uint32_t>(static_cast<unsigned char>(s[off + 1])) << 8) |
           (static_cast<uint32_t>(static_cast<unsigned char>(s[off + 2])) << 16) |
           (static_cast<uint32_t>(static_cast<unsigned char>(s[off + 3])) << 24);
}

uint64_t leU64(const std::string& s, size_t off) {
    uint64_t v = 0;
    for (int i = 0; i < 8; ++i) {
        v |= static_cast<uint64_t>(static_cast<unsigned char>(s[off + static_cast<size_t>(i)]))
             << (8 * i);
    }
    return v;
}

void putU32(std::string& out, uint32_t v) {
    for (int i = 0; i < 4; ++i) {
        out.push_back(static_cast<char>((v >> (8 * i)) & 0xFFu));
    }
}

void putU64(std::string& out, uint64_t v) {
    for (int i = 0; i < 8; ++i) {
        out.push_back(static_cast<char>((v >> (8 * i)) & 0xFFu));
    }
}

void putFloatBits(std::string& out, uint32_t bits) {
    putU32(out, bits);
}

/// Hand-build a .vec header so corrupt-file paths can be exercised directly.
std::string makeHeader(uint32_t dim, uint64_t count, const std::string& model = "",
                       uint32_t version = 1, uint32_t metric = 0, uint32_t normalize = 0) {
    std::string h;
    h.append("GAIAVEC", 7);
    h.push_back('\0');
    putU32(h, version);
    putU32(h, metric);
    putU32(h, normalize);
    putU32(h, dim);
    putU64(h, count);
    putU32(h, static_cast<uint32_t>(model.size()));
    h.append(model);
    return h;
}

void writeFile(const fs::path& p, const std::string& bytes) {
    std::ofstream out(p, std::ios::binary | std::ios::trunc);
    out.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
}

std::vector<std::string> idsOf(const std::vector<std::pair<VectorIndex::Id, float>>& hits) {
    std::vector<std::string> out;
    out.reserve(hits.size());
    for (const auto& h : hits) {
        out.push_back(h.first);
    }
    return out;
}

// ---------------------------------------------------------------------------
// Parity corpus — the exact vectors fed to faiss.IndexFlatL2 / IndexFlatIP when
// the expected orderings below were generated. Keep both in sync.
// ---------------------------------------------------------------------------

const std::vector<std::vector<float>> kParityVectors = {
    {0.10f, 0.20f, 0.30f, 0.40f},     // 0
    {0.90f, 0.10f, 0.05f, 0.00f},     // 1
    {-0.50f, 0.25f, 0.75f, -0.25f},   // 2
    {0.33f, 0.33f, 0.33f, 0.33f},     // 3
    {1.00f, 0.00f, 0.00f, 0.00f},     // 4
    {0.00f, 1.00f, 0.00f, 0.00f},     // 5
    {0.00f, 0.00f, 1.00f, 0.00f},     // 6
    {0.00f, 0.00f, 0.00f, 1.00f},     // 7
    {0.15f, 0.25f, 0.35f, 0.45f},     // 8
    {-0.90f, -0.80f, 0.10f, 0.60f},   // 9
};
const std::vector<float> kParityQuery = {0.12f, 0.22f, 0.32f, 0.42f};

class VectorIndexTest : public ::testing::Test {
protected:
    fs::path tmpDir;

    void SetUp() override {
        tmpDir = fs::temp_directory_path() / "gaia_vector_index_test";
        fs::remove_all(tmpDir);
        fs::create_directories(tmpDir);
    }

    void TearDown() override {
        fs::remove_all(tmpDir);
    }

    fs::path path(const std::string& name) const { return tmpDir / name; }
};

}  // namespace

// ---------------------------------------------------------------------------
// Hand-computed correctness — L2
// ---------------------------------------------------------------------------

// Vectors: a=(0,0) b=(3,4) c=(1,0); query=(0,0).
// Squared L2: a=0, c=1, b=25  ->  scores 1/(1+d²) = 1.0, 0.5, 1/26.
TEST_F(VectorIndexTest, L2ScoresMatchHandComputedDistances) {
    VectorIndex index(2, Metric::L2);
    index.add("a", {0.0f, 0.0f});
    index.add("b", {3.0f, 4.0f});
    index.add("c", {1.0f, 0.0f});

    auto hits = index.search({0.0f, 0.0f}, 3);
    ASSERT_EQ(hits.size(), 3u);
    EXPECT_EQ(idsOf(hits), (std::vector<std::string>{"a", "c", "b"}));
    EXPECT_FLOAT_EQ(hits[0].second, 1.0f);
    EXPECT_FLOAT_EQ(hits[1].second, 0.5f);
    EXPECT_FLOAT_EQ(hits[2].second, 1.0f / 26.0f);
}

// The score convention is Python's: 1/(1 + squared-L2), not 1/(1 + euclidean).
// Query (0,0) vs (3,4): euclidean 5 would give 1/6; squared 25 gives 1/26.
TEST_F(VectorIndexTest, L2UsesSquaredDistanceLikeFaiss) {
    VectorIndex index(2, Metric::L2);
    index.add("b", {3.0f, 4.0f});

    auto hits = index.search({0.0f, 0.0f}, 1);
    ASSERT_EQ(hits.size(), 1u);
    EXPECT_FLOAT_EQ(hits[0].second, 1.0f / 26.0f);
    EXPECT_NE(hits[0].second, 1.0f / 6.0f);
}

// ---------------------------------------------------------------------------
// Hand-computed correctness — inner product
// ---------------------------------------------------------------------------

// query=(1,2,3); dots: x=(1,0,0)->1, y=(0,1,0)->2, z=(0,0,1)->3, n=(-1,-1,-1)->-6.
TEST_F(VectorIndexTest, InnerProductScoresAreRawDotProducts) {
    VectorIndex index(3, Metric::InnerProduct);
    index.add("x", {1.0f, 0.0f, 0.0f});
    index.add("y", {0.0f, 1.0f, 0.0f});
    index.add("z", {0.0f, 0.0f, 1.0f});
    index.add("n", {-1.0f, -1.0f, -1.0f});

    auto hits = index.search({1.0f, 2.0f, 3.0f}, 4);
    ASSERT_EQ(hits.size(), 4u);
    EXPECT_EQ(idsOf(hits), (std::vector<std::string>{"z", "y", "x", "n"}));
    EXPECT_FLOAT_EQ(hits[0].second, 3.0f);
    EXPECT_FLOAT_EQ(hits[1].second, 2.0f);
    EXPECT_FLOAT_EQ(hits[2].second, 1.0f);
    EXPECT_FLOAT_EQ(hits[3].second, -6.0f);
}

// normalizeOnAdd + InnerProduct = cosine similarity (procedural memory's setup).
// (2,0) and (7,0) both normalize to (1,0); query (3,3) normalizes to
// (1/sqrt2, 1/sqrt2), so cos = 1/sqrt2 for both, and 1.0 for the diagonal.
TEST_F(VectorIndexTest, NormalizeOnAddGivesCosineSimilarity) {
    VectorIndexOptions opts;
    opts.metric = Metric::InnerProduct;
    opts.normalizeOnAdd = true;
    opts.dimension = 2;
    VectorIndex index(opts);
    index.add("east-short", {2.0f, 0.0f});
    index.add("east-long", {7.0f, 0.0f});
    index.add("diagonal", {5.0f, 5.0f});

    auto hits = index.search({3.0f, 3.0f}, 3);
    ASSERT_EQ(hits.size(), 3u);
    EXPECT_EQ(hits[0].first, "diagonal");
    EXPECT_NEAR(hits[0].second, 1.0f, 1e-6f);
    // Magnitude is normalized away, so the two east vectors tie exactly.
    EXPECT_NEAR(hits[1].second, static_cast<float>(1.0 / std::sqrt(2.0)), 1e-6f);
    EXPECT_FLOAT_EQ(hits[1].second, hits[2].second);
    // Stored vectors are the normalized ones.
    auto stored = index.get("east-long");
    ASSERT_EQ(stored.size(), 2u);
    EXPECT_FLOAT_EQ(stored[0], 1.0f);
    EXPECT_FLOAT_EQ(stored[1], 0.0f);
}

TEST_F(VectorIndexTest, NormalizeRejectsZeroVectors) {
    VectorIndexOptions opts;
    opts.metric = Metric::InnerProduct;
    opts.normalizeOnAdd = true;
    opts.dimension = 2;
    VectorIndex index(opts);

    EXPECT_THROW(index.add("zero", {0.0f, 0.0f}), std::invalid_argument);
    index.add("ok", {1.0f, 0.0f});
    EXPECT_THROW(index.search({0.0f, 0.0f}, 1), std::invalid_argument);
}

// ---------------------------------------------------------------------------
// Top-k, ties, degenerate k
// ---------------------------------------------------------------------------

TEST_F(VectorIndexTest, TopKReturnsOnlyKBestInOrder) {
    VectorIndex index(1, Metric::L2);
    index.add("d0", {0.0f});
    index.add("d3", {3.0f});
    index.add("d1", {1.0f});
    index.add("d2", {2.0f});

    auto hits = index.search({0.0f}, 2);
    ASSERT_EQ(hits.size(), 2u);
    EXPECT_EQ(idsOf(hits), (std::vector<std::string>{"d0", "d1"}));
    EXPECT_GT(hits[0].second, hits[1].second);
}

TEST_F(VectorIndexTest, KLargerThanIndexReturnsEverything) {
    VectorIndex index(1, Metric::L2);
    index.add("a", {0.0f});
    index.add("b", {1.0f});

    auto hits = index.search({0.0f}, 100);
    EXPECT_EQ(idsOf(hits), (std::vector<std::string>{"a", "b"}));
}

TEST_F(VectorIndexTest, KZeroReturnsNoResults) {
    VectorIndex index(1, Metric::L2);
    index.add("a", {0.0f});
    EXPECT_TRUE(index.search({0.0f}, 0).empty());
}

TEST_F(VectorIndexTest, TiesKeepInsertionOrder) {
    VectorIndex index(2, Metric::L2);
    index.add("first", {1.0f, 0.0f});
    index.add("second", {0.0f, 1.0f});
    index.add("third", {-1.0f, 0.0f});
    index.add("fourth", {0.0f, -1.0f});

    // Every vector is exactly distance 1 from the origin.
    auto hits = index.search({0.0f, 0.0f}, 4);
    ASSERT_EQ(hits.size(), 4u);
    EXPECT_EQ(idsOf(hits), (std::vector<std::string>{"first", "second", "third", "fourth"}));
    for (const auto& h : hits) {
        EXPECT_FLOAT_EQ(h.second, 0.5f);
    }
    // Truncating to k must take the first ones by insertion order, not arbitrary ones.
    EXPECT_EQ(idsOf(index.search({0.0f, 0.0f}, 2)),
              (std::vector<std::string>{"first", "second"}));
}

TEST_F(VectorIndexTest, SearchOnEmptyIndexReturnsEmpty) {
    VectorIndex index(4, Metric::L2);
    EXPECT_EQ(index.size(), 0u);
    EXPECT_TRUE(index.search({1.0f, 2.0f, 3.0f, 4.0f}, 5).empty());
}

// ---------------------------------------------------------------------------
// Guards — never silently return garbage
// ---------------------------------------------------------------------------

TEST_F(VectorIndexTest, AddWithWrongDimensionThrowsAndNamesBothSizes) {
    VectorIndex index(4, Metric::L2);
    try {
        index.add("bad", {1.0f, 2.0f});
        FAIL() << "expected a dimension-mismatch throw";
    } catch (const std::invalid_argument& e) {
        const std::string msg = e.what();
        EXPECT_NE(msg.find("expects 4"), std::string::npos) << msg;
        EXPECT_NE(msg.find("got 2"), std::string::npos) << msg;
    }
    EXPECT_EQ(index.size(), 0u);
}

TEST_F(VectorIndexTest, SearchWithWrongDimensionThrowsEvenOnEmptyIndex) {
    VectorIndex empty(4, Metric::L2);
    EXPECT_THROW(empty.search({1.0f}, 1), std::invalid_argument);

    VectorIndex index(4, Metric::L2);
    index.add("a", {1.0f, 0.0f, 0.0f, 0.0f});
    EXPECT_THROW(index.search({1.0f, 0.0f}, 1), std::invalid_argument);
}

TEST_F(VectorIndexTest, InferredDimensionIsFixedByTheFirstAdd) {
    VectorIndex index;  // dimension unset
    EXPECT_EQ(index.dimension(), 0u);
    index.add("a", {1.0f, 2.0f, 3.0f});
    EXPECT_EQ(index.dimension(), 3u);
    EXPECT_THROW(index.add("b", {1.0f, 2.0f}), std::invalid_argument);
}

TEST_F(VectorIndexTest, EmptyAndNonFiniteInputsAreRejected) {
    VectorIndex index(2, Metric::L2);
    EXPECT_THROW(index.add("empty", {}), std::invalid_argument);
    EXPECT_THROW(index.add("nan", {std::nanf(""), 0.0f}), std::invalid_argument);
    EXPECT_THROW(index.add("inf", {std::numeric_limits<float>::infinity(), 0.0f}),
                 std::invalid_argument);
    EXPECT_THROW(index.add("", {1.0f, 2.0f}), std::invalid_argument);
    EXPECT_EQ(index.size(), 0u);
}

TEST_F(VectorIndexTest, DuplicateAddThrowsButUpsertReplaces) {
    VectorIndex index(2, Metric::L2);
    index.add("a", {1.0f, 1.0f});
    index.add("b", {5.0f, 5.0f});
    EXPECT_THROW(index.add("a", {9.0f, 9.0f}), std::invalid_argument);

    index.upsert("a", {4.9f, 4.9f});
    EXPECT_EQ(index.size(), 2u);
    // "a" keeps its slot but now sits next to "b".
    auto hits = index.search({5.0f, 5.0f}, 2);
    EXPECT_EQ(hits[0].first, "b");
    EXPECT_EQ(hits[1].first, "a");
    EXPECT_GT(hits[1].second, 0.9f);

    index.upsert("c", {0.0f, 0.0f});  // upsert of a new id behaves like add
    EXPECT_EQ(index.size(), 3u);
    EXPECT_TRUE(index.contains("c"));
}

// ---------------------------------------------------------------------------
// remove / clear
// ---------------------------------------------------------------------------

TEST_F(VectorIndexTest, RemoveThenSearchThenReAdd) {
    VectorIndex index(1, Metric::L2);
    index.add("a", {0.0f});
    index.add("b", {1.0f});
    index.add("c", {2.0f});

    EXPECT_TRUE(index.remove("b"));
    EXPECT_FALSE(index.remove("b"));  // idempotent: already gone
    EXPECT_EQ(index.size(), 2u);
    EXPECT_FALSE(index.contains("b"));

    auto hits = index.search({2.0f}, 3);
    EXPECT_EQ(idsOf(hits), (std::vector<std::string>{"c", "a"}));
    // Surviving rows still map to their own vectors after the shift.
    EXPECT_FLOAT_EQ(index.get("c")[0], 2.0f);
    EXPECT_FLOAT_EQ(index.get("a")[0], 0.0f);
    EXPECT_THROW(index.get("b"), std::out_of_range);

    index.add("b", {1.9f});
    EXPECT_EQ(index.size(), 3u);
    hits = index.search({2.0f}, 3);
    EXPECT_EQ(idsOf(hits), (std::vector<std::string>{"c", "b", "a"}));
}

TEST_F(VectorIndexTest, RemoveFirstRowKeepsRemainingVectorsAligned) {
    VectorIndex index(2, Metric::L2);
    index.add("a", {1.0f, 1.0f});
    index.add("b", {2.0f, 2.0f});
    index.add("c", {3.0f, 3.0f});

    ASSERT_TRUE(index.remove("a"));
    EXPECT_EQ(index.get("b"), (std::vector<float>{2.0f, 2.0f}));
    EXPECT_EQ(index.get("c"), (std::vector<float>{3.0f, 3.0f}));
    EXPECT_EQ(idsOf(index.search({3.0f, 3.0f}, 2)), (std::vector<std::string>{"c", "b"}));
}

TEST_F(VectorIndexTest, ClearEmptiesVectorsButKeepsConfiguration) {
    VectorIndexOptions opts;
    opts.metric = Metric::InnerProduct;
    opts.normalizeOnAdd = true;
    opts.dimension = 2;
    opts.embeddingModel = "nomic-embed-text-v1-GGUF";
    VectorIndex index(opts);
    index.add("a", {1.0f, 0.0f});

    index.clear();
    EXPECT_EQ(index.size(), 0u);
    EXPECT_FALSE(index.contains("a"));
    EXPECT_EQ(index.dimension(), 2u);
    EXPECT_EQ(index.metric(), Metric::InnerProduct);
    EXPECT_TRUE(index.normalizeOnAdd());
    EXPECT_EQ(index.embeddingModel(), "nomic-embed-text-v1-GGUF");
}

// ---------------------------------------------------------------------------
// Persistence
// ---------------------------------------------------------------------------

TEST_F(VectorIndexTest, SaveLoadRoundTripPreservesResultsExactly) {
    VectorIndexOptions opts;
    opts.metric = Metric::L2;
    opts.embeddingModel = "nomic-embed-text-v1-GGUF";
    VectorIndex index(opts);
    for (size_t i = 0; i < kParityVectors.size(); ++i) {
        index.add("v" + std::to_string(i), kParityVectors[i]);
    }
    const auto before = index.search(kParityQuery, 5);

    const auto file = path("index.vec");
    index.save(file.string());
    ASSERT_TRUE(fs::exists(file));
    EXPECT_FALSE(fs::exists(file.string() + ".tmp"));

    VectorIndexOptions loadOpts;
    loadOpts.embeddingModel = "nomic-embed-text-v1-GGUF";
    VectorIndex loaded(loadOpts);
    loaded.load(file.string());

    EXPECT_EQ(loaded.size(), index.size());
    EXPECT_EQ(loaded.dimension(), 4u);
    EXPECT_EQ(loaded.metric(), Metric::L2);
    EXPECT_EQ(loaded.ids(), index.ids());

    const auto after = loaded.search(kParityQuery, 5);
    ASSERT_EQ(after.size(), before.size());
    for (size_t i = 0; i < before.size(); ++i) {
        EXPECT_EQ(after[i].first, before[i].first);
        // Bit-exact: the payload is float32 in and float32 out.
        EXPECT_EQ(after[i].second, before[i].second);
    }
    for (size_t i = 0; i < kParityVectors.size(); ++i) {
        EXPECT_EQ(loaded.get("v" + std::to_string(i)), kParityVectors[i]);
    }
}

TEST_F(VectorIndexTest, SaveLoadPreservesMetricAndNormalization) {
    VectorIndexOptions opts;
    opts.metric = Metric::InnerProduct;
    opts.normalizeOnAdd = true;
    VectorIndex index(opts);
    index.add("a", {3.0f, 4.0f});

    const auto file = path("ip.vec");
    index.save(file.string());

    VectorIndex loaded;  // defaults to L2, no normalization
    loaded.load(file.string());
    EXPECT_EQ(loaded.metric(), Metric::InnerProduct);
    EXPECT_TRUE(loaded.normalizeOnAdd());
    EXPECT_EQ(loaded.dimension(), 2u);
    // Stored normalized: (3,4)/5 = (0.6, 0.8)
    EXPECT_FLOAT_EQ(loaded.get("a")[0], 0.6f);
    EXPECT_FLOAT_EQ(loaded.get("a")[1], 0.8f);
}

TEST_F(VectorIndexTest, SaveOverwritesAnExistingIndexFile) {
    const auto file = path("nested") / "index.vec";
    VectorIndex first(1, Metric::L2);
    first.add("a", {1.0f});
    first.save(file.string());  // also exercises parent-directory creation

    VectorIndex second(1, Metric::L2);
    second.add("b", {2.0f});
    second.add("c", {3.0f});
    second.save(file.string());

    VectorIndex loaded;
    loaded.load(file.string());
    EXPECT_EQ(loaded.ids(), (std::vector<std::string>{"b", "c"}));
}

TEST_F(VectorIndexTest, EmptyIndexRoundTrips) {
    VectorIndex index(4, Metric::L2);
    const auto file = path("empty.vec");
    index.save(file.string());

    VectorIndex loaded;
    loaded.load(file.string());
    EXPECT_EQ(loaded.size(), 0u);
    EXPECT_EQ(loaded.dimension(), 4u);
    EXPECT_TRUE(loaded.search({1.0f, 2.0f, 3.0f, 4.0f}, 3).empty());
}

// Locks the documented on-disk layout: a change here is a format change and
// must come with a version bump.
TEST_F(VectorIndexTest, FileLayoutMatchesDocumentedFormat) {
    VectorIndexOptions opts;
    opts.metric = Metric::InnerProduct;
    opts.normalizeOnAdd = true;
    opts.embeddingModel = "test-embed";
    VectorIndex index(opts);
    index.add("id-1", {1.0f, 0.0f, 0.0f});

    const auto file = path("layout.vec");
    index.save(file.string());
    const std::string raw = readAll(file);

    ASSERT_GE(raw.size(), 36u);
    EXPECT_EQ(raw.substr(0, 7), "GAIAVEC");
    EXPECT_EQ(raw[7], '\0');
    EXPECT_EQ(leU32(raw, 8), 1u);                  // version
    EXPECT_EQ(leU32(raw, 12), 1u);                 // metric: InnerProduct
    EXPECT_EQ(leU32(raw, 16), 1u);                 // normalizeOnAdd
    EXPECT_EQ(leU32(raw, 20), 3u);                 // dimension
    EXPECT_EQ(leU64(raw, 24), 1u);                 // count
    EXPECT_EQ(leU32(raw, 32), 10u);                // model length
    EXPECT_EQ(raw.substr(36, 10), "test-embed");
    EXPECT_EQ(leU32(raw, 46), 4u);                 // id length
    EXPECT_EQ(raw.substr(50, 4), "id-1");
    // Record payload is dimension * 4 bytes of float32.
    EXPECT_EQ(raw.size(), 54u + 3u * sizeof(float));
    // First component is 1.0f little-endian (0x3F800000).
    EXPECT_EQ(leU32(raw, 54), 0x3F800000u);
}

TEST_F(VectorIndexTest, LoadRejectsMissingFile) {
    VectorIndex index;
    EXPECT_THROW(index.load(path("nope.vec").string()), std::runtime_error);
}

TEST_F(VectorIndexTest, LoadRejectsForeignAndTruncatedFiles) {
    const auto foreign = path("index.faiss");
    { std::ofstream(foreign, std::ios::binary) << "IxFI not a gaia vector file at all"; }
    VectorIndex a;
    EXPECT_THROW(a.load(foreign.string()), std::runtime_error);

    // A valid file cut short mid-payload.
    VectorIndex src(2, Metric::L2);
    src.add("a", {1.0f, 2.0f});
    const auto good = path("good.vec");
    src.save(good.string());
    std::string raw = readAll(good);
    const auto cut = path("cut.vec");
    { std::ofstream(cut, std::ios::binary) << raw.substr(0, raw.size() - 3); }
    VectorIndex b;
    EXPECT_THROW(b.load(cut.string()), std::runtime_error);

    // Trailing junk is corruption too — it means the writer and reader disagree.
    const auto extra = path("extra.vec");
    { std::ofstream(extra, std::ios::binary) << raw << "junk"; }
    VectorIndex c;
    EXPECT_THROW(c.load(extra.string()), std::runtime_error);
}

TEST_F(VectorIndexTest, LoadRejectsUnknownFormatVersion) {
    VectorIndex src(2, Metric::L2);
    src.add("a", {1.0f, 2.0f});
    const auto file = path("v99.vec");
    src.save(file.string());

    std::string raw = readAll(file);
    raw[8] = static_cast<char>(99);  // bump the version field
    { std::ofstream(file, std::ios::binary) << raw; }

    VectorIndex index;
    try {
        index.load(file.string());
        FAIL() << "expected a version-mismatch throw";
    } catch (const std::runtime_error& e) {
        const std::string msg = e.what();
        EXPECT_NE(msg.find("version 99"), std::string::npos) << msg;
    }
}

TEST_F(VectorIndexTest, LoadRejectsEmbeddingModelMismatchAndNamesBoth) {
    VectorIndexOptions opts;
    opts.embeddingModel = "nomic-embed-text-v1-GGUF";
    VectorIndex src(opts);
    src.add("a", {1.0f, 2.0f});
    const auto file = path("model.vec");
    src.save(file.string());

    VectorIndexOptions other;
    other.embeddingModel = "all-MiniLM-L6-v2";
    VectorIndex index(other);
    index.add("keep", {9.0f, 9.0f});
    try {
        index.load(file.string());
        FAIL() << "expected an embedding-model mismatch throw";
    } catch (const std::runtime_error& e) {
        const std::string msg = e.what();
        EXPECT_NE(msg.find("nomic-embed-text-v1-GGUF"), std::string::npos) << msg;
        EXPECT_NE(msg.find("all-MiniLM-L6-v2"), std::string::npos) << msg;
    }
    // A refused load leaves the existing index intact.
    EXPECT_EQ(index.size(), 1u);
    EXPECT_TRUE(index.contains("keep"));
    EXPECT_FALSE(index.contains("a"));
}

TEST_F(VectorIndexTest, LoadRejectsAnUntaggedFileWhenAModelIsConfigured) {
    VectorIndex src(2, Metric::L2);  // no embedding-model tag
    src.add("a", {1.0f, 2.0f});
    const auto file = path("untagged.vec");
    src.save(file.string());

    VectorIndexOptions opts;
    opts.embeddingModel = "nomic-embed-text-v1-GGUF";
    VectorIndex index(opts);
    try {
        index.load(file.string());
        FAIL() << "expected an embedding-model mismatch throw";
    } catch (const std::runtime_error& e) {
        const std::string msg = e.what();
        EXPECT_NE(msg.find("no embedding-model tag"), std::string::npos) << msg;
        EXPECT_NE(msg.find("nomic-embed-text-v1-GGUF"), std::string::npos) << msg;
    }
}

TEST_F(VectorIndexTest, LoadAdoptsTheFileModelWhenNoneIsConfigured) {
    VectorIndexOptions opts;
    opts.embeddingModel = "nomic-embed-text-v1-GGUF";
    VectorIndex src(opts);
    src.add("a", {1.0f, 2.0f});
    const auto file = path("adopt.vec");
    src.save(file.string());

    VectorIndex index;
    index.load(file.string());
    EXPECT_EQ(index.embeddingModel(), "nomic-embed-text-v1-GGUF");
}

TEST_F(VectorIndexTest, LoadRejectsDimensionMismatch) {
    VectorIndex src(2, Metric::L2);
    src.add("a", {1.0f, 2.0f});
    const auto file = path("dim.vec");
    src.save(file.string());

    VectorIndex index(768, Metric::L2);
    try {
        index.load(file.string());
        FAIL() << "expected a dimension-mismatch throw";
    } catch (const std::invalid_argument& e) {
        const std::string msg = e.what();
        EXPECT_NE(msg.find("768"), std::string::npos) << msg;
        EXPECT_NE(msg.find("2-dimensional"), std::string::npos) << msg;
    }
}

// A corrupt header must not be trusted to size an allocation: 10^9 vectors of
// 768 floats is a 3 TB reserve, and 2^60 escapes as a bare std::length_error
// that names nothing.
TEST_F(VectorIndexTest, LoadRejectsAVectorCountTheFileCannotHold) {
    for (uint64_t bogus : {uint64_t{1000000000}, uint64_t{1} << 60}) {
        const auto file = path("huge.vec");
        writeFile(file, makeHeader(768, bogus));
        VectorIndex index;
        try {
            index.load(file.string());
            FAIL() << "expected a corrupt-header throw for count=" << bogus;
        } catch (const std::runtime_error& e) {
            EXPECT_NE(std::string(e.what()).find(std::to_string(bogus)), std::string::npos)
                << e.what();
        }
        EXPECT_EQ(index.size(), 0u);
    }
}

// NaN in a payload would poison every ranking and break the search comparator's
// ordering, so the load path applies the same finite check add() does.
TEST_F(VectorIndexTest, LoadRejectsNonFiniteVectorPayload) {
    std::string bytes = makeHeader(2, 1);
    putU32(bytes, 1);
    bytes.append("a");
    putFloatBits(bytes, 0x7FC00000u);  // quiet NaN
    putFloatBits(bytes, 0x3F800000u);  // 1.0f
    const auto file = path("nan.vec");
    writeFile(file, bytes);

    VectorIndex index;
    try {
        index.load(file.string());
        FAIL() << "expected a non-finite payload throw";
    } catch (const std::runtime_error& e) {
        EXPECT_NE(std::string(e.what()).find("non-finite"), std::string::npos) << e.what();
    }
    EXPECT_EQ(index.size(), 0u);
}

TEST_F(VectorIndexTest, LoadRejectsMalformedHeadersAndRecords) {
    VectorIndex index;

    // Unknown metric code.
    const auto badMetric = path("metric.vec");
    writeFile(badMetric, makeHeader(1, 0, "", 1, /*metric=*/7));
    EXPECT_THROW(index.load(badMetric.string()), std::runtime_error);

    // dimension 0 while claiming to hold vectors.
    const auto zeroDim = path("zerodim.vec");
    writeFile(zeroDim, makeHeader(0, 2));
    EXPECT_THROW(index.load(zeroDim.string()), std::runtime_error);

    // Record with an empty id.
    std::string emptyId = makeHeader(1, 1);
    putU32(emptyId, 0);
    putFloatBits(emptyId, 0x3F800000u);
    const auto emptyIdFile = path("emptyid.vec");
    writeFile(emptyIdFile, emptyId);
    EXPECT_THROW(index.load(emptyIdFile.string()), std::runtime_error);

    // Two records sharing an id — ids must address exactly one vector.
    std::string dup = makeHeader(1, 2);
    for (int i = 0; i < 2; ++i) {
        putU32(dup, 1);
        dup.append("a");
        putFloatBits(dup, 0x3F800000u);
    }
    const auto dupFile = path("dup.vec");
    writeFile(dupFile, dup);
    try {
        index.load(dupFile.string());
        FAIL() << "expected a duplicate-id throw";
    } catch (const std::runtime_error& e) {
        EXPECT_NE(std::string(e.what()).find("duplicate id"), std::string::npos) << e.what();
    }

    // The model-length field pointing past the end of the file.
    const auto badModel = path("model-len.vec");
    writeFile(badModel, makeHeader(1, 0, "abc").substr(0, 37));
    EXPECT_THROW(index.load(badModel.string()), std::runtime_error);
}

TEST_F(VectorIndexTest, FailedLoadLeavesTheExistingIndexUsable) {
    VectorIndex index(2, Metric::L2);
    index.add("keep", {1.0f, 1.0f});
    const auto before = index.search({1.0f, 1.0f}, 1);

    VectorIndex src(2, Metric::L2);
    src.add("other", {5.0f, 5.0f});
    const auto good = path("src.vec");
    src.save(good.string());
    const std::string raw = readAll(good);

    const auto cut = path("cut.vec");
    writeFile(cut, raw.substr(0, raw.size() - 2));
    EXPECT_THROW(index.load(cut.string()), std::runtime_error);

    std::string badVersion = raw;
    badVersion[8] = static_cast<char>(42);
    const auto ver = path("ver.vec");
    writeFile(ver, badVersion);
    EXPECT_THROW(index.load(ver.string()), std::runtime_error);

    EXPECT_EQ(index.size(), 1u);
    EXPECT_TRUE(index.contains("keep"));
    EXPECT_FALSE(index.contains("other"));
    EXPECT_EQ(index.search({1.0f, 1.0f}, 1), before);
}

TEST_F(VectorIndexTest, SaveFailsLoudlyWhenTheTargetPathIsUnusable) {
    const auto blocker = path("blocker");
    writeFile(blocker, "not a directory");

    VectorIndex index(1, Metric::L2);
    index.add("a", {1.0f});
    // A regular file sits where the parent directory would have to go.
    EXPECT_THROW(index.save((blocker / "index.vec").string()), std::runtime_error);
}

TEST_F(VectorIndexTest, SaveLoadAfterRemoveKeepsIdsAndVectorsAligned) {
    VectorIndex index(2, Metric::L2);
    index.add("a", {1.0f, 1.0f});
    index.add("b", {2.0f, 2.0f});
    index.add("c", {3.0f, 3.0f});
    ASSERT_TRUE(index.remove("b"));

    const auto file = path("removed.vec");
    index.save(file.string());

    VectorIndex loaded;
    loaded.load(file.string());
    EXPECT_EQ(loaded.ids(), (std::vector<std::string>{"a", "c"}));
    EXPECT_EQ(loaded.get("a"), (std::vector<float>{1.0f, 1.0f}));
    EXPECT_EQ(loaded.get("c"), (std::vector<float>{3.0f, 3.0f}));
    EXPECT_EQ(idsOf(loaded.search({3.0f, 3.0f}, 2)), (std::vector<std::string>{"c", "a"}));
}

// ---------------------------------------------------------------------------
// Recall parity with Python
// ---------------------------------------------------------------------------
//
// Expected values below were produced by faiss 1.13.2 over kParityVectors /
// kParityQuery and cross-checked against a plain numpy scan:
//
//   IndexFlatL2 top-5 (raw = squared L2 -> score = 1/(1+d)):
//     id=0  d=0.0015999981   score=0.998402558
//     id=8  d=0.00360000064  score=0.996412913
//     id=3  d=0.0644000024   score=0.939496428
//     id=7  d=0.501600027    score=0.665956301
//     id=6  d=0.701600015    score=0.587682176
//
//   IndexFlatIP top-5 over L2-normalized vectors (cosine):
//     id=0  0.99960959   id=8  0.999357224   id=3  0.923920929
//     id=7  0.718605161  id=6  0.547508717
//
// To regenerate (V = kParityVectors, q = kParityQuery, both float32):
//
//   import faiss, numpy as np
//   il = faiss.IndexFlatL2(4); il.add(V); print(il.search(q, 5))   # -> 1/(1+d)
//   Vn = V / np.linalg.norm(V, axis=1, keepdims=True)
//   qn = q / np.linalg.norm(q, axis=1, keepdims=True)
//   ip = faiss.IndexFlatIP(4); ip.add(Vn); print(ip.search(qn, 5))

TEST_F(VectorIndexTest, MatchesPythonIndexFlatL2TopK) {
    VectorIndex index(4, Metric::L2);
    for (size_t i = 0; i < kParityVectors.size(); ++i) {
        index.add("v" + std::to_string(i), kParityVectors[i]);
    }

    auto hits = index.search(kParityQuery, 5);
    ASSERT_EQ(hits.size(), 5u);
    EXPECT_EQ(idsOf(hits), (std::vector<std::string>{"v0", "v8", "v3", "v7", "v6"}));

    const std::vector<float> expected = {0.998402558f, 0.996412913f, 0.939496428f, 0.665956301f,
                                         0.587682176f};
    for (size_t i = 0; i < expected.size(); ++i) {
        EXPECT_NEAR(hits[i].second, expected[i], 1e-6f) << "rank " << i;
    }
}

TEST_F(VectorIndexTest, MatchesPythonIndexFlatIPTopK) {
    VectorIndexOptions opts;
    opts.metric = Metric::InnerProduct;
    opts.normalizeOnAdd = true;
    opts.dimension = 4;
    VectorIndex index(opts);
    for (size_t i = 0; i < kParityVectors.size(); ++i) {
        index.add("v" + std::to_string(i), kParityVectors[i]);
    }

    auto hits = index.search(kParityQuery, 5);
    ASSERT_EQ(hits.size(), 5u);
    EXPECT_EQ(idsOf(hits), (std::vector<std::string>{"v0", "v8", "v3", "v7", "v6"}));

    const std::vector<float> expected = {0.99960959f, 0.999357224f, 0.923920929f, 0.718605161f,
                                         0.547508717f};
    for (size_t i = 0; i < expected.size(); ++i) {
        EXPECT_NEAR(hits[i].second, expected[i], 1e-6f) << "rank " << i;
    }
}
