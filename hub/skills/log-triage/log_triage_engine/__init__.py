# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The streaming log-triage engine behind the ``log-triage`` skill's ``triage_logs``.

- ``limits``: caps and thresholds
- ``parsing``: severity, timestamps, masking, entities and line parsers
- ``reading``: opening and sampling files, format detection
- ``analysis``: the streaming pass, classification, bursts and entities
- ``report``: input resolution, ``triage`` and its result
"""
