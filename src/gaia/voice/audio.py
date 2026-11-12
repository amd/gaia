# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""PCM helpers: resampling, levels, G.711 µ-law, and the earcon tone."""

from __future__ import annotations

import numpy as np

ASR_RATE = 16000
TTS_RATE = 24000
PHONE_RATE = 8000


def pcm16_to_float(data: bytes) -> np.ndarray:
    if len(data) % 2:
        raise ValueError(f"PCM16 needs an even byte count, got {len(data)}")
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def float_to_pcm16(samples: np.ndarray) -> bytes:
    clipped = np.clip(samples, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


def resample(samples: np.ndarray, src: int, dst: int) -> np.ndarray:
    """Linear-interpolation resampling; enough for speech at these rates."""
    if src == dst or samples.size == 0:
        return samples.astype(np.float32, copy=False)
    n_out = int(round(samples.size * dst / src))
    if n_out <= 0:
        return np.zeros(0, dtype=np.float32)
    x_old = np.arange(samples.size, dtype=np.float64)
    x_new = np.linspace(0, samples.size - 1, n_out)
    return np.interp(x_new, x_old, samples).astype(np.float32)


def level(samples: np.ndarray) -> float:
    """RMS level mapped to 0..1 for the apps' animations."""
    if samples.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    return min(1.0, rms * 6.0)


_MU = 255.0
_BIAS = 0x84
_CLIP = 32635


def mulaw_encode(pcm: bytes) -> bytes:
    """PCM16 little-endian → G.711 µ-law bytes."""
    s = np.frombuffer(pcm, dtype="<i2").astype(np.int32)
    sign = np.where(s < 0, 0x80, 0)
    mag = np.minimum(np.abs(s), _CLIP) + _BIAS
    exponent = np.floor(np.log2(mag)).astype(np.int32) - 7
    exponent = np.clip(exponent, 0, 7)
    mantissa = (mag >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa) & 0xFF).astype(np.uint8).tobytes()


def mulaw_decode(data: bytes) -> bytes:
    """G.711 µ-law bytes → PCM16 little-endian."""
    u = ~np.frombuffer(data, dtype=np.uint8).astype(np.int32) & 0xFF
    sign = u & 0x80
    exponent = (u >> 4) & 0x07
    mantissa = u & 0x0F
    mag = (((mantissa << 3) + _BIAS) << exponent) - _BIAS
    return np.where(sign, -mag, mag).astype("<i2").tobytes()


def earcon(rate: int = TTS_RATE) -> bytes:
    """A short two-note chime that says "I heard you", as PCM16 at *rate*."""
    parts = []
    for freq, dur in ((880.0, 0.06), (1320.0, 0.09)):
        t = np.arange(int(rate * dur)) / rate
        tone = np.sin(2 * np.pi * freq * t)
        env = np.minimum(1.0, np.minimum(t / 0.008, (dur - t) / 0.03))
        parts.append(0.18 * tone * np.clip(env, 0.0, 1.0))
    return float_to_pcm16(np.concatenate(parts).astype(np.float32))
