# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What a credential looks like in text, and how to mask it.

One copy of the token shapes and URL patterns that failure records, memory
and tool output all mask, so a shape added here is masked everywhere.
"""

import re
from typing import Any, Tuple

REDACTED = "[redacted]"

#: Tokens a provider issues with a fixed prefix; each pattern spans the token.
#: Compile with ``re.IGNORECASE``.
TOKEN_SHAPES: Tuple[str, ...] = (
    r"\bsk-ant-[a-zA-Z0-9_-]{10,}",  # Anthropic API key
    r"\bsk-[a-zA-Z0-9_-]{20,}",  # OpenAI-style API key
    r"\bgh[pousr]_[a-zA-Z0-9]{20,}",  # GitHub token (ghp_/gho_/ghu_/ghs_/ghr_)
    r"\bgithub_pat_[a-zA-Z0-9_]{20,}",  # GitHub fine-grained PAT
    r"\bAKIA[0-9A-Z]{16}\b",  # AWS access key id
    r"\bAIza[0-9A-Za-z_-]{20,}",  # Google API key
    r"\bxox[baprs]-[a-zA-Z0-9-]{10,}",  # Slack token
    r"\bglpat-[a-zA-Z0-9_-]{20,}",  # GitLab PAT
)

#: The userinfo of a URL (``https://user:token@host``): between ``://`` and
#: the ``@`` before the host. scp-style ``git@host:repo`` and email addresses
#: have no ``://`` and are left alone.
URL_USERINFO = r"(?<=://)[^/\s@?#]+(?=@)"

#: A query parameter whose value is a credential: ``?access_token=…``.
_URL_SECRET_QUERY = re.compile(
    r"([?&](?:access_token|private_token|token|api_key|apikey|key|secret"
    r"|password|passwd|auth|sig|signature)=)[^&\s#\"'<>]+",
    re.IGNORECASE,
)

_TOKENS = tuple(re.compile(p, re.IGNORECASE) for p in TOKEN_SHAPES)
_USERINFO = re.compile(URL_USERINFO)


def redact_credentials(text: str) -> str:
    """*text* with URL userinfo, secret query values and known tokens masked."""
    if not text:
        return text
    text = _USERINFO.sub(REDACTED, text)
    text = _URL_SECRET_QUERY.sub(lambda m: m.group(1) + REDACTED, text)
    for pattern in _TOKENS:
        text = pattern.sub(REDACTED, text)
    return text


def redact_credentials_in(value: Any) -> Any:
    """*value* with :func:`redact_credentials` applied to every string in it.

    Dicts, lists and tuples are rebuilt; anything else is returned unchanged.
    """
    if isinstance(value, str):
        return redact_credentials(value)
    if isinstance(value, dict):
        return {key: redact_credentials_in(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_credentials_in(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_credentials_in(item) for item in value)
    return value
