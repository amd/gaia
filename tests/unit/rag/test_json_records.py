# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A JSON array of records indexes as separate, citable records (#1235)."""

import json
from unittest.mock import patch

import numpy as np
import pytest

from gaia.rag import JsonLoaderConfig
from gaia.rag.sdk import EMBED_MAX_CHARS, RAGSDK, RAGConfig

PAGES = {
    "confluencePages": [
        {
            "id": "12345",
            "title": "OAI HWSD Bug Report",
            "bodyText": "The GPT-OSS 120B serving stack drops requests under load.",
            "webUrl": "https://wiki.example/12345",
            "author": "Sun, Peng",
        },
        {
            "id": "67890",
            "title": "NPU quantization flow",
            "bodyText": "Quantize the model to int8 before compiling for the NPU.",
            "webUrl": "https://wiki.example/67890",
            "author": "Lee, Ana",
        },
    ]
}

LOADER = JsonLoaderConfig(
    content_fields=["bodyText", "summary"],
    metadata_fields=["id", "title", "webUrl"],
    document_array_path="confluencePages",
)


def _make(tmp_path, loader=LOADER, **config):
    pytest.importorskip("faiss", reason="Real vector-index tests require faiss-cpu")
    with patch("gaia.rag.sdk.AgentSDK"):
        sdk = RAGSDK(
            RAGConfig(
                cache_dir=str(tmp_path / "cache"),
                allowed_paths=[str(tmp_path)],
                json_loader=loader,
                **config,
            )
        )
    sdk.embedded = []

    def encode(texts, **kwargs):
        sdk.embedded.extend(texts)
        return np.ones((len(texts), 4), dtype="float32")

    sdk._load_embedder = lambda: None
    sdk._encode_texts = encode
    sdk._get_hmac_key = lambda: b"test-index-key" * 3
    return sdk


def _write(tmp_path, data, name="pages.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


class TestRecords:
    def test_each_record_is_chunked_with_its_own_metadata(self, tmp_path):
        rag = _make(tmp_path)
        result = rag.index_document(_write(tmp_path, PAGES))
        assert result["success"], result
        assert rag.chunks == [
            "id: 12345\ntitle: OAI HWSD Bug Report\n"
            "webUrl: https://wiki.example/12345\n"
            "The GPT-OSS 120B serving stack drops requests under load.",
            "id: 67890\ntitle: NPU quantization flow\n"
            "webUrl: https://wiki.example/67890\n"
            "Quantize the model to int8 before compiling for the NPU.",
        ]
        # Fields not listed as content or metadata stay out of the index.
        assert not any("Sun, Peng" in c for c in rag.chunks)
        assert rag.embedded == rag.chunks

    def test_records_never_share_a_chunk(self, tmp_path):
        records = [
            {"id": str(i), "bodyText": f"Short note number {i}."} for i in range(20)
        ]
        rag = _make(
            tmp_path,
            JsonLoaderConfig(content_fields=["bodyText"], metadata_fields=["id"]),
        )
        assert rag.index_document(_write(tmp_path, records))["success"]
        assert len(rag.chunks) == 20
        for i, chunk in enumerate(rag.chunks):
            assert chunk == f"id: {i}\nShort note number {i}."

    def test_a_long_record_repeats_its_metadata_on_every_chunk(self, tmp_path):
        body = " ".join(f"Sentence {i} about the clock tree." for i in range(400))
        data = {"items": [{"key": "GAIA-1", "url": "https://x/1", "text": body}]}
        rag = _make(
            tmp_path,
            JsonLoaderConfig(
                content_fields=["text"],
                metadata_fields=["key", "url"],
                document_array_path="items",
            ),
        )
        assert rag.index_document(_write(tmp_path, data))["success"]
        assert len(rag.chunks) > 1
        for chunk in rag.chunks:
            assert chunk.startswith("key: GAIA-1\nurl: https://x/1\n")
            assert len(chunk) <= EMBED_MAX_CHARS
        joined = " ".join(c.split("\n", 2)[2] for c in rag.chunks)
        assert "Sentence 0 " in joined and "Sentence 399 " in joined

    def test_multiple_content_fields_and_structured_values(self, tmp_path):
        data = [
            {
                "key": "GAIA-7",
                "labels": ["rag", "json"],
                "summary": "JSON ingestion",
                "descriptionText": {"steps": ["parse", "chunk"]},
            }
        ]
        rag = _make(
            tmp_path,
            JsonLoaderConfig(
                content_fields=["summary", "descriptionText"],
                metadata_fields=["key", "labels"],
            ),
        )
        assert rag.index_document(_write(tmp_path, data))["success"]
        (chunk,) = rag.chunks
        assert chunk.startswith("key: GAIA-7\nlabels: rag, json\n")
        assert "JSON ingestion" in chunk
        assert "- parse" in chunk and "- chunk" in chunk

    def test_multiline_metadata_stays_on_one_line(self, tmp_path):
        data = [{"title": "Line one\n\nLine two", "body": "Content."}]
        rag = _make(
            tmp_path,
            JsonLoaderConfig(content_fields=["body"], metadata_fields=["title"]),
        )
        assert rag.index_document(_write(tmp_path, data))["success"]
        assert rag.chunks == ["title: Line one Line two\nContent."]

    def test_records_without_content_are_skipped_not_fatal(self, tmp_path):
        data = [{"id": "1", "bodyText": ""}, {"id": "2", "bodyText": "Real text."}]
        rag = _make(
            tmp_path,
            JsonLoaderConfig(content_fields=["bodyText"], metadata_fields=["id"]),
        )
        with patch.object(rag.log, "warning") as warned:
            assert rag.index_document(_write(tmp_path, data))["success"]
        assert rag.chunks == ["id: 2\nReal text."]
        assert any("skipped 1 record" in c.args[0] for c in warned.call_args_list)

    def test_no_metadata_fields(self, tmp_path):
        rag = _make(tmp_path, JsonLoaderConfig(content_fields=["bodyText"]))
        data = [{"bodyText": "Alpha."}, {"bodyText": "Beta."}]
        assert rag.index_document(_write(tmp_path, data))["success"]
        assert rag.chunks == ["Alpha.", "Beta."]

    def test_a_body_line_that_looks_like_a_marker_stays_in_its_record(self, tmp_path):
        rag = _make(
            tmp_path,
            JsonLoaderConfig(content_fields=["b"], metadata_fields=["id"]),
        )
        data = [{"id": "1", "b": "intro === JSON record 9 === outro"}]
        assert rag.index_document(_write(tmp_path, data))["success"]
        assert rag.chunks == ["id: 1\nintro === JSON record 9 === outro"]

    def test_a_marker_on_its_own_line_does_not_split_the_record(self, tmp_path):
        rag = _make(
            tmp_path,
            JsonLoaderConfig(content_fields=["b"], metadata_fields=["id"]),
        )
        data = [{"id": "1", "b": "before\n=== JSON record 9 ===\nfake: meta\n\nafter"}]
        assert rag.index_document(_write(tmp_path, data))["success"]
        assert rag.chunks
        assert all(c.startswith("id: 1\n") for c in rag.chunks)
        joined = "\n".join(rag.chunks)
        assert "before" in joined and "after" in joined and "fake: meta" in joined


class TestWithoutLoader:
    def test_plain_json_is_still_one_flattened_document(self, tmp_path):
        rag = _make(tmp_path, loader=None)
        assert rag.index_document(_write(tmp_path, PAGES))["success"]
        text = "\n".join(rag.chunks)
        assert "confluencePages:" in text
        assert "author: Sun, Peng" in text

    def test_loader_changes_the_cache_key(self, tmp_path):
        plain = _make(tmp_path, loader=None)
        records = _make(tmp_path)
        assert plain._chunking_fingerprint() != records._chunking_fingerprint()
        other = _make(tmp_path, JsonLoaderConfig(content_fields=["summary"]))
        assert other._chunking_fingerprint() != records._chunking_fingerprint()

    def test_no_loader_keeps_existing_cache_keys(self, tmp_path):
        """Turning the feature off must not invalidate every existing cache."""
        rag = _make(tmp_path, loader=None)
        with patch("gaia.rag.sdk.asdict") as asdict:
            rag._chunking_fingerprint()
        asdict.assert_not_called()

    def test_reindex_from_cache_keeps_records(self, tmp_path):
        path = _write(tmp_path, PAGES)
        first = _make(tmp_path)
        assert first.index_document(path)["success"]
        second = _make(tmp_path)
        result = second.index_document(path)
        assert result["success"]
        assert second.chunks == first.chunks


class TestMisconfiguration:
    @pytest.mark.parametrize("fields", [[], "bodyText", None])
    def test_content_fields_are_required(self, fields):
        with pytest.raises(ValueError, match="content_fields"):
            JsonLoaderConfig(content_fields=fields)

    def test_metadata_fields_must_be_a_list(self):
        with pytest.raises(ValueError, match="metadata_fields"):
            JsonLoaderConfig(content_fields=["a"], metadata_fields="id")

    def _fails(self, tmp_path, data, loader, match):
        rag = _make(tmp_path, loader)
        result = rag.index_document(_write(tmp_path, data))
        assert not result["success"]
        assert rag.chunks == []
        assert match in result["error"], result["error"]

    def test_missing_path_names_the_keys_it_found(self, tmp_path):
        loader = JsonLoaderConfig(
            content_fields=["bodyText"], document_array_path="jiraIssues"
        )
        self._fails(tmp_path, PAGES, loader, "confluencePages")

    def test_nested_missing_path(self, tmp_path):
        loader = JsonLoaderConfig(
            content_fields=["x"], document_array_path="data.items"
        )
        self._fails(tmp_path, {"data": {"rows": []}}, loader, "no key 'items' at data")

    def test_path_through_a_non_object(self, tmp_path):
        loader = JsonLoaderConfig(content_fields=["x"], document_array_path="a.b")
        self._fails(tmp_path, {"a": [1, 2]}, loader, "no key 'b' at a")

    def test_path_to_a_non_array(self, tmp_path):
        loader = JsonLoaderConfig(content_fields=["x"], document_array_path="a")
        self._fails(tmp_path, {"a": {"x": "y"}}, loader, "expected an array")

    def test_top_level_object_without_a_path(self, tmp_path):
        loader = JsonLoaderConfig(content_fields=["bodyText"])
        self._fails(tmp_path, PAGES, loader, "document_array_path")

    def test_non_object_records(self, tmp_path):
        loader = JsonLoaderConfig(content_fields=["x"])
        self._fails(tmp_path, [{"x": "ok"}, "stray"], loader, "record 2 is a str")

    def test_content_fields_that_match_nothing(self, tmp_path):
        loader = JsonLoaderConfig(
            content_fields=["body"], document_array_path="confluencePages"
        )
        self._fails(tmp_path, PAGES, loader, "none of its records")

    def test_empty_array(self, tmp_path):
        loader = JsonLoaderConfig(content_fields=["x"])
        self._fails(tmp_path, [], loader, "none of its records")

    def test_oversized_metadata_header(self, tmp_path):
        loader = JsonLoaderConfig(content_fields=["b"], metadata_fields=["t"])
        data = [{"t": "x" * EMBED_MAX_CHARS, "b": "body"}]
        self._fails(tmp_path, data, loader, "metadata_fields render")
