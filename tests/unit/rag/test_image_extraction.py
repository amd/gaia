# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Image files are indexed by the text the vision model reads from them (#730).

The VLM is mocked; nothing reaches a Lemonade server. The tests pin the call
the image path makes against the call the PDF path makes, and that an image
the VLM cannot read fails instead of indexing empty or placeholder text.
"""

from __future__ import annotations

import base64
import io
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
    assert "isn't reachable" not in message
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


# ---------------------------------------------------------------------------
# Server down vs. model missing
# ---------------------------------------------------------------------------


def test_a_down_server_says_unreachable_not_download(tmp_path):
    from gaia.llm.lemonade_launcher import StartHint

    vlm = _vlm(available=False)
    vlm.client.health_check.side_effect = ConnectionError("connection refused")
    with (
        patch("gaia.llm.VLMClient", return_value=vlm),
        patch(
            "gaia.llm.lemonade_launcher.describe_start_hint",
            return_value=StartHint(instruction="Start the Lemonade app."),
        ),
    ):
        with pytest.raises(ImageExtractionError) as excinfo:
            _sdk(tmp_path)._extract_text_from_image(str(_image(tmp_path)))

    message = str(excinfo.value)
    assert "isn't reachable at http://localhost:13305" in message
    assert "Start the Lemonade app." in message
    assert "Download" not in message and "pull" not in message
    assert isinstance(excinfo.value.__cause__, ConnectionError)


def test_a_server_error_that_is_not_a_connection_failure_is_reported_as_is(
    tmp_path,
):
    from gaia.llm.lemonade_client import LemonadeClientError

    vlm = _vlm(available=False)
    vlm.client.health_check.side_effect = LemonadeClientError("401 unauthorized")
    with patch("gaia.llm.VLMClient", return_value=vlm):
        with pytest.raises(ImageExtractionError, match="returned an error: 401"):
            _sdk(tmp_path)._extract_text_from_image(str(_image(tmp_path)))


# ---------------------------------------------------------------------------
# What the VLM is shown
# ---------------------------------------------------------------------------


def _sent_images(tmp_path, path):
    vlm = _vlm()
    with patch("gaia.llm.VLMClient", return_value=vlm):
        _, metadata = _sdk(tmp_path)._extract_text_from_image(str(path))
    calls = vlm.extract_from_page_images.call_args_list
    return [c.args[0][0] for c in calls], metadata


def _decoded(image):
    return Image.open(io.BytesIO(image["image_bytes"])).convert("RGB")


def test_exif_orientation_is_applied_before_the_vlm_sees_a_photo(tmp_path):
    path = tmp_path / "portrait.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6  # stored sideways; shown rotated 90 degrees clockwise
    Image.new("RGB", (400, 200), "white").save(path, format="JPEG", exif=exif)

    (image,), _ = _sent_images(tmp_path, path)
    assert (image["width"], image["height"]) == (200, 400)


def test_every_page_of_a_multi_page_tiff_is_read(tmp_path):
    path = tmp_path / "fax.tiff"
    first = Image.new("RGB", (300, 100), "white")
    first.save(path, format="TIFF", save_all=True, append_images=[first.copy()])
    vlm = _vlm()
    vlm.extract_from_page_images.side_effect = lambda images, page_num: [
        {"image_num": 1, "text": f"page {page_num} text", "size_kb": 1}
    ]
    with patch("gaia.llm.VLMClient", return_value=vlm):
        text, metadata = _sdk(tmp_path)._extract_text_from_image(str(path))

    calls = vlm.extract_from_page_images.call_args_list
    assert [c.kwargs["page_num"] for c in calls] == [1, 2]
    assert text == "[Page 1]\npage 1 text\n\n[Page 2]\npage 2 text"
    assert metadata["num_pages"] == 2
    assert metadata["vlm_pages"] == 2 and metadata["total_images"] == 2


def test_a_tiff_with_too_many_pages_fails_naming_the_count(tmp_path):
    path = tmp_path / "archive.tif"
    page = Image.new("L", (20, 20), 255)
    page.save(path, format="TIFF", save_all=True, append_images=[page] * 3)
    with (
        patch("gaia.rag.sdk.MAX_IMAGE_PAGES", 3),
        patch("gaia.llm.VLMClient") as ctor,
    ):
        with pytest.raises(ImageExtractionError, match="it has 4 pages"):
            _sdk(tmp_path)._extract_text_from_image(str(path))
    ctor.assert_not_called()


def test_an_animated_gif_is_read_from_its_first_frame(tmp_path):
    path = tmp_path / "loop.gif"
    frames = [Image.new("P", (40, 40), i) for i in range(3)]
    frames[0].save(path, format="GIF", save_all=True, append_images=frames[1:])

    images, metadata = _sent_images(tmp_path, path)
    assert len(images) == 1 and metadata["vlm_pages"] == 1


@pytest.mark.parametrize("mode", ["RGBA", "LA"])
def test_a_transparent_background_reaches_the_vlm_as_white(tmp_path, mode):
    path = tmp_path / "diagram.png"
    Image.new(mode, (50, 50), 0).save(path)  # fully transparent black

    (image,), _ = _sent_images(tmp_path, path)
    assert _decoded(image).getpixel((0, 0)) == (255, 255, 255)


def test_palette_transparency_reaches_the_vlm_as_white(tmp_path):
    path = tmp_path / "icon.png"
    img = Image.new("P", (50, 50), 0)
    img.putpalette([0, 0, 0] * 256)
    img.save(path, transparency=0)

    (image,), _ = _sent_images(tmp_path, path)
    assert _decoded(image).getpixel((0, 0)) == (255, 255, 255)


def test_16_bit_grayscale_keeps_its_tones(tmp_path):
    path = tmp_path / "scan16.png"
    Image.new("I;16", (50, 50), 30000).save(path)

    (image,), _ = _sent_images(tmp_path, path)
    assert _decoded(image).getpixel((0, 0)) == (116, 116, 116)


def test_the_shared_helper_scales_16_bit_and_leaves_alpha_alone():
    """The PDF path's helper: 16-bit fixed, transparency untouched."""
    from gaia.rag.pdf_utils import prepare_image_for_vlm

    buf = io.BytesIO()
    Image.new("I;16", (10, 10), 30000).save(buf, format="PNG")
    assert _decoded(prepare_image_for_vlm(buf.getvalue())).getpixel((0, 0)) == (
        116,
        116,
        116,
    )

    buf = io.BytesIO()
    Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(buf, format="PNG")
    prepared = prepare_image_for_vlm(buf.getvalue())
    assert Image.open(io.BytesIO(prepared["image_bytes"])).mode == "RGBA"
