"""Config loading."""
import json


def load_config(path):
    """Read a JSON config."""
    with open(path) as f:
        # BUG: an existing but EMPTY file raises JSONDecodeError, uncaught.
        return json.load(f)
