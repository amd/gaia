# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Image files are indexed by the text the vision model reads from them (#730).

The VLM is mocked; nothing reaches a Lemonade server. The tests pin the call
the image path makes against the call the PDF path makes, and that an image
the VLM cannot read fails instead of indexing empty or placeholder text.
"""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from gaia.llm.vlm_client import VLMExtractionError
from gaia.rag.sdk import IMAGE_EXTENSIONS, RAGSDK, ImageExtractionError, RAGConfig

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PIL_FORMATS = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".webp": "WEBP",
    ".bmp": "BMP",
    ".gif": "GIF",
    ".tif": "TIFF",
    ".tiff": "TIFF",
}


def _sdk(tmp_path: Path, **overrides) -> RAGSDK:
    config = RAGConfig(
        cache_dir=str(tmp_path / ".gaia"),
        show_stats=False,
        use_local_llm=False,
        allowed_paths=[str(tmp_path)],
        **overrides,
    )
    with (
        patch.object(RAGSDK, "_check_dependencies", return_value=None),
        patch("gaia.rag.sdk.AgentSDK"),
    ):
        return RAGSDK(config=config)


def _image(tmp_path: Path, ext: str = ".png", size=(320, 120)) -> Path:
    path = tmp_path / f"scan{ext}"
    Image.new("RGB", size, "white").save(path, format=PIL_FORMATS[ext])
    return path


def _vlm(available: bool = True, text: str = "INVOICE 4471") -> MagicMock:
    vlm = MagicMock(name="VLMClient")
    vlm.server_url = "http://localhost:13305"
    vlm.check_availability.return_value = available
    vlm.extract_from_page_images.side_effect = lambda images, page_num: [
        {"image_num": 1, "text": text, "dimensions": "1x1", "size_kb": 1.0}
    ]
    return vlm


def test_every_image_extension_routes_to_the_image_extractor(tmp_path):
    rag = _sdk(tmp_path)
    with patch.object(
        rag, "_extract_text_from_image", return_value=("t", {})
    ) as extract:
        for ext in IMAGE_EXTENSIONS:
            rag._extract_text_from_file(str(tmp_path / f"a{ext.upper()}"))
    assert extract.call_count == len(IMAGE_EXTENSIONS)


def test_the_vlm_is_built_and_called_the_way_the_pdf_path_calls_it(tmp_path):
    rag = _sdk(tmp_path, vlm_model="Some-VL-Model", base_url="http://h:1/api/v1")
    vlm = _vlm()
    with patch("gaia.llm.VLMClient", return_value=vlm) as ctor:
        text, metadata = rag._extract_text_from_file(str(_image(tmp_path)))

    ctor.assert_called_once_with(
        vlm_model="Some-VL-Model", base_url="http://h:1/api/v1"
    )
    vlm.extract_from_page_images.assert_called_once()
    (images,), kwargs = vlm.extract_from_page_images.call_args
    assert kwargs == {"page_num": 1}
    assert len(images) == 1
    # The keys extract_images_from_page_pymupdf hands the PDF path.
    assert set(images[0]) == {"image_bytes", "width", "height", "format", "size_kb"}
    assert images[0]["image_bytes"].startswith(PNG_SIGNATURE)
    assert (images[0]["width"], images[0]["height"]) == (320, 120)
    vlm.cleanup.assert_called_once()

    assert text == "INVOICE 4471"
    assert metadata["vlm_pages"] == 1 and metadata["total_images"] == 1
    assert metadata["vlm_checked"] is True


@pytest.mark.parametrize("ext", sorted(IMAGE_EXTENSIONS))
def test_each_format_reaches_the_vlm_as_png(tmp_path, ext):
    vlm = _vlm()
    with patch("gaia.llm.VLMClient", return_value=vlm):
        _sdk(tmp_path)._extract_text_from_image(str(_image(tmp_path, ext)))
    (images,), _ = vlm.extract_from_page_images.call_args
    assert images[0]["image_bytes"].startswith(PNG_SIGNATURE)


def test_an_oversized_image_is_downscaled_before_it_is_sent(tmp_path):
    vlm = _vlm()
    with patch("gaia.llm.VLMClient", return_value=vlm):
        _sdk(tmp_path)._extract_text_from_image(
            str(_image(tmp_path, size=(4000, 1000)))
        )
    (images,), _ = vlm.extract_from_page_images.call_args
    assert max(images[0]["width"], images[0]["height"]) <= 1600


def test_the_request_reaching_lemonade_carries_the_image(tmp_path):
    """Real VLMClient, mocked transport: the chat request is a vision request."""
    model = "Qwen3-VL-4B-Instruct-GGUF"
    lemonade = MagicMock(name="LemonadeClient")
    lemonade.base_url = "http://localhost:13305/api/v1"
    lemonade.list_models.return_value = {"data": [{"id": model}]}
    lemonade.chat_completions.return_value = {
        "choices": [{"message": {"content": "Quarterly report"}}]
    }
    with patch("gaia.llm.lemonade_client.LemonadeClient", return_value=lemonade):
        text, _ = _sdk(tmp_path, vlm_model=model)._extract_text_from_image(
            str(_image(tmp_path, ".jpg"))
        )

    assert text == "Quarterly report"
    kwargs = lemonade.chat_completions.call_args.kwargs
    assert kwargs["model"] == model
    content = kwargs["messages"][0]["content"]
    url = next(c["image_url"]["url"] for c in content if c["type"] == "image_url")
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]).startswith(PNG_SIGNATURE)


def test_no_vlm_fails_with_the_model_and_where_to_get_it(tmp_path):
    from gaia.llm.lemonade_launcher import StartHint

    vlm = _vlm(available=False)
    pull = StartHint(
        instruction="Run: lemonade pull Qwen3-VL-4B-Instruct-GGUF",
        command="lemonade pull Qwen3-VL-4B-Instruct-GGUF",
    )
    with (
        patch("gaia.llm.VLMClient", return_value=vlm),
        patch(
            "gaia.llm.lemonade_launcher.describe_client_hint", return_value=pull
        ) as hint,
    ):
        with pytest.raises(ImageExtractionError) as excinfo:
            _sdk(tmp_path)._extract_text_from_image(str(_image(tmp_path)))

    message = str(excinfo.value)
    hint.assert_called_once_with("pull", "Qwen3-VL-4B-Instruct-GGUF")
    assert "scan.png" in message
    assert "Run: lemonade pull Qwen3-VL-4B-Instruct-GGUF" in message
    assert "Lemonade Model Manager (http://localhost:13305)" in message
    assert "gaia init" in message
    assert "lemonade-server" not in message
    vlm.extract_from_page_images.assert_not_called()


def test_no_vlm_and_no_lemonade_cli_names_no_command(tmp_path):
    from gaia.llm.lemonade_launcher import LemonadeTooling

    with (
        patch("gaia.llm.VLMClient", return_value=_vlm(available=False)),
        patch(
            "gaia.llm.lemonade_launcher.resolve_lemonade",
            return_value=LemonadeTooling(found=False, kind="none"),
        ),
    ):
        with pytest.raises(ImageExtractionError) as excinfo:
            _sdk(tmp_path)._extract_text_from_image(str(_image(tmp_path)))

    message = str(excinfo.value)
    assert "no Lemonade client CLI was found" in message
    assert "Run:" not in message


def test_use_vlm_off_fails_without_asking_the_vlm(tmp_path):
    with patch("gaia.llm.VLMClient") as ctor:
        with pytest.raises(ImageExtractionError, match="use_vlm"):
            _sdk(tmp_path, use_vlm=False)._extract_text_from_image(
                str(_image(tmp_path))
            )
    ctor.assert_not_called()


def test_a_file_that_is_not_an_image_fails_before_the_vlm(tmp_path):
    bogus = tmp_path / "photo.png"
    bogus.write_bytes(b"this is not an image")
    with patch("gaia.llm.VLMClient") as ctor:
        with pytest.raises(ImageExtractionError, match="Could not read image"):
            _sdk(tmp_path)._extract_text_from_image(str(bogus))
    ctor.assert_not_called()


def test_a_failed_vlm_read_is_an_error_not_indexed_text(tmp_path):
    vlm = _vlm()
    vlm.extract_from_page_images.side_effect = VLMExtractionError("backend down")
    with patch("gaia.llm.VLMClient", return_value=vlm):
        with pytest.raises(ImageExtractionError) as excinfo:
            _sdk(tmp_path)._extract_text_from_image(str(_image(tmp_path)))
    assert isinstance(excinfo.value.__cause__, VLMExtractionError)
    vlm.cleanup.assert_called_once()


def test_blank_vlm_output_is_an_error(tmp_path):
    with patch("gaia.llm.VLMClient", return_value=_vlm(text="  \n ")):
        with pytest.raises(ImageExtractionError, match="No text found"):
            _sdk(tmp_path)._extract_text_from_image(str(_image(tmp_path)))


def test_index_document_reports_the_no_vlm_error_and_indexes_nothing(tmp_path):
    rag = _sdk(tmp_path)
    with patch("gaia.llm.VLMClient", return_value=_vlm(available=False)):
        stats = rag.index_document(str(_image(tmp_path)))

    assert stats["success"] is False
    assert "Qwen3-VL-4B-Instruct-GGUF" in stats["error"]
    assert rag.chunks == [] and not rag.indexed_files
