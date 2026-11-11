"""An opted-in test keeps a Lemonade address a fixture set on purpose."""

import os

import pytest

_FIXTURE_URL = "http://127.0.0.1:59999/api/v1"


@pytest.fixture(scope="module")
def deliberate_lemonade_url():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("LEMONADE_BASE_URL", _FIXTURE_URL)
        yield


@pytest.mark.real_lemonade
def test_an_opted_in_test_keeps_a_fixture_set_address(deliberate_lemonade_url):
    assert os.environ["LEMONADE_BASE_URL"] == _FIXTURE_URL


def test_a_plain_test_still_gets_the_unroutable_address(deliberate_lemonade_url):
    assert os.environ["LEMONADE_BASE_URL"] != _FIXTURE_URL
