# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""EPUB indexing, and refusing binary files instead of indexing them as text.

EPUB fixtures are built with zipfile so the tests stay hermetic.
"""

from __future__ import annotations

import zipfile
import zlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.tools.document_text import INDEX_ONLY_DOCUMENTS
from gaia.rag.epub_utils import extract_epub_text
from gaia.rag.sdk import RAGSDK, RAGConfig
from gaia.utils.document_formats import UNSUPPORTED_DOCUMENTS

_CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""

_OPF = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="c2" href="Text/ch2.xhtml" media-type="application/xhtml+xml"/>
    <item id="c1" href="Text/ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="css" href="style.css" media-type="text/css"/>
  </manifest>
  <spine>
    <itemref idref="c1"/>
    <itemref idref="c2"/>
  </spine>
</package>"""


def _chapter(title: str, body: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>x</title>'
        "<style>p { color: red }</style></head>"
        f"<body><h1>{title}</h1>{body}</body></html>"
    )


def _make_epub(path: Path, *, encrypted: bool = False) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", _CONTAINER)
        zf.writestr("OEBPS/content.opf", _OPF)
        zf.writestr("OEBPS/style.css", "p { margin: 0 }")
        zf.writestr(
            "OEBPS/Text/ch1.xhtml",
            _chapter(
                "Getting Started",
                "<p>The NPU runs <em>quantized</em> models.</p><p>Second paragraph.</p>",
            ),
        )
        zf.writestr(
            "OEBPS/Text/ch2.xhtml",
            _chapter("Deployment", "<ul><li>Install</li><li>Run</li></ul>"),
        )
        if encrypted:
            zf.writestr(
                "META-INF/encryption.xml",
                '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" '
                'xmlns:enc="http://www.w3.org/2001/04/xmlenc#"><enc:EncryptedData>'
                '<enc:CipherData><enc:CipherReference URI="OEBPS/Text/ch1.xhtml"/>'
                "</enc:CipherData></enc:EncryptedData></encryption>",
            )
    return path


@pytest.fixture
def rag(tmp_path: Path) -> RAGSDK:
    """A RAGSDK with heavy ML deps stubbed, as in test_docx_extraction.py."""
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
        patch("gaia.rag.sdk.AgentSDK", autospec=True) as mock_agent_sdk,
        patch("gaia.llm.VLMClient", return_value=fake_vlm),
    ):
        mock_agent_sdk.return_value = MagicMock(name="AgentSDK")
        yield RAGSDK(config=config)


def test_epub_text_follows_the_spine_with_chapter_headings(tmp_path):
    text, chapters = extract_epub_text(str(_make_epub(tmp_path / "book.epub")))

    assert chapters == 2
    assert text.index("# Getting Started") < text.index("# Deployment")
    assert "The NPU runs quantized models." in text
    assert "Second paragraph." in text
    assert "Install\nRun" in text
    assert "color: red" not in text


def test_epub_is_dispatched_and_reports_chapters(rag, tmp_path):
    text, metadata = rag._extract_text_from_file(
        str(_make_epub(tmp_path / "book.epub"))
    )

    assert "# Deployment" in text
    assert metadata["num_chapters"] == 2


def test_drm_encrypted_epub_is_refused(tmp_path):
    path = _make_epub(tmp_path / "drm.epub", encrypted=True)
    with pytest.raises(ValueError, match="DRM-encrypted"):
        extract_epub_text(str(path))


def test_chapter_failing_its_crc_is_a_corrupt_file_error(tmp_path):
    path = _make_epub(tmp_path / "damaged.epub")
    raw = path.read_bytes()
    assert raw.count(b"Second paragraph") == 1
    path.write_bytes(raw.replace(b"Second paragraph", b"Xecond paragraph"))
    with pytest.raises(ValueError, match=r"damaged\.epub[\s\S]*ch1\.xhtml") as err:
        extract_epub_text(str(path))
    assert isinstance(err.value.__cause__, zipfile.BadZipFile)


@pytest.mark.parametrize(
    "failure",
    [NotImplementedError("compression type 99"), zlib.error("invalid stored block")],
)
def test_unreadable_zip_entry_is_a_corrupt_file_error(tmp_path, failure):
    path = _make_epub(tmp_path / "odd.epub")
    with (
        patch.object(zipfile.ZipFile, "read", side_effect=failure),
        pytest.raises(ValueError, match=r"odd\.epub[\s\S]*not a valid \.epub"),
    ):
        extract_epub_text(str(path))


def test_corrupt_epub_names_the_file(tmp_path):
    path = tmp_path / "broken.epub"
    path.write_bytes(b"not a zip at all")
    with pytest.raises(ValueError, match="broken.epub"):
        extract_epub_text(str(path))


def test_unknown_binary_file_is_refused_not_indexed_as_garbage(rag, tmp_path):
    path = tmp_path / "book.mobi"
    path.write_bytes(b"BOOKMOBI\x00\x00\x01\x02" + bytes(range(256)))
    with pytest.raises(ValueError, match=r"binary file.*no reader for \.mobi"):
        rag._extract_text_from_file(str(path))


def test_unknown_text_file_is_still_read(rag, tmp_path):
    path = tmp_path / "notes.adoc"
    path.write_text("= Title\n\nPlain text with an unknown extension.\n")
    text, _ = rag._extract_text_from_file(str(path))
    assert "unknown extension" in text


@pytest.mark.parametrize("ext", sorted(UNSUPPORTED_DOCUMENTS))
def test_unsupported_formats_say_what_to_convert_to(rag, tmp_path, ext):
    path = tmp_path / f"old{ext}"
    path.write_bytes(b"PK\x03\x04\x00\x00")
    with pytest.raises(ValueError, match="Re-save it as"):
        rag._extract_text_from_file(str(path))


@pytest.mark.parametrize("ext", sorted(INDEX_ONLY_DOCUMENTS))
def test_index_only_formats_reach_a_real_parser(rag, tmp_path, ext):
    """read_file sends these to index_document, so RAG must parse them."""
    assert ext not in UNSUPPORTED_DOCUMENTS
    path = tmp_path / f"broken{ext}"
    path.write_bytes(b"PK\x03\x04\x00\x00garbage")
    with pytest.raises(ValueError) as err:
        rag._extract_text_from_file(str(path))
    assert "no reader for" not in str(err.value)
    assert f"not a valid {ext} file" in str(err.value)


def test_refused_binary_does_not_log_that_it_is_read_as_text(rag, tmp_path):
    path = tmp_path / "book.mobi"
    path.write_bytes(b"BOOKMOBI\x00\x00")
    with (
        patch.object(rag.log, "warning") as warning,
        pytest.raises(ValueError, match="binary file"),
    ):
        rag._extract_text_from_file(str(path))
    warning.assert_not_called()


@pytest.mark.parametrize("bom", [b"\xff\xfe", b"\xfe\xff"])
def test_utf16_text_is_called_utf16_not_binary(rag, tmp_path, bom):
    path = tmp_path / "notes.adoc"
    codec = "utf-16-le" if bom == b"\xff\xfe" else "utf-16-be"
    path.write_bytes(bom + "Plain notes".encode(codec))
    with pytest.raises(ValueError, match="UTF-16 text.*Re-save it as UTF-8"):
        rag._extract_text_from_file(str(path))


def test_utf8_bom_and_cjk_text_with_unknown_extension_is_read(rag, tmp_path):
    path = tmp_path / "notes.adoc"
    path.write_bytes("﻿设备 NPU 推理 — ünïcödé".encode("utf-8"))
    text, _ = rag._extract_text_from_file(str(path))
    assert "设备 NPU 推理" in text


def test_nul_in_the_last_sniffed_byte_is_refused(rag, tmp_path):
    path = tmp_path / "log.adoc"
    path.write_bytes(b"a" * 8191 + b"\x00")
    with pytest.raises(ValueError, match="binary file"):
        rag._extract_text_from_file(str(path))


def test_epub_over_the_uncompressed_cap_is_refused_as_a_zip_bomb(tmp_path):
    path = _make_epub(tmp_path / "bomb.epub")
    with (
        patch("gaia.rag.epub_utils.MAX_UNCOMPRESSED_BYTES", 100),
        pytest.raises(ValueError, match=r"bomb\.epub[\s\S]*zip bomb"),
    ):
        extract_epub_text(str(path))


def test_prefixed_opf_and_encoded_fragment_hrefs_resolve(tmp_path):
    path = tmp_path / "real.epub"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", _CONTAINER)
        zf.writestr(
            "OEBPS/content.opf",
            '<opf:package xmlns:opf="http://www.idpf.org/2007/opf"><opf:manifest>'
            '<opf:item id="a" href="Text/chapter%20one.xhtml#top" '
            'media-type="application/xhtml+xml"/>'
            '<opf:item id="n" href="nav.xhtml" media-type="application/xhtml+xml" '
            'properties="nav"/></opf:manifest>'
            '<opf:spine><opf:itemref idref="a"/></opf:spine></opf:package>',
        )
        zf.writestr("OEBPS/Text/chapter one.xhtml", _chapter("One", "<p>Body</p>"))
        zf.writestr("OEBPS/nav.xhtml", _chapter("Contents", "<p>TOC</p>"))
    text, chapters = extract_epub_text(str(path))
    assert chapters == 1
    assert "# One" in text and "TOC" not in text


def test_spine_href_escaping_the_archive_is_a_missing_chapter(tmp_path):
    path = tmp_path / "evil.epub"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("META-INF/container.xml", _CONTAINER)
        zf.writestr(
            "OEBPS/content.opf",
            '<package><manifest><item id="x" href="../../../etc/passwd" '
            'media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="x"/></spine></package>',
        )
    with pytest.raises(ValueError, match="listed but missing"):
        extract_epub_text(str(path))
