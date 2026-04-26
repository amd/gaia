"""
Shared resilience exceptions.

All resilience primitives inherit from these base exceptions so that
consumers can catch `ResilienceError` from a single module.
"""


class ResilienceError(Exception):
    """Base exception for all resilience pattern failures."""
    pass
