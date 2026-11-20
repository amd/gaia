# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Caps and thresholds for ``triage_logs``. Every cap is reported when it bites."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Limits. Every one of them is reported when it bites.
# ---------------------------------------------------------------------------

MAX_FILES = 500
MAX_TEMPLATES = 20_000
MAX_HOSTS_PER_TEMPLATE = 2_000
MAX_HOSTS_TOTAL = 20_000
MAX_CELL_HOSTS = 32
MAX_ENTITIES_PER_TEMPLATE = 20_000
MAX_ENTITIES_TOTAL = 200_000
MAX_ENTITY_BUCKET_PAIRS = 3_000_000
MAX_ENTITY_KINDS = 300
#: Entity lists up to this long ride inline in the compact result.
OUT_ENTITY_LIST = 150
MAX_LINE_CHARS = 8_192
#: The rest of a longer line is read in chunks of this size and dropped.
READ_CHUNK_CHARS = 64 * 1024
MAX_MESSAGE_CHARS = 1_500
TARGET_BUCKETS = 720
MAX_BUCKETS = 3_000
DEFAULT_TIME_BUDGET_S = 540.0
SAMPLE_HEAD_LINES = 400
SAMPLE_MID_LINES = 200
SAMPLE_TAIL_BYTES = 64 * 1024
#: A line's own timestamp starts within this many characters.
TS_SEARCH_CHARS = 60
MERGE_MIN_VARIANTS = 8
#: A caller's regex may take this long on one probe line, on this many lines.
REGEX_PROBE_BUDGET_S = 0.25
REGEX_PROBE_LINES = 20

# Classification thresholds (percent of the analysed time range unless noted).
BASELINE_MIN_SPAN_PCT = 60.0
BASELINE_MIN_QUARTERS = 3
SHORT_SPAN_PCT = 20.0
CLUSTER_WINDOW_FRAC = 0.10
CLUSTER_SHARE = 0.80
SPIKE_FACTOR = 6.0
SPIKE_MIN_LINES = 10
ESCALATION_FACTOR = 4.0
ESCALATION_MIN_COUNT = 20
NEW_TEMPLATE_MIN_FIRST_PCT = 10.0
NEW_TEMPLATE_EXPECTED_BEFORE = 5.0
BLAST_MIN_HOSTS = 3
BLAST_HOST_SHARE = 0.30
BURST_MIN_LINES = 8
#: A bucket needs this many lines to start a burst; the burst needs BURST_MIN_LINES.
BURST_MIN_BUCKET = 3
BURST_MEDIAN_FACTOR = 3.0
BURST_SIGMAS = 6.0
ENTITY_SHARE_WARN = 0.30
#: Above this share of all entities a list is not "the anomalous ones": a
#: criterion that matches this many describes normal operation.
ENTITY_SHARE_IMPLAUSIBLE = 1 / 3
#: Shares are not judged on fewer entities than this.
ENTITY_SHARE_MIN_TOTAL = 20
#: A share is of a known population only when routine templates name at
#: least this share of the entities; ids seen only in anomaly lines are all
#: flagged by construction.
ENTITY_BASELINE_MIN_SHARE = 0.05
#: Below this share of parsed lines a file's format was probably misread.
MIN_PARSED_SHARE = 0.95
ENTITY_TEMPLATE_SHARE_DROP = 0.50
#: A steady error in a range this short may be an outage that covers the log.
PERSISTENT_SHORT_RANGE_S = 2 * 3600.0
PERSISTENT_READING = (
    "steady across the whole range: either routine or an outage that covers "
    "the whole log"
)

# Output caps: the compact result must fit a small model's context.
OUT_TEMPLATE_CHARS = 160
OUT_HOSTS = 5
OUT_FILES = 20
OUT_BURSTS = 5
OUT_TIMELINE = 15
OUT_BASELINE = 8
OUT_PERSISTENT = 5
OUT_ENTITIES = 25
OUT_WARNINGS = 12
OUT_WARNING_CHARS = 300
OUT_SAMPLES = 2
OUT_SAMPLE_CHARS = 160
DEFAULT_TOP_N = 10
MAX_TOP_N = 50

#: Extensions that are never logs; skipped when a folder is walked.
NON_LOG_SUFFIXES = frozenset(
    {
        ".zip", ".7z", ".rar", ".tar", ".bz2", ".xz", ".zst", ".png", ".jpg",
        ".jpeg", ".gif", ".bmp", ".ico", ".pdf", ".docx", ".xlsx", ".pptx",
        ".exe", ".dll", ".so", ".dylib", ".pyc", ".class", ".jar", ".db",
        ".sqlite", ".parquet", ".pkl", ".npy", ".mp3", ".mp4", ".wav",
    }
)  # fmt: skip

OUTPUT_MARKER = "gaia.triage_logs"
BOM = "﻿"
#: What a byte that is not valid in the file's encoding is decoded to.
REPLACEMENT_CHAR = "\ufffd"
