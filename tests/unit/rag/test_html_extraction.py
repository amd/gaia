# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A saved web page is indexed as its readable text, not its markup."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from gaia.rag import html_utils
from gaia.rag.html_utils import extract_html_text
from gaia.rag.sdk import RAGSDK, RAGConfig

# A trimmed Wikipedia article: the shapes that buried its facts in raw markup.
_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>Coffee - Wikipedia</title>
<script>RLCONF={"wgPageName":"Coffee","wgRelevantArticleId":5964};</script>
<style>.mw-parser-output .infobox{border:1px solid #a2a9b1}</style>
<script type="application/ld+json">{"@context":"https://schema.org","name":"Coffee"}</script>
</head><body>
<a class="mw-jump-link" href="#bodyContent">Jump to content</a>
<nav role="navigation"><ul><li>Main page</li><li>Random article</li></ul></nav>
<div id="p-lang-btn"><ul>
<li><a href="https://af.wikipedia.org/wiki/Koffie" hreflang="af" lang="af">Afrikaans</a></li>
<li><a href="https://de.wikipedia.org/wiki/Kaffee" hreflang="de" lang="de">Deutsch</a></li>
</ul></div>
<main><h1>Coffee</h1>
<div class="mw-parser-output">
<style data-mw-deduplicate="TemplateStyles:r1">.infobox{width:22em}</style>
<table class="infobox" data-mw='{"parts":[{"template":{"params":{"founded":{"wt":"{{since|969}}"}}}}]}'>
<tbody><tr><th>Type</th><td>Usually hot</td></tr>
<tr><th>Origin</th><td>Yemen<sup>[1]</sup></td></tr></tbody></table>
<p>Coffee is a beverage brewed from <b>roasted</b> coffee beans.</p>
<div style="display:none">Hidden maintenance note</div>
<span aria-hidden="true">decorative</span>
<div class="mw-heading"><h2 id="Production">Production</h2></div>
<p>In 2023, Brazil led production with 31% of the total.</p>
<table class="wikitable"><caption>Green coffee production 2023</caption>
<tr><th>Country</th><th>Million tonnes</th></tr>
<tr><td>Brazil</td><td>3.41</td></tr>
<tr><td>Vietnam</td><td>1.96</td></tr></table>
<ul><li>Arabica</li><li>Robusta</li></ul>
</div></main>
<footer>This page was last edited on 1 November 2025.</footer>
<script>(RLQ=window.RLQ||[]).push(function(){mw.config.set({"wgBackendResponseTime":120});});</script>
</body></html>
"""

# The Gemma 4 real-document test's saved pages; skipped where they are absent.
_CORPUS = Path(os.environ.get("GAIA_REAL_CORPUS", "C:/gtest/mdqa/real/docs"))


@pytest.fixture
def rag(tmp_path: Path) -> RAGSDK:
    config = RAGConfig(
        cache_dir=str(tmp_path / ".gaia"),
        show_stats=False,
        use_local_llm=False,
        allowed_paths=[str(tmp_path)],
    )
    fake_vlm = MagicMock(name="VLMClient")
    fake_vlm.check_availability.return_value = False
    with (
        patch.object(RAGSDK, "_check_dependencies", return_value=None),
        patch("gaia.rag.sdk.AgentSDK", autospec=True),
        patch("gaia.llm.VLMClient", return_value=fake_vlm),
    ):
        yield RAGSDK(config=config)


def test_page_keeps_title_headings_paragraphs_and_table_rows(tmp_path):
    text = extract_html_text("Coffee.html", _PAGE.encode("utf-8"))

    assert text.startswith("# Coffee - Wikipedia")
    assert "\n# Coffee\n" in text
    assert "\n## Production\n" in text
    assert "Coffee is a beverage brewed from roasted coffee beans." in text
    assert "Type | Usually hot" in text
    assert "Origin | Yemen[1]" in text
    assert "Green coffee production 2023" in text
    assert "Country | Million tonnes\nBrazil | 3.41\nVietnam | 1.96" in text
    assert "- Arabica\n- Robusta" in text


@pytest.mark.parametrize(
    "noise",
    [
        "RLCONF",
        "schema.org",
        "border:1px",
        "width:22em",
        "data-mw",
        "{{since|969}}",
        "wgBackendResponseTime",
        "Random article",
        "Afrikaans",
        "Deutsch",
        "Hidden maintenance note",
        "decorative",
        "last edited",
        "<",
    ],
)
def test_page_drops_scripts_styles_navigation_and_embedded_json(noise):
    assert noise not in extract_html_text("Coffee.html", _PAGE.encode("utf-8"))


def test_html_is_dispatched_to_the_text_reader(rag, tmp_path):
    page = tmp_path / "Coffee.html"
    page.write_bytes(_PAGE.encode("utf-8"))

    text, metadata = rag._extract_text_from_file(str(page))

    assert "Brazil | 3.41" in text
    assert "RLCONF" not in text
    assert metadata["html_text"] is True


def test_legacy_encoded_page_is_decoded_from_its_meta_charset(tmp_path):
    page = '<html><head><meta charset="windows-1252"></head><body><p>Caf\xe9 cr\xe8me</p></body></html>'

    assert "Café crème" in extract_html_text("x.html", page.encode("cp1252"))


def test_deeply_nested_page_does_not_hit_the_recursion_limit():
    page = "<div>" * 5000 + "<p>deep fact</p>" + "</div>" * 5000

    assert "deep fact" in extract_html_text("deep.html", page.encode())


def test_page_over_the_tag_cap_is_refused_before_parsing(monkeypatch):
    monkeypatch.setattr(html_utils, "MAX_HTML_TAGS", 10)

    with pytest.raises(ValueError, match=r"too large to index: big\.html"):
        extract_html_text("big.html", b"<p>x</p>" * 20)


def _indexing_sdk(tmp_path):
    pytest.importorskip("faiss", reason="Real vector-index tests require faiss-cpu")
    config = RAGConfig(
        cache_dir=str(tmp_path / "cache"),
        allowed_paths=[str(tmp_path)],
        use_llm_chunking=False,
        show_stats=False,
    )
    with patch("gaia.rag.sdk.AgentSDK"):
        sdk = RAGSDK(config)
    sdk._load_embedder = lambda: None
    sdk._encode_texts = lambda texts, **_: np.ones((len(texts), 4), dtype="float32")
    sdk._get_hmac_key = lambda: b"test-cache-key" * 3
    return sdk


def test_page_cached_as_raw_markup_is_read_again(tmp_path):
    page = tmp_path / "Coffee.html"
    page.write_bytes(_PAGE.encode("utf-8"))
    with patch("gaia.rag.sdk.HTML_EXTENSIONS", frozenset()):
        assert _indexing_sdk(tmp_path).index_document(str(page))["success"]

    sdk = _indexing_sdk(tmp_path)
    result = sdk.index_document(str(page))

    assert result["success"], result
    assert not result.get("from_cache")
    served = " ".join(sdk.chunks)
    assert "Brazil | 3.41" in served
    assert "RLCONF" not in served


@pytest.mark.skipif(
    not (_CORPUS / "web" / "Coffee.html").exists(),
    reason="real-document corpus not on this machine",
)
@pytest.mark.parametrize(
    "name, facts",
    [
        ("web/Coffee.html", ["Brazil | 3.41", "Vietnam | 1.96", "31% of the total"]),
        (
            "web/2022_FIFA_World_Cup.html",
            ["Top scorer | Kylian Mbappé (8 goals)", "3,404,252 (53,191 per match)"],
        ),
        ("web/Jupiter.html", ["At least 115 moons orbit the planet"]),
        ("arabic/wiki_ar_cairo.html", ["تاريخ التأسيس | 969"]),
    ],
)
def test_real_wikipedia_pages_index_as_text(name, facts):
    markup = (_CORPUS / name).read_bytes()

    text = extract_html_text(name, markup)

    assert len(text) < len(markup) / 5
    for fact in facts:
        assert fact in text
    for noise in ("data-mw", "<div", "RLCONF", '"wt":', "Afrikaans"):
        assert noise not in text
