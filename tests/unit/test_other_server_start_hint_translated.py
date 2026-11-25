# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Agent UI translates the "GAIA doesn't run this model server" line (#8345).

The app matches the backend's sentence by text to show it in the user's
language; a reworded sentence would go back to English-only in Arabic.
"""

from pathlib import Path

from gaia.llm.lemonade_launcher import OTHER_SERVER_START_HINT

WEBUI = Path(__file__).resolve().parents[2] / "src/gaia/apps/webui/src"


def test_the_app_matches_the_sentence_the_backend_sends():
    notice = (WEBUI / "components/settings/ErrorNotice.tsx").read_text(encoding="utf-8")
    assert f'"{OTHER_SERVER_START_HINT}"' in notice


def test_both_languages_word_it():
    for locale in ("en", "ar"):
        text = (WEBUI / f"i18n/locales/{locale}.ts").read_text(encoding="utf-8")
        assert "'server.otherServerStart':" in text, locale
        assert "'server.chosenServerAt':" in text, locale


def test_the_app_matches_the_sentence_naming_a_chosen_address():
    import re

    from gaia.llm.lemonade_launcher import chosen_server_start_hint

    notice = (WEBUI / "components/settings/ErrorNotice.tsx").read_text(encoding="utf-8")
    pattern = re.search(r"const CHOSEN_SERVER_AT = /(.+)/s;", notice).group(1)
    for url, address in (
        ("http://127.0.0.1:4328/api/v1", "127.0.0.1:4328"),
        ("http://[::1]:9000", "[::1]:9000"),
        ("http://gpu-box.lan", "gpu-box.lan"),
    ):
        match = re.match(pattern, chosen_server_start_hint(url), re.S)
        assert match, url
        assert match.groups() == (address, OTHER_SERVER_START_HINT)
