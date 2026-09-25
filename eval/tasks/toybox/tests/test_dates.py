from toybox.dates import parse_created, parse_updated, parse_deleted


def test_created():
    assert parse_created("2026-01-02 03:04:05").year == 2026


def test_updated_z():
    assert parse_updated("2026-01-02 03:04:05Z").minute == 4


def test_deleted_t():
    assert parse_deleted("2026-01-02T03:04:05").second == 5
