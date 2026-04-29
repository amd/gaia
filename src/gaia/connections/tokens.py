"""Access token cache and refresh orchestration.

This file provides a minimal TokenManager skeleton. The heavy-lifting
refresh and HTTP code will be implemented in follow-ups; we expose the
`get_access_token` coroutine as the public surface so other modules can be
wired to it incrementally.
"""
import asyncio
from typing import List

from .errors import AuthRequiredError, ConnectionRevokedError


class TokenManager:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._cache = {}  # provider -> {scopes_key: (access_token, expires_at)}

    async def get_access_token(self, provider: str, scopes: List[str], *, agent_id: str | None = None) -> str:
        """Return a fresh access token for the given provider/scopes.

        Raises AuthRequiredError or ConnectionRevokedError as appropriate.
        """
        raise NotImplementedError("Token refresh not implemented yet")


_GLOBAL = TokenManager()


async def get_access_token(provider: str, scopes: List[str], *, agent_id: str | None = None) -> str:
    return await _GLOBAL.get_access_token(provider, scopes, agent_id=agent_id)


__all__ = ["get_access_token", "TokenManager"]
