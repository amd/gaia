# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Acoustic speaker diarization — who spoke when, from the audio itself.

Text alone cannot tell one voice from another. Asked to segment a transcript by
speaker, a language model guesses: sixty labels in a four-person meeting, and no
way to know that the person talking at minute 2 is the same one at minute 40.
Pauses narrow it down but do not identify anyone.

This runs the real thing locally: pyannote's segmentation network and a
WeSpeaker embedding model, both exported to ONNX and executed by sherpa-onnx.
No PyTorch, no HuggingFace token, no gated licence — the models are plain
GitHub release downloads, redistributed under their original MIT terms.

Everything is fetched on first use, never at import or startup.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from gaia.logger import get_logger

log = get_logger(__name__)

DOCS_URL = "https://amd-gaia.ai/docs/guides/transcription"

#: Where the models live once fetched.
MODEL_DIR = Path.home() / ".gaia" / "models" / "diarization"

_SEGMENTATION_ARCHIVE = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
)
# The release tag really is spelled "recongition" upstream.
_EMBEDDING_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx"
)

_SEGMENTATION_MODEL = MODEL_DIR / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
_EMBEDDING_MODEL = MODEL_DIR / "wespeaker_en_voxceleb_resnet34_LM.onnx"

#: Measured on a 46-minute 4-speaker meeting: 0.50 over-split to 10 clusters and
#: 0.75 collapsed to 2, while 0.65 gave 5. Tuned on one recording, so it is a
#: sensible default rather than a universal constant — pass ``num_speakers``
#: when the count is actually known.
DEFAULT_CLUSTER_THRESHOLD = 0.65

#: Thread oversubscription measurably hurts: 16 and 32 threads ran 1.5-4x
#: SLOWER than 8 on the same file.
NUM_THREADS = 8

REQUIRED_SAMPLE_RATE = 16000


class DiarizationError(RuntimeError):
    """Diarization could not run. The message says what to do about it."""


@dataclass(frozen=True)
class SpeakerSpan:
    """A stretch of audio attributed to one voice."""

    start: float
    end: float
    speaker: str


def is_available() -> bool:
    """Whether diarization can run right now without downloading anything."""
    if not (_SEGMENTATION_MODEL.is_file() and _EMBEDDING_MODEL.is_file()):
        return False
    try:
        import sherpa_onnx  # noqa: F401
    except ImportError:
        return False
    return True


def ensure_ready(progress: Optional[Callable[[str], None]] = None) -> None:
    """Install sherpa-onnx and fetch the models, if they are not here yet.

    Roughly 40 MB of wheel and 33 MB of models, once. Raises
    :class:`DiarizationError` with the manual command when it cannot.
    """
    _say = progress or (lambda _m: None)
    _ensure_package(_say)
    _ensure_models(_say)


def diarize(
    wav_path: os.PathLike | str,
    num_speakers: Optional[int] = None,
    cluster_threshold: float = DEFAULT_CLUSTER_THRESHOLD,
    progress: Optional[Callable[[str], None]] = None,
) -> List[SpeakerSpan]:
    """Return who spoke when, in order.

    Args:
        wav_path: 16 kHz mono WAV — the same file transcription reads.
        num_speakers: Exact speaker count when known. Far more accurate than
            letting the clustering decide; leave ``None`` to auto-detect.
        cluster_threshold: Auto-detect sensitivity. Lower splits more.
        progress: Called with human-readable status while it runs.

    Raises:
        DiarizationError: components missing, or the audio is the wrong shape.
    """
    import numpy as np
    import sherpa_onnx

    path = Path(wav_path).expanduser()
    if not path.is_file():
        raise DiarizationError(f"No such audio file: {path}")

    samples, sample_rate = _read_wav(path)
    if sample_rate != REQUIRED_SAMPLE_RATE:
        raise DiarizationError(
            f"{path.name} is {sample_rate} Hz; diarization needs "
            f"{REQUIRED_SAMPLE_RATE} Hz mono. Decode it with "
            "gaia.audio.media.to_wav16k_mono() first."
        )

    clustering = (
        sherpa_onnx.FastClusteringConfig(num_clusters=int(num_speakers))
        if num_speakers
        else sherpa_onnx.FastClusteringConfig(
            num_clusters=-1, threshold=cluster_threshold
        )
    )
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(_SEGMENTATION_MODEL)
            ),
            num_threads=NUM_THREADS,
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(_EMBEDDING_MODEL), num_threads=NUM_THREADS
        ),
        clustering=clustering,
    )
    if not config.validate():
        raise DiarizationError(
            "The diarization models failed validation. Delete "
            f"{MODEL_DIR} and run this again to re-download them."
        )

    if progress:
        minutes = len(samples) / REQUIRED_SAMPLE_RATE / 60
        progress(
            f"Identifying speakers in {minutes:.0f}m of audio "
            f"— around {minutes * 0.09:.0f}m to go"
        )

    result = sherpa_onnx.OfflineSpeakerDiarization(config).process(
        np.asarray(samples, dtype="float32")
    )
    return [
        SpeakerSpan(start=s.start, end=s.end, speaker=f"Speaker {s.speaker + 1}")
        for s in result.sort_by_start_time()
    ]


def _read_wav(path: Path):
    """Read a mono WAV as float32 samples in -1..1."""
    import numpy as np

    with wave.open(str(path)) as handle:
        if handle.getnchannels() != 1:
            raise DiarizationError(
                f"{path.name} has {handle.getnchannels()} channels; "
                "diarization needs mono."
            )
        if handle.getsampwidth() != 2:
            raise DiarizationError(
                f"{path.name} is not 16-bit PCM, which diarization requires."
            )
        frames = handle.readframes(handle.getnframes())
        rate = handle.getframerate()
    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    return samples, rate


def _ensure_package(say: Callable[[str], None]) -> None:
    try:
        import sherpa_onnx  # noqa: F401

        return
    except ImportError:
        pass

    say("Installing the speaker-identification engine (about 40 MB, once)...")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "sherpa-onnx"],
            check=True,
            capture_output=True,
            text=True,
            timeout=900,
        )
    except subprocess.CalledProcessError as e:
        raise DiarizationError(
            "Could not install sherpa-onnx, which provides speaker "
            f"identification: {(e.stderr or e.stdout or '').strip()[:400]}\n"
            f"Install it manually with: {sys.executable} -m pip install "
            f"sherpa-onnx\nSee {DOCS_URL}"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise DiarizationError(
            "Installing sherpa-onnx timed out. Install it manually with: "
            f"{sys.executable} -m pip install sherpa-onnx\nSee {DOCS_URL}"
        ) from e

    try:
        import sherpa_onnx  # noqa: F401
    except ImportError as e:
        raise DiarizationError(
            "sherpa-onnx installed but cannot be imported. If GAIA is running "
            "from a different environment than the one just installed into, "
            "install it there instead. "
            f"See {DOCS_URL}"
        ) from e


def _ensure_models(say: Callable[[str], None]) -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    if not _SEGMENTATION_MODEL.is_file():
        say("Downloading the speaker-segmentation model (7 MB, once)...")
        archive = MODEL_DIR / "segmentation.tar.bz2"
        _download(_SEGMENTATION_ARCHIVE, archive)
        try:
            with tarfile.open(archive, "r:bz2") as tar:
                _safe_extract(tar, MODEL_DIR)
        except (tarfile.TarError, OSError) as e:
            raise DiarizationError(
                f"Could not unpack the segmentation model: {e}. Delete "
                f"{MODEL_DIR} and try again."
            ) from e
        finally:
            archive.unlink(missing_ok=True)

    if not _EMBEDDING_MODEL.is_file():
        say("Downloading the voice-embedding model (26 MB, once)...")
        _download(_EMBEDDING_URL, _EMBEDDING_MODEL)

    missing = [p for p in (_SEGMENTATION_MODEL, _EMBEDDING_MODEL) if not p.is_file()]
    if missing:
        raise DiarizationError(
            "The diarization models are still missing after download: "
            + ", ".join(p.name for p in missing)
            + f". Check network access to github.com, or see {DOCS_URL}"
        )


def _download(url: str, destination: Path) -> None:
    """Fetch to a temp file first so an interrupted download is not mistaken
    for a complete one on the next run."""
    import requests

    partial = destination.with_suffix(destination.suffix + ".part")
    try:
        with requests.get(url, stream=True, timeout=120) as response:
            response.raise_for_status()
            with partial.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1 << 20):
                    handle.write(chunk)
        partial.replace(destination)
    except requests.RequestException as e:
        partial.unlink(missing_ok=True)
        raise DiarizationError(
            f"Could not download {url}: {e}. Diarization needs one-time "
            f"access to github.com. See {DOCS_URL}"
        ) from e


def _safe_extract(tar: tarfile.TarFile, target: Path) -> None:
    """Extract without letting a crafted archive escape the target directory."""
    target = target.resolve()
    for member in tar.getmembers():
        destination = (target / member.name).resolve()
        if target not in destination.parents and destination != target:
            raise DiarizationError(
                f"Refusing to extract '{member.name}' — it points outside " f"{target}."
            )
    tar.extractall(target)
