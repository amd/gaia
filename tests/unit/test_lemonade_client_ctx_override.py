# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Fixed contract (#1892): LemonadeClient instance-scoped exact-pin ctx override.

``LemonadeClient`` does not yet accept a ``ctx_size_override`` constructor
kwarg / attribute. This file is the TDD pin for it:

1. ``ctx_size_override`` is INSTANCE-scoped only -- never a module/class-level
   flag, never a mutation of the shared ``MODELS`` dict.
2. When set, ``_ensure_model_loaded`` switches from FLOOR semantics
   (``loaded_ctx >= expected_ctx`` -> no-op) to EXACT-PIN semantics: re-pin
   whenever ``loaded_ctx != override``, even when the loaded ctx is HIGHER
   than the override (a floor check would wrongly no-op here).
3. The non-override path keeps today's floor semantics unchanged (regression
   guard).

ASYNC-SETTLE BOUNDARY (amended after Lemonade 10.7 hardware findings):
Lemonade's ``/load`` and ``/unload`` are ASYNCHRONOUS, and ``/load`` on an
already-loaded model can no-op with a success status while leaving the STALE
context window in place. A plain reload therefore cannot re-pin the window.
The only reliable re-pin sequence is:

    unload -> poll /health until the model entry is ABSENT
          -> load with ctx_size
          -> poll /health until the model entry is PRESENT
          -> verify recipe_options.ctx_size == requested

So when ``ctx_size_override`` is set and the model is NOT already loaded at
exactly the override, ``_ensure_model_loaded`` must:

1. ``unload_model(model, ignore_if_not_loaded=True)`` exactly once.
2. Poll ``get_status()`` until the model entry is ABSENT (deadline
   ``PIN_UNLOAD_SETTLE_DEADLINE_S`` = 120.0; interval
   ``PIN_SETTLE_POLL_INTERVAL_S`` = 2.0).
3. ``load_model(model, auto_download=True, prompt=False, ctx_size=<override>)``
   exactly once.
4. Poll ``get_status()`` until the model entry is PRESENT (deadline
   ``PIN_LOAD_SETTLE_DEADLINE_S`` = 300.0).
5. Read the settled ``recipe_options.ctx_size``: == override -> return;
   != override -> raise loudly naming BOTH values (requested + actual,
   mentioning a possible model ctx ceiling); never-settles (deadline
   exhausted in step 2 OR step 4) -> raise a DISTINCT loud error naming the
   deadline and the observed /health loaded-models state.

Both settle deadlines and the poll interval are module-level constants in
``gaia.llm.lemonade_client`` so tests can monkeypatch them to run instantly.
Exceptions from this pinned path PROPAGATE to the caller -- they are NOT
swallowed by ``_ensure_model_loaded``'s historical best-effort debug-swallow.
"""

from unittest.mock import patch

import pytest

from gaia.llm.lemonade_client import LemonadeClient, LemonadeStatus


def _status(entries):
    """Build a LemonadeStatus whose ``loaded_models`` is ``entries``.

    ``entries`` is a list of (id, ctx) tuples, or ``[]`` for an absent model.
    """
    return LemonadeStatus(
        url="http://localhost:13305",
        running=True,
        loaded_models=[
            {
                "id": model_id,
                "model_name": model_id,
                "recipe_options": {"ctx_size": ctx},
            }
            for model_id, ctx in entries
        ],
    )


def _present(model_id, ctx):
    return _status([(model_id, ctx)])


def _absent():
    return _status([])


class TestCtxOverrideIsInstanceScoped:
    def test_ctx_override_is_instance_scoped(self):
        """Setting the override on one instance must not leak to another
        instance constructed afterward in the same process."""
        client_a = LemonadeClient(host="localhost", port=13305, ctx_size_override=4096)
        client_b = LemonadeClient(host="localhost", port=13305)

        assert client_a.ctx_size_override == 4096
        assert client_b.ctx_size_override is None

    def test_default_ctx_size_override_is_none(self):
        client = LemonadeClient(host="localhost", port=13305)
        assert client.ctx_size_override is None

    def test_ctx_size_override_is_not_a_class_attribute_mutation(self):
        """Setting the override on an instance must not become visible as a
        class-level default for instances constructed with no override --
        i.e. it must not be implemented as a mutable class attribute."""
        client_a = LemonadeClient(host="localhost", port=13305, ctx_size_override=8192)
        assert client_a.ctx_size_override == 8192

        client_b = LemonadeClient(host="localhost", port=13305)
        assert client_b.ctx_size_override is None

        # Also assert the class itself carries no non-None default that a
        # future instance would inherit.
        assert getattr(LemonadeClient, "ctx_size_override", None) is None

    def test_ctx_size_override_does_not_mutate_models_registry(self):
        """The override must be a client-instance concern -- it must never
        write into the shared MODELS dict (which is process-wide and shared
        across every LemonadeClient instance)."""
        from gaia.llm.lemonade_client import MODELS

        # Snapshot the registry's min_ctx_size values before constructing an
        # overridden client.
        before = {k: v.min_ctx_size for k, v in MODELS.items()}

        LemonadeClient(host="localhost", port=13305, ctx_size_override=4096)

        after = {k: v.min_ctx_size for k, v in MODELS.items()}
        assert before == after


class TestEnsureModelLoadedExactPin:
    @patch("gaia.llm.lemonade_client.PIN_SETTLE_POLL_INTERVAL_S", 0)
    @patch.object(LemonadeClient, "unload_model")
    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_ensure_model_loaded_exact_pins_override(
        self, mock_load, mock_status, mock_unload
    ):
        """A model loaded at ctx=16384 with override=4096 must re-pin at
        exactly 4096 via the async unload -> settle-absent -> load ->
        settle-present sequence. A floor check (16384 >= 4096) would wrongly
        no-op here, but exact-pin requires an exact match."""
        model = "Gemma-4-E4B-it-GGUF"
        client = LemonadeClient(host="localhost", port=13305, ctx_size_override=4096)
        # Sequence: initial check (present@16384), one post-unload settling
        # poll still showing the stale entry, then absent, then load-settled
        # present@4096.
        mock_status.side_effect = [
            _present(model, 16384),  # initial "is it already pinned?" check
            _present(model, 16384),  # unload not yet settled
            _absent(),  # unload settled
            _present(model, 4096),  # load settled at the override
        ]

        client._ensure_model_loaded(model, auto_download=True)

        mock_unload.assert_called_once()
        mock_load.assert_called_once_with(
            model,
            auto_download=True,
            prompt=False,
            ctx_size=4096,
        )

    @patch("gaia.llm.lemonade_client.PIN_SETTLE_POLL_INTERVAL_S", 0)
    @patch.object(LemonadeClient, "unload_model")
    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_exact_pin_skips_reload_when_already_at_override(
        self, mock_load, mock_status, mock_unload
    ):
        """When the loaded ctx already equals the override, no re-pin is
        needed -- exact-pin is a ``!=`` check, not always-reload. Neither
        unload nor load fires."""
        model = "Gemma-4-E4B-it-GGUF"
        client = LemonadeClient(host="localhost", port=13305, ctx_size_override=4096)
        mock_status.return_value = _present(model, 4096)

        client._ensure_model_loaded(model, auto_download=True)

        mock_unload.assert_not_called()
        mock_load.assert_not_called()

    @patch("gaia.llm.lemonade_client.PIN_SETTLE_POLL_INTERVAL_S", 0)
    @patch.object(LemonadeClient, "unload_model")
    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_exact_pin_reloads_when_loaded_ctx_is_lower_than_override(
        self, mock_load, mock_status, mock_unload
    ):
        """Sanity companion to the exact-pin test: the lower-than-override
        direction (which floor semantics also would have reloaded) must still
        re-pin at exactly the override via the async settle sequence."""
        model = "Gemma-4-E4B-it-GGUF"
        client = LemonadeClient(host="localhost", port=13305, ctx_size_override=16384)
        mock_status.side_effect = [
            _present(model, 4096),  # initial check
            _absent(),  # unload settled
            _present(model, 16384),  # load settled at the override
        ]

        client._ensure_model_loaded(model, auto_download=True)

        mock_unload.assert_called_once()
        mock_load.assert_called_once_with(
            model,
            auto_download=True,
            prompt=False,
            ctx_size=16384,
        )


class TestExactPinAsyncSettling:
    @patch("gaia.llm.lemonade_client.PIN_SETTLE_POLL_INTERVAL_S", 0)
    @patch.object(LemonadeClient, "unload_model")
    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_pin_survives_async_load_noop(self, mock_load, mock_status, mock_unload):
        """The re-pin must survive BOTH async no-op traps: an unload that
        keeps reporting the stale entry for several polls, and a load that
        reports the model absent for several polls before it appears at the
        override. unload/load each fire exactly once regardless."""
        model = "Gemma-4-E4B-it-GGUF"
        client = LemonadeClient(host="localhost", port=13305, ctx_size_override=16384)
        mock_status.side_effect = [
            _present(model, 32768),  # initial check -> needs re-pin
            _present(model, 32768),  # async unload: still stale (poll 1)
            _present(model, 32768),  # async unload: still stale (poll 2)
            _absent(),  # unload finally settled
            _absent(),  # async load: not yet present (poll 1)
            _present(model, 16384),  # load settled at the override
        ]

        client._ensure_model_loaded(model, auto_download=True)

        mock_unload.assert_called_once()
        mock_load.assert_called_once_with(
            model,
            auto_download=True,
            prompt=False,
            ctx_size=16384,
        )

    @patch("gaia.llm.lemonade_client.PIN_SETTLE_POLL_INTERVAL_S", 0)
    @patch("gaia.llm.lemonade_client.PIN_UNLOAD_SETTLE_DEADLINE_S", 0.05)
    @patch.object(LemonadeClient, "unload_model")
    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_pin_fails_loud_when_unload_never_settles(
        self, mock_load, mock_status, mock_unload
    ):
        """If the model never leaves /health after unload, the pin path must
        raise a DISTINCT loud error naming the deadline/timeout AND the
        observed loaded state -- not silently proceed to load."""
        model = "Gemma-4-E4B-it-GGUF"
        client = LemonadeClient(host="localhost", port=13305, ctx_size_override=16384)
        # Forever stale: unload never settles.
        mock_status.return_value = _present(model, 32768)

        with pytest.raises(Exception) as excinfo:  # noqa: PT011
            client._ensure_model_loaded(model, auto_download=True)

        msg = str(excinfo.value).lower()
        # Names the deadline / timeout ...
        assert (
            "deadline" in msg
            or "timeout" in msg
            or "timed out" in msg
            or "settle" in msg
        )
        # ... and the observed loaded state (the model / its stale ctx).
        assert model.lower() in msg or "32768" in msg
        # A never-settled unload must not proceed to load.
        mock_load.assert_not_called()

    @patch("gaia.llm.lemonade_client.PIN_SETTLE_POLL_INTERVAL_S", 0)
    @patch.object(LemonadeClient, "unload_model")
    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_pin_fails_loud_when_settled_at_wrong_ctx(
        self, mock_load, mock_status, mock_unload
    ):
        """If the model settles PRESENT but at a ctx != override (e.g. the
        server clamped to a model ceiling), raise a loud mismatch error
        naming BOTH the requested override and the actual settled ctx."""
        model = "Gemma-4-E4B-it-GGUF"
        client = LemonadeClient(host="localhost", port=13305, ctx_size_override=16384)
        mock_status.side_effect = [
            _present(model, 32768),  # initial check -> needs re-pin
            _absent(),  # unload settled
            _present(model, 32768),  # load settled but at the WRONG ctx
        ]

        with pytest.raises(Exception) as excinfo:  # noqa: PT011
            client._ensure_model_loaded(model, auto_download=True)

        msg = str(excinfo.value)
        assert "16384" in msg  # the requested override
        assert "32768" in msg  # the actual settled ctx

    @patch("gaia.llm.lemonade_client.PIN_SETTLE_POLL_INTERVAL_S", 0)
    @patch("gaia.llm.lemonade_client.PIN_UNLOAD_SETTLE_DEADLINE_S", 0.05)
    @patch.object(LemonadeClient, "unload_model")
    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_pin_error_propagates_out_of_ensure_model_loaded(
        self, mock_load, mock_status, mock_unload
    ):
        """Regression guard for the historical best-effort debug-swallow:
        a pin-path failure must PROPAGATE out of _ensure_model_loaded, not be
        caught and logged. Uses the never-settles case to force a raise."""
        model = "Gemma-4-E4B-it-GGUF"
        client = LemonadeClient(host="localhost", port=13305, ctx_size_override=16384)
        mock_status.return_value = _present(model, 32768)

        with pytest.raises(Exception):  # noqa: PT011
            client._ensure_model_loaded(model, auto_download=True)


class TestNonOverridePathKeepsFloorSemantics:
    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_no_override_and_loaded_ctx_at_or_above_expected_does_not_reload(
        self, mock_load, mock_status
    ):
        """Regression guard: a client with NO ctx_size_override set must keep
        today's floor semantics -- loaded_ctx >= expected_ctx (from the
        MODELS registry lookup) is still a no-op, not a forced exact-pin
        reload."""
        client = LemonadeClient(host="localhost", port=13305)
        assert client.ctx_size_override is None

        # Qwen3-0.6B-GGUF is in MODELS with min_ctx_size=4096 (the smallest
        # registered model) -- loaded at exactly that floor must no-op.
        mock_status.return_value = _present("Qwen3-0.6B-GGUF", 4096)

        client._ensure_model_loaded("Qwen3-0.6B-GGUF", auto_download=True)

        mock_load.assert_not_called()

    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_no_override_and_loaded_ctx_above_expected_still_does_not_reload(
        self, mock_load, mock_status
    ):
        """Floor semantics: a HIGHER-than-expected loaded ctx must still
        no-op with no override set -- this is the case exact-pin
        deliberately changes when an override IS set (see
        test_ensure_model_loaded_exact_pins_override above)."""
        client = LemonadeClient(host="localhost", port=13305)
        assert client.ctx_size_override is None

        mock_status.return_value = _present("Qwen3-0.6B-GGUF", 16384)

        client._ensure_model_loaded("Qwen3-0.6B-GGUF", auto_download=True)

        mock_load.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
