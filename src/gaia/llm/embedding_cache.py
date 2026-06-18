# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Content-keyed LRU cache for text embeddings.

Skips redundant per-turn embeds: the same query text re-embedded across
turns (or by two tool calls in one turn) pays the Lemonade embed cost once.

The cache key *is* the content — ``(model_id, dim, sha256(text))`` — so a hit
is never stale, and swapping the embedding model invalidates by construction
(the model_id component changes). ``dim`` may be ``None`` when the caller does
not track an expected dimensionality; the model_id alone still guarantees
correctness.
"""

import hashlib
import threading
from collections import OrderedDict
from typing import Optional, Tuple

import numpy as np

DEFAULT_MAX_ENTRIES = 512


class EmbeddingCache:
    """Thread-safe, bounded LRU cache mapping text content to its embedding.

    Vectors are stored and returned as copies, so callers can mutate the
    returned array (e.g. L2-normalize in place) without corrupting the cache.
    """

    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES):
        if max_entries < 1:
            raise ValueError(f"max_entries must be >= 1, got {max_entries}")
        self._max_entries = max_entries
        self._store: "OrderedDict[Tuple[str, Optional[int], str], np.ndarray]" = (
            OrderedDict()
        )
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _make_key(
        model_id: str, dim: Optional[int], text: str
    ) -> Tuple[str, Optional[int], str]:
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return (model_id, dim, text_hash)

    def get(self, model_id: str, dim: Optional[int], text: str) -> Optional[np.ndarray]:
        """Return a copy of the cached vector, or ``None`` on a miss."""
        key = self._make_key(model_id, dim, text)
        with self._lock:
            vec = self._store.get(key)
            if vec is None:
                self.misses += 1
                return None
            self._store.move_to_end(key)
            self.hits += 1
            return vec.copy()

    def put(
        self, model_id: str, dim: Optional[int], text: str, vector: np.ndarray
    ) -> None:
        """Store a copy of ``vector`` under the content key, evicting LRU entries."""
        key = self._make_key(model_id, dim, text)
        with self._lock:
            self._store[key] = np.asarray(vector).copy()
            self._store.move_to_end(key)
            while len(self._store) > self._max_entries:
                self._store.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self.hits = 0
            self.misses = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)
