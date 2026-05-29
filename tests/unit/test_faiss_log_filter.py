# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for faiss.loader log-noise suppression.

faiss-cpu's loader tries AVX-512 → AVX2 → generic SWIG backends, logging each
failed attempt at INFO level.  GaiaLogger's ``filter_faiss_loader`` suppresses
the noisy fallback messages while keeping the final "Successfully loaded" line.
"""

import logging

import pytest

from gaia.logger import GaiaLogger


@pytest.fixture()
def gaia_logger(tmp_path):
    """Create a GaiaLogger that writes to a temp file (avoids side-effects)."""
    return GaiaLogger(log_file=tmp_path / "test.log")


class TestFaissLoaderFilter:
    """Verify filter_faiss_loader keeps the right messages."""

    @staticmethod
    def _make_record(msg: str) -> logging.LogRecord:
        return logging.LogRecord(
            name="faiss.loader",
            level=logging.INFO,
            pathname="loader.py",
            lineno=120,
            msg=msg,
            args=(),
            exc_info=None,
        )

    def test_suppresses_avx512_attempt(self, gaia_logger):
        rec = self._make_record("Loading faiss with AVX512 support.")
        assert gaia_logger.filter_faiss_loader(rec) is False

    def test_suppresses_avx2_attempt(self, gaia_logger):
        rec = self._make_record("Loading faiss with AVX2 support.")
        assert gaia_logger.filter_faiss_loader(rec) is False

    def test_suppresses_generic_attempt(self, gaia_logger):
        rec = self._make_record("Loading faiss.")
        assert gaia_logger.filter_faiss_loader(rec) is False

    def test_suppresses_avx512_failure(self, gaia_logger):
        rec = self._make_record(
            "Could not load library with AVX512 support due to:\n"
            "ModuleNotFoundError(\"No module named 'faiss.swigfaiss_avx512'\")"
        )
        assert gaia_logger.filter_faiss_loader(rec) is False

    def test_suppresses_avx2_failure(self, gaia_logger):
        rec = self._make_record(
            "Could not load library with AVX2 support due to:\n"
            "ModuleNotFoundError(\"No module named 'faiss.swigfaiss_avx2'\")"
        )
        assert gaia_logger.filter_faiss_loader(rec) is False

    def test_keeps_success_avx512(self, gaia_logger):
        rec = self._make_record("Successfully loaded faiss with AVX512 support.")
        assert gaia_logger.filter_faiss_loader(rec) is True

    def test_keeps_success_avx2(self, gaia_logger):
        rec = self._make_record("Successfully loaded faiss with AVX2 support.")
        assert gaia_logger.filter_faiss_loader(rec) is True

    def test_keeps_success_generic(self, gaia_logger):
        rec = self._make_record("Successfully loaded faiss.")
        assert gaia_logger.filter_faiss_loader(rec) is True

    def test_keeps_unrelated_message(self, gaia_logger):
        rec = self._make_record("Some other faiss message")
        assert gaia_logger.filter_faiss_loader(rec) is True

    def test_filter_installed_on_logger(self, gaia_logger):
        """GaiaLogger.__init__ installs the filter on the faiss.loader logger."""
        faiss_logger = logging.getLogger("faiss.loader")
        # The filter is installed as a bound method; check it's present by
        # verifying at least one filter on faiss.loader matches ours.
        assert any(
            getattr(f, "__name__", "") == "filter_faiss_loader"
            or (hasattr(f, "__self__") and hasattr(f.__self__, "filter_faiss_loader"))
            for f in faiss_logger.filters
        ), "filter_faiss_loader not installed on faiss.loader logger"
