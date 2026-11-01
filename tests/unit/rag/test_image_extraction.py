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

from gaia.llm.lemonade_client import LemonadeClientError, resident_vision_model
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
    # The keys extract_images_from_page_pdfium hands the PDF path.
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
        instruction="Run: lemonade pull Gemma-4-E4B-it-GGUF",
        command="lemonade pull Gemma-4-E4B-it-GGUF",
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
    hint.assert_called_once_with("pull", "Gemma-4-E4B-it-GGUF")
    assert "scan.png" in message
    assert "Run: lemonade pull Gemma-4-E4B-it-GGUF" in message
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
    assert "Gemma-4-E4B-it-GGUF" in stats["error"]
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
        with pytest.raises(ImageExtractionError, match="returned an error: 401") as e:
            _sdk(tmp_path)._extract_text_from_image(str(_image(tmp_path)))
    assert "Check the Lemonade Server log" in str(e.value)


def test_an_image_full_of_nul_bytes_is_read_not_refused_as_binary(tmp_path):
    rag = _sdk(tmp_path)
    path = tmp_path / "scan.png"
    path.write_bytes(PNG_SIGNATURE + b"\x00" * 4096)
    with (
        patch.object(rag, "_extract_text_from_image", return_value=("t", {})) as img,
        patch.object(rag, "_refuse_binary") as refuse,
    ):
        rag._extract_text_from_file(str(path))
    img.assert_called_once()
    refuse.assert_not_called()


@pytest.mark.parametrize("ext", [".heic", ".ico", ".bak"])
def test_an_unsupported_image_format_is_still_refused_as_binary(tmp_path, ext):
    path = tmp_path / f"photo{ext}"
    path.write_bytes(PNG_SIGNATURE + b"\x00" * 64)
    with pytest.raises(ValueError, match="binary file") as e:
        _sdk(tmp_path)._extract_text_from_file(str(path))
    assert not isinstance(e.value, ImageExtractionError)
    assert "images (PNG, JPG" in str(e.value)


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


def test_default_vision_model_is_one_gaia_init_downloads(tmp_path):
    """A fresh install must not need an extra download before indexing an image."""
    from gaia.installer.init_command import INIT_PROFILES

    with patch("gaia.llm.VLMClient", return_value=_vlm()) as client:
        _sdk(tmp_path)._extract_text_from_image(str(_image(tmp_path)))

    model = client.call_args.kwargs["vlm_model"]
    for profile in ("gaia", "chat", "rag", "minimal"):
        assert model in INIT_PROFILES[profile]["models"], profile


def test_an_explicit_vision_model_still_wins(tmp_path):
    with patch("gaia.llm.VLMClient", return_value=_vlm()) as client:
        _sdk(tmp_path, vlm_model="Qwen3-VL-4B-Instruct-GGUF")._extract_text_from_image(
            str(_image(tmp_path))
        )
    assert client.call_args.kwargs["vlm_model"] == "Qwen3-VL-4B-Instruct-GGUF"


def _scan_page(mode="RGB"):
    """A page-sized image with fine detail, far over the PNG budget."""
    import random

    rng = random.Random(1)
    page = Image.new(mode, (1400, 1900), "white")
    pixels = page.load()
    for _ in range(600_000):
        value = (rng.randrange(256),) * 3 + ((255,) if mode == "RGBA" else ())
        pixels[rng.randrange(1400), rng.randrange(1900)] = value
    buf = io.BytesIO()
    page.save(buf, format="PNG")
    return buf.getvalue()


def test_a_scanned_page_keeps_its_resolution():
    from gaia.rag.pdf_utils import prepare_image_for_vlm

    raw = _scan_page()
    assert len(raw) > 300 * 1024

    prepared = prepare_image_for_vlm(raw)

    assert (prepared["width"], prepared["height"]) == (1178, 1600)
    assert prepared["format"] == "jpeg"
    decoded = Image.open(io.BytesIO(prepared["image_bytes"]))
    assert decoded.format == "JPEG"
    assert decoded.size == (1178, 1600)
    assert prepared["size_kb"] == pytest.approx(len(prepared["image_bytes"]) / 1024)


def test_a_transparent_scan_goes_to_jpeg_on_white():
    from gaia.rag.pdf_utils import prepare_image_for_vlm

    prepared = prepare_image_for_vlm(_scan_page("RGBA"))

    assert prepared["format"] == "jpeg"
    assert Image.open(io.BytesIO(prepared["image_bytes"])).mode == "RGB"


def test_a_slide_picture_keeps_its_resolution_too():
    from unittest.mock import MagicMock

    from gaia.rag.pptx_utils import extract_images_from_slide

    picture = MagicMock()
    picture.shape_type = None
    picture.image.blob = _scan_page()
    slide = MagicMock()
    slide.shapes = [picture]

    (image,) = extract_images_from_slide(slide, slide_num=1)

    assert (image["width"], image["height"]) == (1178, 1600)


@pytest.mark.parametrize(
    "default_model, expected",
    [
        (None, "Gemma-4-E4B-it-GGUF"),
        ("Gemma-4-E4B-it-GGUF", "Gemma-4-E4B-it-GGUF"),
        ("Qwen3.6-35B-A3B-GGUF", "Qwen3.6-35B-A3B-GGUF"),
        ("fireworks.deepseek-v4p1-flash", "Gemma-4-E4B-it-GGUF"),
        ("LFM2-8B-A1B-GGUF", "Gemma-4-E4B-it-GGUF"),
    ],
)
def test_scans_use_a_vision_model_the_pc_has(
    tmp_path, monkeypatch, default_model, expected
):
    """Qwen3-VL-4B is never downloaded by gaia init, so it can't be the default."""
    monkeypatch.setenv("GAIA_HOME", str(tmp_path))
    if default_model:
        (tmp_path / "config.json").write_text(
            '{"default_model": "%s"}' % default_model, encoding="utf-8"
        )

    assert RAGConfig().vlm_model == expected


def _loaded(*models):
    return {"all_models_loaded": [{"model_name": m} for m in models]}


@pytest.mark.parametrize(
    "configured,loaded,expected",
    [
        # A tested vision model already resident is used, whichever one it is.
        ("Gemma-4-E4B-it-GGUF", ["Qwen3.6-35B-A3B-GGUF"], "Qwen3.6-35B-A3B-GGUF"),
        ("Qwen3.6-35B-A3B-GGUF", ["Gemma-4-E4B-it-GGUF"], "Gemma-4-E4B-it-GGUF"),
        # A text-only chat model is passed over for the configured one.
        ("Gemma-4-E4B-it-GGUF", ["LFM2-8B-A1B-GGUF"], "Gemma-4-E4B-it-GGUF"),
        ("Gemma-4-E4B-it-GGUF", [], "Gemma-4-E4B-it-GGUF"),
    ],
)
def test_scans_use_a_vision_model_already_loaded(configured, loaded, expected):
    with patch(
        "gaia.llm.lemonade_client.LemonadeClient.health_check",
        return_value=_loaded(*loaded),
    ):
        assert resident_vision_model(configured) == expected


def test_a_chosen_vision_model_is_not_replaced_by_a_loaded_one():
    with patch("gaia.llm.lemonade_client.LemonadeClient.health_check") as health:
        model = resident_vision_model("Qwen3-VL-4B-Instruct-GGUF")

    assert model == "Qwen3-VL-4B-Instruct-GGUF"
    health.assert_not_called()


def test_an_unreachable_server_keeps_the_configured_model():
    with patch(
        "gaia.llm.lemonade_client.LemonadeClient.health_check",
        side_effect=LemonadeClientError("not reachable"),
    ):
        assert resident_vision_model("Gemma-4-E4B-it-GGUF") == "Gemma-4-E4B-it-GGUF"


def test_indexing_a_scan_asks_for_the_loaded_model(tmp_path):
    with (
        patch(
            "gaia.llm.lemonade_client.LemonadeClient.health_check",
            return_value=_loaded("Qwen3.6-35B-A3B-GGUF"),
        ),
        patch("gaia.llm.VLMClient", return_value=_vlm()) as client,
    ):
        _sdk(tmp_path)._extract_text_from_image(str(_image(tmp_path)))

    assert client.call_args.kwargs["vlm_model"] == "Qwen3.6-35B-A3B-GGUF"
