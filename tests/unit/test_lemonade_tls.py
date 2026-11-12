# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA names a missing CA file instead of blaming the key or the download (#7226).

The bundled Linux lemond reads only /etc/ssl/certs/ca-certificates.crt; Fedora
44's original ca-certificates build has no such file.
"""

from __future__ import annotations

import responses

from gaia.llm import lemonade_tls
from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError
from gaia.llm.lemonade_tls import (
    LEMOND_CA_FILE,
    ca_store_problem,
    explain_tls_failure,
    find_system_ca_bundle,
)

FEDORA_BUNDLE = "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem"
CURL_CA_ERROR = "CURL error: Problem with the SSL CA cert (path? access rights?)"

DEBIAN = {LEMOND_CA_FILE}
FEDORA_44 = {FEDORA_BUNDLE}
NONE: set = set()


def _fs(files):
    return lambda path: path in files


def test_debian_layout_has_no_problem():
    assert ca_store_problem(platform="linux", exists=_fs(DEBIAN)) is None


def test_fedora_44_layout_names_the_missing_file_and_the_update():
    problem = ca_store_problem(platform="linux", exists=_fs(FEDORA_44))
    assert problem is not None
    assert LEMOND_CA_FILE in problem
    assert FEDORA_BUNDLE in problem
    assert "ca-certificates" in problem
    assert "restart GAIA" in problem


def test_no_bundle_anywhere_names_the_package_to_install():
    problem = ca_store_problem(platform="linux", exists=_fs(NONE))
    assert problem is not None
    assert "no system certificate store" in problem
    assert "Install the ca-certificates package" in problem


def test_remedies_name_no_command_line():
    for files in (FEDORA_44, NONE):
        problem = ca_store_problem(platform="linux", exists=_fs(files))
        assert "`" not in problem and "sudo" not in problem


def test_the_text_fits_a_lemonade_the_user_installed_too():
    # Download errors get this text whichever Lemonade is in use.
    for files in (FEDORA_44, NONE):
        problem = ca_store_problem(platform="linux", exists=_fs(files))
        assert "GAIA's model server" not in problem


def test_other_platforms_are_not_judged():
    for platform in ("win32", "darwin"):
        assert ca_store_problem(platform=platform, exists=_fs(NONE)) is None


def test_probe_order_prefers_the_debian_path():
    assert find_system_ca_bundle(_fs({FEDORA_BUNDLE, LEMOND_CA_FILE})) == (
        LEMOND_CA_FILE
    )
    assert find_system_ca_bundle(_fs(FEDORA_44)) == FEDORA_BUNDLE
    assert find_system_ca_bundle(_fs(NONE)) is None


def test_only_a_ca_failure_is_explained():
    probe = {"platform": "linux", "exists": _fs(FEDORA_44)}
    other = "CURL error: Couldn't resolve host name"
    assert explain_tls_failure(other, **probe) == other
    explained = explain_tls_failure(CURL_CA_ERROR, **probe)
    assert explained.startswith("This PC's certificate store has no")
    assert explained.endswith(f"(Lemonade said: {CURL_CA_ERROR})")


def test_a_ca_failure_on_a_good_store_is_left_alone():
    probe = {"platform": "linux", "exists": _fs(DEBIAN)}
    assert explain_tls_failure(CURL_CA_ERROR, **probe) == CURL_CA_ERROR


@responses.activate
def test_a_download_that_hits_the_ca_error_says_why(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path))
    monkeypatch.setattr(
        lemonade_tls,
        "ca_store_problem",
        lambda **_: ca_store_problem(platform="linux", exists=_fs(FEDORA_44)),
    )
    client = LemonadeClient(host="127.0.0.1", port=1, verbose=False)
    monkeypatch.setattr(client, "ensure_npu_backend", lambda _model: None)
    responses.add(
        responses.POST,
        f"{client.base_url}/pull",
        body=f'event: error\ndata: {{"error":"{CURL_CA_ERROR}"}}\n\n',
        status=200,
        content_type="text/event-stream",
    )
    events = []
    try:
        for event in client.pull_model_stream("nomic-embed-text-v1-GGUF"):
            events.append(event)
    except LemonadeClientError as e:
        raised = str(e)
    else:
        raise AssertionError("the pull should have failed")
    assert LEMOND_CA_FILE in events[-1]["error"]
    assert LEMOND_CA_FILE in raised and CURL_CA_ERROR in raised
