# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""SWE-bench rows from Hugging Face: the pinned Verified id list, and bounded,
visible retries when the datasets-server answers with a 5xx.

No network: ``requests.get`` and ``time.sleep`` are stubbed.
"""

import hashlib
import json
import re

import pytest
import requests

from gaia.eval.bench import swebench
from gaia.eval.code_retrieval import instances, suites

#: Change only together with swebench_verified_ids.json, from a fresh read of
#: the dataset: every seeded Verified sample is drawn from this list.
VERIFIED_IDS_SHA256 = "fad0fdea4fc2315e9b78cdf80882a32e32393297052e502e0e63c79ad648fb85"
ID_SHAPE = re.compile(r"^[A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+-\d+$")

# ---------------------------------------------------------------------------
# The pinned id list
# ---------------------------------------------------------------------------


def test_the_pinned_id_list_is_the_one_the_samples_were_drawn_from():
    ids = json.loads(swebench.VERIFIED_IDS_FILE.read_text(encoding="utf-8"))
    digest = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
    assert digest == VERIFIED_IDS_SHA256


def test_the_pinned_id_list_is_the_whole_split_sorted_and_unique():
    ids = swebench.all_instance_ids()
    assert len(ids) == swebench.VERIFIED_SIZE == 500
    assert ids == sorted(set(ids))
    assert all(ID_SHAPE.match(i) for i in ids), [
        i for i in ids if not ID_SHAPE.match(i)
    ]


def test_every_id_the_code_names_is_in_the_pinned_list():
    ids = set(swebench.all_instance_ids())
    named = set(swebench.PILOT)
    for suite in suites.SUITES.values():
        named |= set(suite.verified_ids)
    assert named and named <= ids, sorted(named - ids)


def test_listing_the_ids_needs_no_network(monkeypatch):
    def no_network(*_a, **_k):
        raise AssertionError("all_instance_ids went to the network")

    monkeypatch.setattr(requests, "get", no_network)
    assert len(swebench.all_instance_ids()) == 500


@pytest.mark.parametrize(
    "broken",
    [
        lambda ids: ids[:-1],  # one short
        lambda ids: list(reversed(ids)),  # unsorted
        lambda ids: ids[:-1] + [ids[0]],  # a duplicate
    ],
    ids=["short", "unsorted", "duplicate"],
)
def test_a_damaged_id_list_is_refused_not_used(tmp_path, monkeypatch, broken):
    ids = swebench.all_instance_ids()
    path = tmp_path / "ids.json"
    path.write_text(json.dumps(broken(ids)), encoding="utf-8")
    monkeypatch.setattr(swebench, "VERIFIED_IDS_FILE", path)
    with pytest.raises(swebench.SweBenchError, match="restore it from git"):
        swebench.all_instance_ids()


def test_a_seeded_sample_from_the_pinned_list_is_stable():
    first = swebench.sample_ids(20)
    assert first == swebench.sample_ids(20)
    assert set(first) <= set(swebench.all_instance_ids())


# ---------------------------------------------------------------------------
# Retries on the datasets-server
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, status=200, page=None, bad_json=False):
        self.status_code = status
        self.page = page if page is not None else {"rows": [], "num_rows_total": 0}
        self.bad_json = bad_json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Server Error", response=self)

    def json(self):
        if self.bad_json:
            raise requests.exceptions.JSONDecodeError("Expecting value", "<html>", 0)
        return self.page


@pytest.fixture
def server(monkeypatch):
    """Scripted replies, in order; each is a _Resp or an exception to raise."""
    state = {"replies": [], "urls": [], "sleeps": []}

    def get(url, timeout):
        state["urls"].append(url)
        reply = state["replies"].pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(swebench.time, "sleep", state["sleeps"].append)
    return state


PAGE = {"rows": [{"row": {"instance_id": "a__a-1"}}], "num_rows_total": 1}


def test_a_5xx_is_retried_visibly_until_the_server_answers(server, capsys):
    server["replies"] = [_Resp(502), _Resp(503), _Resp(page=PAGE)]
    assert swebench.rows_page("d/x", "test", 100) == PAGE
    assert server["sleeps"] == list(swebench.FETCH_RETRY_DELAYS_S[:2])
    err = capsys.readouterr().err
    assert err.count("[RETRY]") == 2
    assert "d/x test offset 100" in err and "retry 1/3" in err


def test_a_server_that_stays_down_fails_after_a_bounded_number_of_tries(server):
    server["replies"] = [_Resp(502)] * 10
    with pytest.raises(requests.HTTPError, match="502"):
        swebench.rows_page("d/x", "test", 0)
    assert len(server["urls"]) == len(swebench.FETCH_RETRY_DELAYS_S) + 1
    assert server["sleeps"] == list(swebench.FETCH_RETRY_DELAYS_S)


@pytest.mark.parametrize("status", [400, 401, 404, 429])
def test_a_4xx_is_not_retried(server, status):
    server["replies"] = [_Resp(status), _Resp(page=PAGE)]
    with pytest.raises(requests.HTTPError):
        swebench.rows_page("d/x", "test", 0)
    assert len(server["urls"]) == 1 and server["sleeps"] == []


def test_a_dropped_connection_or_timeout_is_retried(server):
    server["replies"] = [
        requests.ConnectionError("reset"),
        requests.Timeout("slow"),
        requests.exceptions.ChunkedEncodingError("reset mid-body"),
        _Resp(page=PAGE),
    ]
    assert swebench.rows_page("d/x", "test", 0) == PAGE
    assert len(server["sleeps"]) == 3


def test_a_certificate_failure_is_not_retried(server):
    server["replies"] = [requests.exceptions.SSLError("bad cert"), _Resp(page=PAGE)]
    with pytest.raises(requests.exceptions.SSLError):
        swebench.rows_page("d/x", "test", 0)
    assert server["sleeps"] == []


def test_a_page_that_is_not_json_is_not_retried(server):
    server["replies"] = [_Resp(bad_json=True), _Resp(page=PAGE)]
    with pytest.raises(ValueError):
        swebench.rows_page("d/x", "test", 0)
    assert server["sleeps"] == []


def test_verified_rows_fail_loudly_when_the_server_stays_down(server):
    server["replies"] = [_Resp(page={**PAGE, "num_rows_total": 2})] + [_Resp(500)] * 4
    with pytest.raises(swebench.SweBenchError) as exc:
        swebench._fetch_with_rest(["a__a-1", "b__b-2"])
    message = str(exc.value)
    assert "offset 1" in message and "500" in message
    assert "retried 3 times" in message


def test_verified_rows_survive_a_transient_5xx(server, monkeypatch):
    monkeypatch.setattr(swebench, "ROWS_PER_PAGE", 1)
    server["replies"] = [
        _Resp(page={"rows": [{"row": {"instance_id": "a__a-1"}}], "num_rows_total": 2}),
        _Resp(502),
        _Resp(page={"rows": [{"row": {"instance_id": "b__b-2"}}], "num_rows_total": 2}),
    ]
    found = swebench._fetch_with_rest(["a__a-1", "b__b-2"])
    assert sorted(found) == ["a__a-1", "b__b-2"]


def test_rebench_rows_fail_loudly_with_no_partial_cache(server, tmp_path):
    server["replies"] = [_Resp(page={**PAGE, "num_rows_total": 2})] + [_Resp(502)] * 4
    with pytest.raises(instances.InstanceError, match="retried 3 times"):
        instances.rebench_rows("2026_01", tmp_path)
    assert not list(tmp_path.rglob("*.json")), "a partial split must not be cached"


def test_rebench_rows_survive_a_transient_5xx(server, tmp_path):
    row = {c: f"v-{c}" for c in instances.REBENCH_COLUMNS}
    server["replies"] = [
        _Resp(500),
        _Resp(page={"rows": [{"row": row}], "num_rows_total": 1}),
    ]
    assert instances.rebench_rows("2026_01", tmp_path) == [row]
    assert "dataset=nebius%2FSWE-rebench-leaderboard" in server["urls"][0]
