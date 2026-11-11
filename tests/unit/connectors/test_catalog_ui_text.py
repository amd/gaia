# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Agent UI translates catalog text only while its English matches the catalog (#6860).

A catalog edit that isn't mirrored into ``locales/en.ts`` (and translated in every
other locale) would quietly put that connector back in English for non-English users.
"""

import re
from pathlib import Path

import pytest

import gaia.connectors.catalog  # noqa: F401  # populate REGISTRY
from gaia.connectors.registry import REGISTRY

_EN_TS = (
    Path(__file__).resolve().parents[3] / "src/gaia/apps/webui/src/i18n/locales/en.ts"
)
_ENTRY = re.compile(r"""^\s*'(connectors\.[^']+)':\s*(['"])(.*)\2,\s*$""")


def _ui_messages() -> dict[str, str]:
    messages = {}
    for line in _EN_TS.read_text(encoding="utf-8").splitlines():
        m = _ENTRY.match(line)
        if m:
            quote = m.group(2)
            messages[m.group(1)] = m.group(3).replace("\\" + quote, quote)
    return messages


def _catalog_text() -> dict[str, str]:
    text = {}
    for spec in REGISTRY.all():
        text[f"connectors.{spec.id}.description"] = spec.description
        for field in (*spec.oauth_setup_fields, *spec.config_schema):
            text[f"connectors.{spec.id}.{field.key}.label"] = field.label
            text[f"connectors.{spec.id}.{field.key}.help"] = field.help_md
    return text


def test_registry_is_populated():
    assert len(REGISTRY.all()) >= 2


@pytest.mark.parametrize("key", sorted(_catalog_text()))
def test_ui_english_matches_catalog(key):
    ui = _ui_messages()
    assert (
        key in ui
    ), f"{key} is missing from {_EN_TS}; add it there and in every other locale"
    assert ui[key] == _catalog_text()[key], (
        f"{key} in {_EN_TS} no longer matches the catalog; copy the catalog text "
        "and re-translate it in every other locale"
    )


def test_ui_has_no_keys_for_removed_catalog_text():
    stale = sorted(set(_ui_messages()) - set(_catalog_text()))
    assert (
        not stale
    ), f"remove these from every locale; the catalog no longer sends them: {stale}"
