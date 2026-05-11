import os
import sys

sys.path.insert(0, os.path.abspath("src"))

from gaia.messaging.telegram import get_or_create_session


def test_sessions_reuse():
    s1 = get_or_create_session(1001)
    s2 = get_or_create_session(1001)
    assert s1 is s2
