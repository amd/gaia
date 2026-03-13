# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
ServiceIntegrationMixin: API discovery, credential management, and preference learning.

Provides:
- discover_api(service) — Search web for API docs, determine auth type
- setup_integration(service, credential_data) — Store credentials + create API skill
- store_credential / get_credential / refresh_credential / list_credentials
- Preference learning: explicit corrections and implicit confirmations
- Decision workflow executor: observe → recall → apply rules → fallback

Usage:
    class MyAgent(Agent, MemoryMixin, ServiceIntegrationMixin):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.init_memory()

        def _register_tools(self):
            self.register_memory_tools()
            self.register_service_integration_tools()

Credential encryption uses stdlib-only XOR + base64 with a machine-derived key.
This is lightweight obfuscation (not production crypto) — sufficient to prevent
accidental plaintext exposure in DB files.
"""

import base64
import hashlib
import json
import logging
import platform
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Import web search function for API discovery
from gaia.agents.tools.web_search import _call_perplexity_api

# ============================================================================
# Credential Encryption (stdlib only)
# ============================================================================


def _get_encryption_key() -> bytes:
    """Derive a machine-specific encryption key.

    Uses platform info (hostname + OS) hashed with SHA-256.
    This is lightweight obfuscation, not production-grade crypto.
    """
    machine_info = f"{platform.node()}-{platform.system()}-gaia-credential-key-v1"
    return hashlib.sha256(machine_info.encode("utf-8")).digest()


def _encrypt_data(plaintext: str) -> str:
    """Encrypt a string using XOR with machine-derived key, then base64 encode.

    Args:
        plaintext: The string to encrypt.

    Returns:
        Base64-encoded encrypted string.
    """
    key = _get_encryption_key()
    data_bytes = plaintext.encode("utf-8")
    encrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(data_bytes))
    return base64.b64encode(encrypted).decode("ascii")


def _decrypt_data(encrypted: str) -> str:
    """Decrypt a base64-encoded XOR-encrypted string.

    Args:
        encrypted: Base64-encoded encrypted string.

    Returns:
        Decrypted plaintext string.
    """
    key = _get_encryption_key()
    encrypted_bytes = base64.b64decode(encrypted)
    decrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(encrypted_bytes))
    return decrypted.decode("utf-8")


# ============================================================================
# API Discovery Helpers
# ============================================================================

# Auth type detection patterns (applied to web search results)
_AUTH_PATTERNS = [
    (re.compile(r"oauth\s*2\.?0?", re.IGNORECASE), "oauth2"),
    (re.compile(r"oauth", re.IGNORECASE), "oauth2"),
    (re.compile(r"api[_\s]?key", re.IGNORECASE), "api_key"),
    (re.compile(r"bearer[_\s]?token", re.IGNORECASE), "bearer_token"),
    (re.compile(r"basic[_\s]?auth", re.IGNORECASE), "basic_auth"),
    (re.compile(r"jwt", re.IGNORECASE), "jwt"),
]

# Patterns indicating no API exists
_NO_API_PATTERNS = [
    re.compile(r"does not have.{0,30}(?:public|rest|api)", re.IGNORECASE),
    re.compile(r"no (?:public |official )?api", re.IGNORECASE),
    re.compile(r"not (?:provide|offer|have).{0,20}api", re.IGNORECASE),
    re.compile(r"no developer.{0,20}(?:documentation|docs)", re.IGNORECASE),
    re.compile(r"through the (?:browser|web) interface", re.IGNORECASE),
]

# Patterns indicating a service HAS an API (pre-compiled for performance)
_HAS_API_PATTERNS = [
    re.compile(r"(?:rest|graphql|grpc)\s*api", re.IGNORECASE),
    re.compile(r"api\s*(?:documentation|docs|reference|endpoint)", re.IGNORECASE),
    re.compile(r"developer\s*(?:documentation|portal|console)", re.IGNORECASE),
    re.compile(r"(?:has|provides|offers)\s+(?:a\s+)?(?:public\s+)?api", re.IGNORECASE),
    re.compile(r"https?://\S*(?:api|developer)", re.IGNORECASE),
]

_API_WORD_PATTERN = re.compile(r"\bapi\b", re.IGNORECASE)


def _detect_auth_type(text: str) -> str:
    """Detect authentication type from text describing an API.

    Args:
        text: Text to analyze (typically from web search results).

    Returns:
        Auth type string: oauth2, api_key, bearer_token, basic_auth, jwt, or "unknown".
    """
    for pattern, auth_type in _AUTH_PATTERNS:
        if pattern.search(text):
            return auth_type
    return "unknown"


def _detect_has_api(text: str) -> bool:
    """Detect whether the text indicates a service has a public API.

    Returns True if text appears to describe an API, False if it indicates
    no API exists.
    """
    # Check negative patterns first
    for pattern in _NO_API_PATTERNS:
        if pattern.search(text):
            return False

    # Check positive indicators
    for pattern in _HAS_API_PATTERNS:
        if pattern.search(text):
            return True

    # Default: if text mentions the word "API" at all, assume it exists
    if _API_WORD_PATTERN.search(text):
        return True

    return False


def _extract_setup_steps(text: str) -> List[str]:
    """Extract setup steps from API documentation text.

    Looks for numbered steps, bullet points, or sequential instructions.
    Falls back to generic steps if none found.
    """
    steps = []

    # Try to find numbered steps (e.g., "1. Do this", "Step 1:", etc.)
    numbered = re.findall(
        r"(?:^|\n)\s*(?:\d+[\.\)]\s*|step\s*\d+[:\s]+)(.+?)(?:\n|$)",
        text,
        re.IGNORECASE | re.MULTILINE,
    )
    if numbered:
        steps = [s.strip() for s in numbered if len(s.strip()) > 10]

    if not steps:
        # Try bullet points
        bullets = re.findall(
            r"(?:^|\n)\s*[-*]\s+(.+?)(?:\n|$)",
            text,
            re.MULTILINE,
        )
        if bullets:
            steps = [s.strip() for s in bullets if len(s.strip()) > 10]

    if not steps:
        # Generic fallback steps
        steps = [
            "Visit the service's developer portal",
            "Create a developer account/project",
            "Generate API credentials",
            "Configure authentication",
        ]

    return steps[:10]  # Cap at 10 steps


def _extract_documentation_url(text: str, sources: List[str]) -> str:
    """Extract the most relevant documentation URL.

    Prefers sources list, falls back to URL extraction from text.
    """
    # Prefer sources from web search
    if sources:
        # Look for developer/API docs URLs
        for src in sources:
            if any(
                kw in src.lower()
                for kw in ["developer", "api", "docs", "documentation"]
            ):
                return src
        return sources[0]

    # Extract URLs from text
    urls = re.findall(r"https?://[^\s\)\"']+", text)
    for url in urls:
        if any(kw in url.lower() for kw in ["developer", "api", "docs"]):
            return url
    if urls:
        return urls[0]

    return ""


# ============================================================================
# ServiceIntegrationMixin
# ============================================================================


class ServiceIntegrationMixin:
    """Service integration tools for any GAIA agent.

    Provides tools for:
    - API discovery via web search
    - Credential management (encrypted storage)
    - Service integration setup
    - Preference learning (explicit corrections + implicit confirmations)
    - Decision workflow execution

    Requires MemoryMixin to be initialized first (needs .knowledge property).

    Tool registration follows GAIA pattern: register_service_integration_tools() method.
    """

    def register_service_integration_tools(self) -> None:
        """Register service integration tools with the agent's tool registry.

        Call this from _register_tools() in your agent subclass.
        Tools registered:
        - discover_api: Search for API documentation for a service
        - setup_integration: Store credentials and create API skill
        - store_credential: Encrypt and store credentials
        - get_credential: Retrieve and decrypt credentials
        - refresh_credential: Refresh OAuth2 tokens
        - list_credentials: List credentials (no secrets)
        """
        from gaia.agents.base.tools import tool

        mixin = self  # Capture self for nested functions

        # ================================================================
        # discover_api tool
        # ================================================================

        @tool(atomic=True)
        def discover_api(service: str) -> Dict:
            """Search for API documentation and setup instructions for a service.

            Uses web search to find whether a service has a public API,
            what authentication it requires, and how to set it up.

            Args:
                service: Service name (e.g., "gmail", "twitter", "linkedin",
                         "slack", "notion", "github")

            Returns:
                Dictionary with:
                - has_api: Whether a public API was found
                - auth_type: Authentication type (oauth2, api_key, bearer_token, etc.)
                - setup_steps: List of setup step descriptions
                - documentation_url: URL to API documentation
                - fallback: "computer_use" if no API found

            Example:
                result = discover_api("gmail")
                if result["has_api"]:
                    print(f"Auth: {result['auth_type']}")
                    for step in result["setup_steps"]:
                        print(f"  - {step}")
            """
            logger.info("[ServiceIntegration] discover_api called for: %s", service)

            try:
                # Search for API documentation
                query = f"{service} API documentation setup authentication developer"
                search_result = _call_perplexity_api(query)

                if not search_result.get("success"):
                    logger.warning(
                        "[ServiceIntegration] web search failed for %s: %s",
                        service,
                        search_result.get("error", "unknown"),
                    )
                    return {
                        "has_api": False,
                        "auth_type": "unknown",
                        "setup_steps": [],
                        "documentation_url": "",
                        "fallback": "computer_use",
                        "error": search_result.get("error", "Web search failed"),
                    }

                answer = search_result.get("answer", "")
                sources = search_result.get("sources", [])

                has_api = _detect_has_api(answer)

                if not has_api:
                    logger.info(
                        "[ServiceIntegration] no API found for %s, suggesting computer_use",
                        service,
                    )
                    return {
                        "has_api": False,
                        "auth_type": "unknown",
                        "setup_steps": [],
                        "documentation_url": "",
                        "fallback": "computer_use",
                        "message": (
                            f"No public API found for {service}. "
                            "Consider using browser automation (computer_use) as a fallback."
                        ),
                    }

                auth_type = _detect_auth_type(answer)
                setup_steps = _extract_setup_steps(answer)
                doc_url = _extract_documentation_url(answer, sources)

                logger.info(
                    "[ServiceIntegration] API found for %s: auth=%s, steps=%d",
                    service,
                    auth_type,
                    len(setup_steps),
                )

                return {
                    "has_api": True,
                    "auth_type": auth_type,
                    "setup_steps": setup_steps,
                    "documentation_url": doc_url,
                    "service": service,
                    "raw_answer": answer[:500],  # Truncated for context
                }

            except Exception as e:
                logger.error(
                    "[ServiceIntegration] discover_api error: %s", e, exc_info=True
                )
                return {
                    "has_api": False,
                    "auth_type": "unknown",
                    "setup_steps": [],
                    "documentation_url": "",
                    "fallback": "computer_use",
                    "error": str(e),
                }

        # ================================================================
        # setup_integration tool
        # ================================================================

        @tool(atomic=True)
        def setup_integration(service: str, credential_data: str) -> Dict:
            """Store API credentials and create an API skill for a service.

            Validates the credential data, encrypts and stores it, then creates
            an API skill insight in KnowledgeDB that references the credential.

            Args:
                service: Service name (e.g., "gmail", "twitter")
                credential_data: JSON string containing:
                    - credential_type (required): "oauth2", "api_key", "bearer_token"
                    - Plus type-specific fields (access_token, api_key, etc.)
                    - capabilities (optional): list of API capabilities
                    - scopes (optional): list of permission scopes

            Returns:
                Dictionary with status, credential_id, skill_id on success.

            Example:
                result = setup_integration("gmail", '{"credential_type": "oauth2", ...}')
            """
            logger.info("[ServiceIntegration] setup_integration for: %s", service)

            # Parse credential data
            try:
                cred_dict = json.loads(credential_data)
            except (json.JSONDecodeError, TypeError) as e:
                return {
                    "status": "error",
                    "message": f"Invalid JSON in credential_data: {e}",
                }

            # Validate required fields
            if "credential_type" not in cred_dict:
                return {
                    "status": "error",
                    "message": "Missing required field 'credential_type' in credential_data.",
                }

            credential_type = cred_dict["credential_type"]
            scopes = cred_dict.pop("scopes", None)
            capabilities = cred_dict.pop("capabilities", [])

            # Encrypt the credential data
            encrypted = _encrypt_data(json.dumps(cred_dict))
            credential_id = f"cred_{service}_{credential_type}"

            try:
                # Store credential
                mixin.knowledge.store_credential(
                    credential_id=credential_id,
                    service=service,
                    credential_type=credential_type,
                    encrypted_data=encrypted,
                    scopes=scopes,
                )

                # Create API skill insight
                skill_id = mixin.knowledge.store_insight(
                    category="skill",
                    domain=service,
                    content=f"{service} API integration ({credential_type})",
                    metadata={
                        "type": "api",
                        "provider": service,
                        "credential_id": credential_id,
                        "capabilities": capabilities,
                        "credential_type": credential_type,
                    },
                    triggers=[service, "api", credential_type],
                )

                logger.info(
                    "[ServiceIntegration] integration set up: service=%s cred=%s skill=%s",
                    service,
                    credential_id,
                    skill_id,
                )

                return {
                    "status": "success",
                    "credential_id": credential_id,
                    "skill_id": skill_id,
                    "service": service,
                    "message": f"Successfully set up {service} integration with {credential_type} authentication.",
                }

            except Exception as e:
                logger.error(
                    "[ServiceIntegration] setup_integration error: %s", e, exc_info=True
                )
                return {"status": "error", "message": str(e)}

        # ================================================================
        # store_credential tool
        # ================================================================

        @tool(atomic=True)
        def store_credential(
            service: str,
            credential_type: str,
            data: str,
            scopes: str = "",
            expires_at: str = "",
        ) -> Dict:
            """Encrypt and store credentials for a service.

            Args:
                service: Service name (e.g., "gmail", "aws", "twitter")
                credential_type: Type of credential ("oauth2", "api_key", "bearer_token")
                data: JSON string of credential data (tokens, keys, etc.)
                scopes: Comma-separated permission scopes (optional)
                expires_at: ISO format expiry timestamp (optional)

            Returns:
                Dictionary with status and credential_id.

            Example:
                store_credential("gmail", "oauth2",
                    '{"access_token": "...", "refresh_token": "..."}',
                    scopes="gmail.modify,gmail.compose",
                    expires_at="2026-04-01T00:00:00")
            """
            logger.info(
                "[ServiceIntegration] store_credential for: %s (%s)",
                service,
                credential_type,
            )

            # Parse data JSON
            try:
                json.loads(data)  # Validate it's valid JSON
            except (json.JSONDecodeError, TypeError) as e:
                return {"status": "error", "message": f"Invalid JSON in data: {e}"}

            # Encrypt the data
            encrypted = _encrypt_data(data)
            credential_id = f"cred_{service}_{credential_type}"

            # Parse scopes
            scope_list = (
                [s.strip() for s in scopes.split(",") if s.strip()] if scopes else None
            )

            try:
                mixin.knowledge.store_credential(
                    credential_id=credential_id,
                    service=service,
                    credential_type=credential_type,
                    encrypted_data=encrypted,
                    scopes=scope_list,
                    expires_at=expires_at or None,
                )

                logger.info("[ServiceIntegration] credential stored: %s", credential_id)
                return {
                    "status": "stored",
                    "credential_id": credential_id,
                    "service": service,
                    "credential_type": credential_type,
                }

            except Exception as e:
                logger.error(
                    "[ServiceIntegration] store_credential error: %s", e, exc_info=True
                )
                return {"status": "error", "message": str(e)}

        # ================================================================
        # get_credential tool
        # ================================================================

        @tool(atomic=True)
        def get_credential(service: str) -> Dict:
            """Retrieve credentials for a service. Warns if expired.

            Decrypts the stored credential data for use.

            Args:
                service: Service name (e.g., "gmail", "aws")

            Returns:
                Dictionary with status, data (decrypted), expired flag.

            Example:
                result = get_credential("gmail")
                if result["status"] == "found" and not result["expired"]:
                    token = result["data"]["access_token"]
            """
            logger.info("[ServiceIntegration] get_credential for: %s", service)

            try:
                cred = mixin.knowledge.get_credential(service)
                if cred is None:
                    return {
                        "status": "not_found",
                        "message": f"No credentials found for service '{service}'.",
                    }

                # Decrypt the data
                try:
                    decrypted_json = _decrypt_data(cred["encrypted_data"])
                    decrypted_data = json.loads(decrypted_json)
                except Exception as e:
                    logger.error(
                        "[ServiceIntegration] credential decryption failed: %s", e
                    )
                    return {
                        "status": "error",
                        "message": f"Failed to decrypt credentials: {e}",
                    }

                expired = cred.get("expired", False)
                if expired:
                    logger.warning(
                        "[ServiceIntegration] credential expired for %s", service
                    )

                return {
                    "status": "found",
                    "service": cred["service"],
                    "credential_type": cred["credential_type"],
                    "data": decrypted_data,
                    "expired": expired,
                    "expires_at": cred.get("expires_at"),
                    "scopes": cred.get("scopes"),
                    "last_refreshed": cred.get("last_refreshed"),
                }

            except Exception as e:
                logger.error(
                    "[ServiceIntegration] get_credential error: %s", e, exc_info=True
                )
                return {"status": "error", "message": str(e)}

        # ================================================================
        # refresh_credential tool
        # ================================================================

        @tool(atomic=True)
        def refresh_credential(service: str) -> Dict:
            """Refresh OAuth2 tokens for a service. Updates stored credential.

            Retrieves the current credential, uses the refresh_token to get a
            new access_token, and updates the stored credential.

            Args:
                service: Service name (e.g., "gmail")

            Returns:
                Dictionary with status and updated expiry.

            Example:
                result = refresh_credential("gmail")
                if result["status"] == "refreshed":
                    print("Token refreshed successfully")
            """
            import requests

            logger.info("[ServiceIntegration] refresh_credential for: %s", service)

            try:
                cred = mixin.knowledge.get_credential(service)
                if cred is None:
                    return {
                        "status": "error",
                        "message": f"No credentials found for service '{service}'.",
                    }

                if cred["credential_type"] != "oauth2":
                    return {
                        "status": "error",
                        "message": f"Refresh only supported for oauth2 credentials, got '{cred['credential_type']}'.",
                    }

                # Decrypt current data
                try:
                    current_data = json.loads(_decrypt_data(cred["encrypted_data"]))
                except Exception as e:
                    return {
                        "status": "error",
                        "message": f"Failed to decrypt current credentials: {e}",
                    }

                # Validate refresh token fields exist
                required = ["refresh_token", "token_uri", "client_id", "client_secret"]
                missing = [f for f in required if f not in current_data]
                if missing:
                    return {
                        "status": "error",
                        "message": f"Missing fields for refresh: {missing}",
                    }

                # Make OAuth2 refresh request
                resp = requests.post(
                    current_data["token_uri"],
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": current_data["refresh_token"],
                        "client_id": current_data["client_id"],
                        "client_secret": current_data["client_secret"],
                    },
                    timeout=30,
                )

                if resp.status_code != 200:
                    return {
                        "status": "error",
                        "message": f"Token refresh failed: HTTP {resp.status_code}",
                    }

                token_data = resp.json()
                new_access_token = token_data.get("access_token")
                expires_in = token_data.get("expires_in", 3600)

                if not new_access_token:
                    return {
                        "status": "error",
                        "message": "Token refresh response missing access_token.",
                    }

                # Update credential data
                current_data["access_token"] = new_access_token
                if "refresh_token" in token_data:
                    current_data["refresh_token"] = token_data["refresh_token"]

                # Calculate new expiry
                new_expires_at = (
                    datetime.now() + timedelta(seconds=expires_in)
                ).isoformat()

                # Re-encrypt and update
                new_encrypted = _encrypt_data(json.dumps(current_data))
                mixin.knowledge.update_credential(
                    credential_id=cred["id"],
                    encrypted_data=new_encrypted,
                    expires_at=new_expires_at,
                )

                logger.info(
                    "[ServiceIntegration] credential refreshed for %s, expires=%s",
                    service,
                    new_expires_at,
                )

                return {
                    "status": "refreshed",
                    "service": service,
                    "expires_at": new_expires_at,
                    "message": f"Successfully refreshed {service} credentials.",
                }

            except Exception as e:
                logger.error(
                    "[ServiceIntegration] refresh_credential error: %s",
                    e,
                    exc_info=True,
                )
                return {"status": "error", "message": str(e)}

        # ================================================================
        # list_credentials tool
        # ================================================================

        @tool(atomic=True)
        def list_credentials() -> Dict:
            """List all stored credentials (service names and types only, no secrets).

            Returns a summary of all stored credentials without exposing
            any sensitive data like tokens or keys.

            Returns:
                Dictionary with status and list of credential summaries.

            Example:
                result = list_credentials()
                for cred in result["credentials"]:
                    print(f"{cred['service']}: {cred['credential_type']}")
            """
            logger.info("[ServiceIntegration] list_credentials called")

            try:
                # Query credentials table directly for summary info
                with mixin.knowledge.lock:
                    cursor = mixin.knowledge.conn.execute("""
                        SELECT id, service, credential_type, scopes,
                               created_at, expires_at, last_used, last_refreshed
                        FROM credentials
                        ORDER BY created_at DESC
                        """)
                    rows = cursor.fetchall()

                credentials = []
                for row in rows:
                    expired = False
                    if row[5]:  # expires_at
                        try:
                            expires_dt = datetime.fromisoformat(row[5])
                            if expires_dt < datetime.now():
                                expired = True
                        except (ValueError, TypeError):
                            pass

                    credentials.append(
                        {
                            "id": row[0],
                            "service": row[1],
                            "credential_type": row[2],
                            "scopes": json.loads(row[3]) if row[3] else None,
                            "created_at": row[4],
                            "expires_at": row[5],
                            "last_used": row[6],
                            "last_refreshed": row[7],
                            "expired": expired,
                        }
                    )

                return {
                    "status": "success",
                    "count": len(credentials),
                    "credentials": credentials,
                }

            except Exception as e:
                logger.error(
                    "[ServiceIntegration] list_credentials error: %s",
                    e,
                    exc_info=True,
                )
                return {"status": "error", "message": str(e), "credentials": []}

        logger.info("[ServiceIntegration] registered 6 service integration tools")

    # ------------------------------------------------------------------
    # Preference Learning Helpers
    # ------------------------------------------------------------------

    def _handle_explicit_correction(
        self,
        original_action: str,
        corrected_action: str,
        context: Dict[str, Any],
    ) -> None:
        """Handle user explicitly correcting an agent decision.

        Stores/updates a preference rule with high confidence.

        Args:
            original_action: The action the agent took (e.g., "archive").
            corrected_action: The action the user wants (e.g., "star").
            context: Dict with:
                - domain: e.g., "email"
                - entity: e.g., "boss@company.com"
                - rule_description: Human-readable rule
        """
        domain = context.get("domain", "general")
        entity = context.get("entity", "")
        rule_desc = context.get(
            "rule_description",
            f"When {entity}: use '{corrected_action}' instead of '{original_action}'",
        )

        content = (
            f"Correction: {entity} → '{corrected_action}' "
            f"(was '{original_action}'). {rule_desc}"
        )

        logger.info(
            "[ServiceIntegration] explicit correction: %s → %s for %s",
            original_action,
            corrected_action,
            entity,
        )

        # Store as a high-confidence strategy insight
        # KnowledgeDB's dedup will merge if similar rule already exists
        self.knowledge.store_insight(
            category="strategy",
            domain=domain,
            content=content,
            triggers=(
                [domain, entity, corrected_action]
                if entity
                else [domain, corrected_action]
            ),
            confidence=0.95,
        )

    def _handle_implicit_confirmation(
        self,
        action: str,
        context: Dict[str, Any],
    ) -> None:
        """Handle implicit confirmation (user didn't correct the decision).

        Bumps the confidence of the driving rule by 0.05 (capped at 1.0).

        Args:
            action: The action that was taken (uncorrected).
            context: Dict with:
                - domain: e.g., "email"
                - rule_id: ID of the insight/rule that drove this decision
        """
        rule_id = context.get("rule_id")
        if not rule_id:
            logger.debug(
                "[ServiceIntegration] implicit confirmation skipped — no rule_id"
            )
            return

        logger.info(
            "[ServiceIntegration] implicit confirmation for action=%s rule=%s",
            action,
            rule_id,
        )

        try:
            with self.knowledge.lock:
                # Get current confidence
                cursor = self.knowledge.conn.execute(
                    "SELECT confidence FROM insights WHERE id = ?", (rule_id,)
                )
                row = cursor.fetchone()
                if row is None:
                    logger.warning(
                        "[ServiceIntegration] rule %s not found for confirmation",
                        rule_id,
                    )
                    return

                current_confidence = row[0]
                new_confidence = min(1.0, current_confidence + 0.05)

                # Update confidence and last_used
                now = datetime.now().isoformat()
                self.knowledge.conn.execute(
                    """
                    UPDATE insights SET
                        confidence = ?,
                        last_used = ?,
                        use_count = use_count + 1
                    WHERE id = ?
                    """,
                    (new_confidence, now, rule_id),
                )
                self.knowledge.conn.commit()

            logger.info(
                "[ServiceIntegration] confidence bumped: %s → %.2f (was %.2f)",
                rule_id,
                new_confidence,
                current_confidence,
            )

        except Exception as e:
            logger.error(
                "[ServiceIntegration] implicit confirmation error: %s", e, exc_info=True
            )

    # ------------------------------------------------------------------
    # Decision Workflow Executor
    # ------------------------------------------------------------------

    def _execute_decision_workflow(
        self,
        skill: Dict[str, Any],
        data: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Execute a decision workflow: observe → recall → apply rules → fallback.

        Given a decision skill definition and input data items, this:
        1. Recalls relevant preferences using context_recall queries
        2. For each data item, checks preference_rules for a match
        3. If a rule matches, applies it
        4. If no rule matches, uses a fallback (first available action)
        5. Logs each decision as an event insight

        Args:
            skill: Decision skill metadata dict with keys:
                - observe: {extract: [...], context_recall: [...]}
                - actions: {action_name: {description: ...}, ...}
                - preference_rules: [{rule, match_field, match_value/match_contains, action, confidence}, ...]
            data: List of data items to process (each is a dict).

        Returns:
            Dict with status, decisions list, and recalled_context.
        """
        logger.info(
            "[ServiceIntegration] executing decision workflow on %d items", len(data)
        )

        # 1. Recall context from knowledge base
        recalled_context = []
        observe = skill.get("observe", {})
        context_queries = observe.get("context_recall", [])

        for query in context_queries:
            try:
                results = self.knowledge.recall(query, top_k=5)
                recalled_context.extend(results)
                logger.debug(
                    "[ServiceIntegration] recall '%s' returned %d results",
                    query,
                    len(results),
                )
            except Exception as e:
                logger.warning(
                    "[ServiceIntegration] recall failed for '%s': %s", query, e
                )

        # 2. Get preference rules from skill
        preference_rules = skill.get("preference_rules", [])

        # 3. Get available actions
        actions = skill.get("actions", {})
        action_names = list(actions.keys())
        fallback_action = action_names[0] if action_names else "unknown"

        # 4. Process each data item
        decisions = []
        for item in data:
            decision = self._match_and_decide(item, preference_rules, fallback_action)
            decisions.append(decision)

            # 5. Log decision as event
            try:
                self.knowledge.store_insight(
                    category="event",
                    domain=skill.get("domain", "decision"),
                    content=(
                        f"Decision: {decision['action']} for item "
                        f"(matched_rule={decision['matched_rule']})"
                    ),
                    metadata={
                        "action": decision["action"],
                        "matched_rule": decision["matched_rule"],
                        "item_summary": str(item)[:200],
                    },
                    triggers=["decision", "email"],
                )
            except Exception as e:
                logger.warning("[ServiceIntegration] failed to log decision: %s", e)

        logger.info(
            "[ServiceIntegration] decision workflow complete: %d decisions",
            len(decisions),
        )

        return {
            "status": "success",
            "decisions": decisions,
            "recalled_context": recalled_context,
            "items_processed": len(data),
        }

    def _match_and_decide(
        self,
        item: Dict[str, Any],
        preference_rules: List[Dict[str, Any]],
        fallback_action: str,
    ) -> Dict[str, Any]:
        """Match a data item against preference rules and decide on an action.

        Args:
            item: Data item dict (e.g., email with sender, subject, snippet).
            preference_rules: List of rule dicts with match criteria.
            fallback_action: Action to use if no rule matches.

        Returns:
            Decision dict with item, action, matched_rule, reasoning.
        """
        # Check each rule in priority order (highest confidence first)
        sorted_rules = sorted(
            preference_rules,
            key=lambda r: r.get("confidence", 0),
            reverse=True,
        )

        for rule in sorted_rules:
            match_field = rule.get("match_field", "")
            match_value = rule.get("match_value", "")
            match_contains = rule.get("match_contains", "")

            if match_field and match_field in item:
                field_value = str(item[match_field]).lower()

                # Exact match
                if match_value and field_value == match_value.lower():
                    return {
                        "item": item,
                        "action": rule["action"],
                        "matched_rule": True,
                        "rule": rule.get("rule", ""),
                        "confidence": rule.get("confidence", 0),
                        "reasoning": f"Matched rule: {rule.get('rule', '')}",
                    }

                # Contains match
                if match_contains and match_contains.lower() in field_value:
                    return {
                        "item": item,
                        "action": rule["action"],
                        "matched_rule": True,
                        "rule": rule.get("rule", ""),
                        "confidence": rule.get("confidence", 0),
                        "reasoning": f"Matched rule: {rule.get('rule', '')}",
                    }

        # No rule matched — use fallback
        return {
            "item": item,
            "action": fallback_action,
            "matched_rule": False,
            "rule": "",
            "confidence": 0,
            "reasoning": f"No matching rule found, using fallback action: {fallback_action}",
        }
