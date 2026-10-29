# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Loud, actionable error types for daemon-supervised sidecar agents (no
silent fallbacks).

Relocated verbatim from ``gaia.ui.email_sidecar.errors`` (issue #2142, T1) —
``gaia.ui.email_sidecar.errors`` is now a pure re-export shim over this
module so existing importers keep working unchanged.
"""

from __future__ import annotations

from typing import Optional

#: Header carrying a failed agent route's :attr:`SidecarError.kind`.
ERROR_KIND_HEADER = "X-GAIA-Error-Kind"

#: What went wrong, for a client choosing which next step to show a user.
KIND_START_FAILED = "start_failed"
KIND_OUT_OF_DATE = "out_of_date"
KIND_CAPACITY = "capacity"
KIND_MODE_CONFLICT = "mode_conflict"
KIND_NOT_INSTALLED = "not_installed"
KIND_SERVICE_UNREACHABLE = "service_unreachable"
KIND_SERVICE_OUTDATED = "service_outdated"


class SidecarError(Exception):
    """Base for all sidecar-agent failures.

    :attr:`kind` is one of the ``KIND_*`` names, or ``""`` when the failure has
    no specific next step. Subclasses set it; ``kind=`` overrides it per raise.
    """

    kind: str = ""

    def __init__(self, *args, kind: Optional[str] = None):
        super().__init__(*args)
        if kind is not None:
            self.kind = kind


class PlatformError(SidecarError):
    """Unsupported platform, unreadable/invalid lock, or placeholder entry."""


class IntegrityError(SidecarError):
    """A downloaded binary's SHA-256 did not match binaries.lock.json.

    The security boundary: a tampered/truncated download is rejected before it
    can ever be spawned. There is no 'use it anyway' path.
    """


class BinaryNotFoundError(SidecarError):
    """The frozen binary is not present where it was expected."""


class HealthTimeoutError(SidecarError):
    """The sidecar did not report healthy within the health-poll deadline."""

    kind = KIND_START_FAILED


class SidecarSpawnError(SidecarError):
    """The sidecar process could not be launched (dev env missing, port in use)."""

    kind = KIND_START_FAILED


class SidecarInhibitedError(SidecarSpawnError):
    """A spawn was refused because ``GAIA_TEST_INHIBIT_SIDECAR`` held it down.

    Test-only (see :mod:`gaia.daemon.sidecars.registry`): the daemon's normal
    resilience is to auto-spawn-or-attach a stopped agent on the next
    ``ensure`` — good behavior in production, but it means a test that stops
    an agent to verify degraded-state messaging can never observe that state,
    because the very next probe silently heals it. This lets a test hold the
    stopped state in place. A :class:`SidecarSpawnError` subclass so it maps
    to the existing 502 route handling with no new branch.
    """


class RouteNotAvailableError(SidecarError):
    """A UI capability whose REST route does not exist on the sidecar yet."""


class SidecarHTTPError(SidecarError):
    """The sidecar answered with a non-2xx status.

    Carries the status code and the sidecar's own actionable ``detail`` message
    (e.g. ``502 local LLM triage failed: Lemonade not reachable``) so the loud,
    fixable error the sidecar produced is preserved instead of being flattened
    into a generic ``HTTPError``.
    """

    def __init__(
        self, status_code: int, detail: str, *, path: str = "", agent_id: str = ""
    ):
        self.status_code = status_code
        self.detail = detail
        self.path = path
        self.agent_id = agent_id
        where = f" from {path}" if path else ""
        # Defaults to the bare noun rather than "email": since #4161 more than
        # one agent raises this, and a flagship failure that says "email
        # sidecar" sends the reader to the wrong log.
        who = f"{agent_id} sidecar" if agent_id else "sidecar"
        super().__init__(f"{who} returned HTTP {status_code}{where}: {detail}")


class VersionMismatchError(SidecarError):
    """The running sidecar speaks a different MAJOR contract version than expected."""

    kind = KIND_OUT_OF_DATE


class UnknownAgentError(SidecarError):
    """The requested agent_id is not in the daemon's registered specs."""

    kind = KIND_NOT_INSTALLED


class ModeConflictError(SidecarError):
    """The sidecar is running in a different mode than the ensure requested."""

    kind = KIND_MODE_CONFLICT


class DevSrcDirResolutionError(SidecarError):
    """A caller's dev-mode source directory could not be resolved or was invalid.

    Raised client-side (CLI, Agent UI) before any request reaches the daemon —
    an explicit ``--dev-src-dir`` was not absolute, or the caller's cwd is not
    inside a git work tree — and daemon-side when a non-absolute ``dev_src_dir``
    arrives over the wire. Neither case has a silent guess to fall back to.
    """

    kind = KIND_MODE_CONFLICT


class SidecarNotRunningError(SidecarError):
    """The agent is registered but has no running sidecar to talk to."""


class SidecarUnresponsiveError(SidecarError):
    """The sidecar process is alive but no longer answering its own /health.

    Distinct from :class:`SidecarNotRunningError` (no process) and
    :class:`HealthTimeoutError` (never came up at all): the process passed its
    startup handshake and has since stopped serving — a blocked event loop, a
    wedged credential-store read, or a hung dependency. The process being alive
    is why this cannot be detected by ``poll()``, and why the startup health
    check alone reports it as healthy forever.
    """


class CapacityError(SidecarError):
    """Starting another sidecar would exceed the live-sidecar cap."""

    kind = KIND_CAPACITY


class StopFailedError(SidecarError):
    """The sidecar process survived a tree-kill — it is still alive."""


class InstallBusyError(SidecarError):
    """An install for this agent id is already running (one install per id)."""


class AgentTrustRequiredError(SidecarError):
    """A non-verified agent needs an explicit trust opt-in to install.

    The daemon-side mirror of :class:`gaia.hub.installer.TrustRequiredError`,
    so the route layer maps it to 403 without importing ``gaia.hub``. 403 is a
    distinct, actionable status: a client renders a "Trust & Install" prompt
    and retries with ``trusted: true``. There is no bypass — the gate exists
    because installing runs third-party code on the user's machine.
    """


class AgentNeedsNewerCoreError(SidecarError):
    """No requested/published version of the agent runs on this GAIA core.

    The daemon-side mirror of :class:`gaia.hub.installer.CoreTooOldError`. The
    route maps it to 409 before any sidecar is stopped; the message names the
    core version to upgrade to and the command.
    """


class AgentNotInstalledError(SidecarError):
    """The agent has no ``.installed`` sentinel — there is nothing to remove."""


class InstallFailedError(SidecarError):
    """An install/uninstall could not complete (disk, permissions, bad manifest)."""


class HubUnavailableError(SidecarError):
    """The Agent Hub could not be reached and no usable offline cache exists."""
