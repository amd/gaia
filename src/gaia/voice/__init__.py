# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Dictation and voice mode: the audio half of a voice session.

See docs/plans/voice.mdx. The apps run voice turns through their normal chat
path; this package listens, transcribes, speaks and interrupts.
"""

from gaia.voice.errors import VoiceError
from gaia.voice.session import VoiceOptions, VoiceSession

__all__ = ["VoiceError", "VoiceOptions", "VoiceSession"]
