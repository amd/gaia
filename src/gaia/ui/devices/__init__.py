# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Paired devices: phones and tablets that reach GAIA on the home network.

GAIA serves its API over TLS with its own certificate, which a device pins
when it pairs; the PC shows a single-use invitation as a QR code; and every
device gets a revocable token, stored only as a hash. Hidden behind
``device_pairing`` in ``~/.gaia/config.json`` until it is proven. See
``docs/spec/agent-ui-server.mdx`` for the security model.

Every limit and fixed set of values the feature uses is defined here, once.
"""

from typing import Literal, get_args

#: The listener's port. ``GAIA_DEVICE_PORT`` names another; clients learn it
#: from the pairing code, never hard-code it.
DEVICE_LISTENER_PORT = 4243
DEVICE_PORT_ENV = "GAIA_DEVICE_PORT"
#: The listener binds IPv4 only: an IPv4-mapped peer would dodge the checks.
DEVICE_LISTENER_HOST = "0.0.0.0"

#: The one route the listener serves without a device token, and the one a
#: pending device may still call: unpairing itself.
PAIR_ROUTE = "/api/devices/pair"
SELF_ROUTE = "/api/devices/me"

#: Peers the listener answers, and the subset a pairing code offers.
HOME_NETWORKS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16")
OFFERED_NETWORKS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")

#: How long a pairing code on the PC's screen stays good.
PAIRING_WINDOW_SECONDS = 10 * 60
#: How long a device that paired may wait for Allow before its token dies.
PENDING_DEVICE_SECONDS = 10 * 60
#: A device unused this long is revoked.
DEVICE_IDLE_DAYS = 90

#: Failed pairing attempts one address may make per window before a lockout.
PAIR_FAILURES_ALLOWED = 5
PAIR_FAILURE_WINDOW_SECONDS = 60
PAIR_LOCKOUT_SECONDS = 5 * 60
#: Addresses whose failures are remembered at once; the oldest go first.
PAIR_TRACKED_PEERS = 1024
#: Recent failed pairing attempts the PC can see.
RECENT_FAILURES_KEPT = 20
#: Closed pairing codes remembered, so a replay can be told from a typo.
CLOSED_INVITATIONS_KEPT = 8

#: Why a pairing attempt failed, as the PC sees it: the code timed out, was
#: already used (or its window closed after the first pairing), matches no
#: open window, no window is open, or the address is locked out. The device
#: sees one refusal for the first four, and a 429 for ``rate_limited``.
PairFailureReason = Literal["expired", "used", "wrong", "closed", "rate_limited"]
PAIR_FAILURE_REASONS = get_args(PairFailureReason)
#: Why the listener isn't serving, as the PC sees it.
ListenerErrorCode = Literal[
    "port_in_use", "unsafe_folder", "certificate", "start_failed"
]
LISTENER_ERROR_CODES = get_args(ListenerErrorCode)
#: Why the PC can't show a pairing code: the listener is off, or the PC has
#: no home-network address to put in it.
PairingErrorCode = Literal["listener_off", "no_home_network"]

#: What the PC's windows hear on ``/api/sessions/events`` when a device pairs,
#: and the only actions that notification offers: allowing happens in the
#: device list alone.
DEVICE_PAIRED_EVENT = "device_paired"
DEVICE_PAIRED_ACTIONS = ("review", "deny")
#: And when an address is locked out for failing to pair too often.
SECURITY_ALERT_EVENT = "security_alert"
PAIRING_LOCKOUT_ALERT = "pairing_locked_out"

#: Invitation and token sizes. A token starts with the prefix, so a leaked
#: one is recognizable; only its SHA-256 is stored.
INVITATION_BYTES = 16
DEVICE_TOKEN_BYTES = 32
DEVICE_TOKEN_PREFIX = "gaiad_"
#: A device's last_seen_at is written at most this often.
LAST_SEEN_WRITE_SECONDS = 60

#: Platforms a device may report, and the states a paired device is in.
DevicePlatform = Literal["ios", "ipados", "android"]
DEVICE_PLATFORMS = get_args(DevicePlatform)
DeviceStatus = Literal["pending", "allowed"]
#: Longest device name kept, after control and bidi characters are removed.
DEVICE_NAME_MAX = 64
#: Longest PC name a pairing code carries.
PC_NAME_MAX = 64

#: Largest request body the listener reads, except on upload routes, which
#: keep their own limits.
LISTENER_BODY_LIMIT_BYTES = 8 * 1024 * 1024
LISTENER_UPLOAD_PATHS = frozenset(
    {"/api/files/upload", "/api/files/upload-media", "/api/documents/upload"}
)
#: Connection limits: concurrent requests, idle keep-alive, the TLS
#: handshake, and how long stopping waits for open responses.
LISTENER_MAX_CONCURRENCY = 32
LISTENER_KEEP_ALIVE_SECONDS = 15
LISTENER_HANDSHAKE_SECONDS = 10
LISTENER_SHUTDOWN_SECONDS = 5
#: How often the running listener expires idle and pending devices.
HOUSEKEEPING_SECONDS = 60

#: The certificate: EC P-256, valid this long; its SHA-256 is the pin.
CERTIFICATE_DAYS = 10 * 365
#: The pairing code's format version.
PAIRING_OFFER_VERSION = 1
