# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Opt-in, single-tenant HTTP service entrypoint for container runtimes."""

from __future__ import annotations

import asyncio
import os
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryFile
from urllib.parse import urlsplit

from gaia_agent import caller_auth


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(
            f"{name} is required. See docs/deployment/container-service.mdx."
        )
    return value


def _directory(name: str) -> Path:
    path = Path(_required(name))
    if not path.is_absolute() or not path.is_dir():
        raise ValueError(f"{name} must name an existing absolute directory.")
    try:
        with TemporaryFile(dir=path):
            pass
    except OSError as exc:
        raise ValueError(f"{name} must be writable by the service user.") from exc
    return path.resolve()


@dataclass(frozen=True)
class ServiceConfig:
    host: str
    port: int
    workspace: Path
    model: str
    base_url: str | None
    auth: caller_auth.CallerAuthConfig
    cloud_provider: str | None = None
    cloud_url: str | None = None

    @classmethod
    def from_environment(cls) -> "ServiceConfig":
        auth = caller_auth.config_from_environment()
        if not auth.token or not auth.token.strip():
            raise ValueError(
                "Container service requires a bearer token. Set "
                f"{caller_auth.TOKEN_FILE_ENV_VAR} or {caller_auth.TOKEN_ENV_VAR}."
            )
        hosts = frozenset(
            host.strip().lower()
            for host in _required("GAIA_SERVICE_ALLOWED_HOSTS").split(",")
        )
        for host in hosts:
            parsed = urlsplit(f"//{host}")
            if (
                not host
                or parsed.hostname != host
                or parsed.port is not None
                or parsed.username is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
                or "*" in host
                or any(c.isspace() for c in host)
            ):
                raise ValueError(
                    "GAIA_SERVICE_ALLOWED_HOSTS requires exact hostnames, without ports or wildcards."
                )
        workspace = _directory("GAIA_SERVICE_WORKSPACE")
        _directory("HOME")
        base_url = os.environ.get("LEMONADE_BASE_URL", "").strip().rstrip("/") or None
        if base_url is None and os.environ.get("LEMONADE_API_KEY", "").strip():
            raise ValueError(
                "Unset LEMONADE_API_KEY for embedded mode: Lemonade generates its own key. "
                "For an external endpoint, set LEMONADE_BASE_URL too."
            )
        parsed = urlsplit(base_url or "")
        if base_url and (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "LEMONADE_BASE_URL must be an HTTP(S) URL without embedded credentials, query or fragment."
            )
        port = int(os.environ.get("PORT", "8080"))
        if not 1 <= port <= 65535:
            raise ValueError("PORT must be between 1 and 65535.")
        provider = os.environ.get("GAIA_SERVICE_CLOUD_PROVIDER", "").strip() or None
        cloud_url = os.environ.get("GAIA_SERVICE_CLOUD_URL", "").strip() or None
        if bool(provider) != bool(cloud_url):
            raise ValueError(
                "Set both GAIA_SERVICE_CLOUD_PROVIDER and GAIA_SERVICE_CLOUD_URL."
            )
        if provider:
            if base_url:
                raise ValueError(
                    "Cloud bootstrap is only supported with service-owned embedded Lemonade."
                )
            if not re.fullmatch(r"[a-z][a-z0-9_]*", provider):
                raise ValueError(
                    "GAIA_SERVICE_CLOUD_PROVIDER must be a lowercase provider identifier."
                )
            cloud = urlsplit(cloud_url)
            if (
                cloud.scheme != "https"
                or not cloud.hostname
                or cloud.username
                or cloud.password
                or cloud.query
                or cloud.fragment
            ):
                raise ValueError(
                    "GAIA_SERVICE_CLOUD_URL must be HTTPS without embedded credentials, query or fragment."
                )
            _required(f"LEMONADE_{provider.upper()}_API_KEY")
        return cls(
            host=os.environ.get("GAIA_SERVICE_HOST", "0.0.0.0"),
            port=port,
            workspace=workspace,
            model=_required("GAIA_SERVICE_MODEL"),
            base_url=base_url,
            auth=replace(auth, allowed_hosts=hosts, allowed_origin_hosts=frozenset()),
            cloud_provider=provider,
            cloud_url=cloud_url,
        )


def _configure_cloud(config: ServiceConfig, base_url: str) -> None:
    import requests

    from gaia.llm.lemonade_client import lemonade_auth_headers, resolve_lemonade_api_key

    response = requests.post(
        f"{base_url.rstrip('/')}/install",
        headers=lemonade_auth_headers(resolve_lemonade_api_key(base_url=base_url)),
        json={
            "backend": "cloud",
            "provider": config.cloud_provider,
            "base_url": config.cloud_url,
        },
        timeout=60,
    )
    if not response.ok:
        raise RuntimeError(
            f"Embedded Lemonade cloud setup failed (HTTP {response.status_code}). Check provider URL and credentials."
        )
    if response.json().get("status") != "success":
        raise RuntimeError(
            "Embedded Lemonade did not confirm cloud provider installation."
        )


def create_app(config: ServiceConfig):
    from gaia_agent import server
    from starlette.responses import JSONResponse

    app = server.build_app(
        auth_config=config.auth,
        agent_config={
            "model_id": config.model,
            "base_url": config.base_url,
            "allowed_paths": [str(config.workspace)],
            "project_root": str(config.workspace),
        },
        warmup=False,
    )

    if config.base_url is None:
        original_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def embedded_lifespan(app):
            from gaia.llm.lemonade_embedded import EmbeddedLemonade

            manager = EmbeddedLemonade()
            bundle = os.environ.get("GAIA_SERVICE_LEMONADE_BUNDLE")
            if bundle and not manager.is_installed():
                source = Path(bundle) / "lemonade" / "dist" / manager.version
                if not (source / "lemond").is_file():
                    raise ValueError(
                        "GAIA_SERVICE_LEMONADE_BUNDLE has no matching lemond binary."
                    )
                manager.dist_dir.parent.mkdir(parents=True, exist_ok=True)
                manager.dist_dir.symlink_to(source, target_is_directory=True)
            existing = await asyncio.to_thread(manager.status)
            if existing.running or existing.unresponsive_pid:
                raise RuntimeError(
                    "Service requires exclusive ownership of embedded Lemonade. Stop the existing instance first."
                )
            status = None
            try:
                startup = asyncio.create_task(
                    asyncio.to_thread(
                        manager.start, install_if_missing=False, reuse_existing=False
                    )
                )
                try:
                    status = await asyncio.shield(startup)
                except asyncio.CancelledError:
                    # Cancelling an await cannot stop the startup thread. Recover
                    # its owned PID before the finally block tears it down.
                    status = await startup
                    raise
                app.state.agent_config["base_url"] = status.base_url
                if config.cloud_provider:
                    await asyncio.to_thread(_configure_cloud, config, status.base_url)
                async with original_lifespan(app):
                    yield
            finally:
                if status is not None:
                    await asyncio.to_thread(manager.stop, expected_pid=status.pid)

        app.router.lifespan_context = embedded_lifespan

    @app.get("/ready")
    async def ready():
        probe = await asyncio.to_thread(
            server._probe_lemonade,
            model_id=config.model,
            base_url=app.state.agent_config["base_url"],
        )
        compatible = server._version_meets_min(
            probe["version"], server.MIN_LEMONADE_VERSION
        )
        available = probe["reachable"] and probe["present"] and compatible is not False
        return JSONResponse(
            {"ready": bool(available)}, status_code=200 if available else 503
        )

    return app


def main() -> None:
    import uvicorn

    config = ServiceConfig.from_environment()
    os.chdir(config.workspace)
    uvicorn.run(
        create_app(config),
        host=config.host,
        port=config.port,
        workers=1,
        proxy_headers=False,
        timeout_graceful_shutdown=15,
    )


if __name__ == "__main__":
    main()
