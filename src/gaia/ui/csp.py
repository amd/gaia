# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Content-Security-Policy for the Agent UI page the backend serves.

The page runs its own bundled scripts and talks only to the server it came
from. Anything else a page tries to run or connect to is refused, so markup or
a script that reaches the page some other way cannot load code from, or send
data to, another site.

``img-src`` stays open to http(s) because chat answers may embed remote images.
"""

import base64
import hashlib
import re

# Inline scripts only: a ``<script>`` with a ``src`` is covered by 'self'.
_INLINE_SCRIPT = re.compile(
    r"<script\b(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script\s*>", re.DOTALL | re.IGNORECASE
)


def _script_hashes(html: str) -> list:
    return [
        "'sha256-"
        + base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode()
        + "'"
        for body in _INLINE_SCRIPT.findall(html)
    ]


def content_security_policy(html: str) -> str:
    """The policy for *html*, allowing only the inline scripts it contains."""
    scripts = " ".join(["'self'", *_script_hashes(html)])
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src {scripts}",
            # React sets inline style attributes, so styles cannot be hashed.
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob: http: https:",
            "font-src 'self' data:",
            "connect-src 'self'",
            "media-src 'self' data: blob:",
            "worker-src 'self'",
            "manifest-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )
