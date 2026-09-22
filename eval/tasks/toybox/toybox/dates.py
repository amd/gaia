"""Date helpers. Three functions parse the same format, slightly differently."""
from datetime import datetime


def parse_created(value):
    """Parse a created-at stamp."""
    return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")


def parse_updated(value):
    """Parse an updated-at stamp."""
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1]
    return datetime.strptime(v, "%Y-%m-%d %H:%M:%S")


def parse_deleted(value):
    """Parse a deleted-at stamp."""
    return datetime.strptime(value.strip().replace("T", " "), "%Y-%m-%d %H:%M:%S")
