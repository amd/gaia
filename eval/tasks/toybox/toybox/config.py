"""Config loading."""
import json


def load_config(path):
    """Read a JSON config."""
    with open(path) as f:
        return json.load(f)
