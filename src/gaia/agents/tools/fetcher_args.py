# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Which words of a ``curl``, ``wget`` or ``Invoke-WebRequest`` line name a destination.

A fetcher's line mixes addresses with file names, request bodies, headers and
counts. ``curl -o 10.1 https://example.com/`` saves to a file called ``10.1``;
``curl -x 10.1:8080 https://example.com/`` connects to it. :func:`destinations`
walks the arguments the way the program does and returns only the words it
connects to: its URL operands and the values of options that name a host.
"""

from __future__ import annotations

import re
from typing import Dict, List

#: The value is somewhere the program connects to.
HOST = "host"
#: The value is anything else: a file, a body, a header, a number.
OTHER = "other"

_CURL_HOST_SHORT = "x"
_CURL_OTHER_SHORT = "AbcCdDeEFHKmoPQrtTuUwXyYz"

_CURL_HOST_LONG = (
    "proxy proxy1.0 preproxy socks4 socks4a socks5 socks5-hostname url doh-url "
    "dns-servers resolve connect-to"
)
_CURL_OTHER_LONG = (
    "abstract-unix-socket alt-svc aws-sigv4 cacert capath cert cert-type ciphers "
    "config connect-timeout continue-at cookie cookie-jar create-file-mode crlfile "
    "curves data data-ascii data-binary data-raw data-urlencode delegation "
    "dns-interface dns-ipv4-addr dns-ipv6-addr dump-header ech egd-file engine "
    "etag-compare etag-save expect100-timeout form form-string ftp-account "
    "ftp-alternative-to-user ftp-method ftp-port ftp-ssl-ccc-mode "
    "happy-eyeballs-timeout-ms haproxy-clientip header hostpubmd5 hostpubsha256 "
    "help hsts interface ip-tos ipfs-gateway json keepalive-cnt keepalive-time key "
    "key-type knownhosts krb libcurl limit-rate local-port login-options mail-auth "
    "mail-from mail-rcpt max-filesize max-redirs max-time netrc-file noproxy "
    "oauth2-bearer output output-dir parallel-max parallel-max-host pass "
    "pinnedpubkey proto proto-default "
    "proto-redir proxy-cacert proxy-capath proxy-cert proxy-cert-type "
    "proxy-ciphers proxy-crlfile proxy-header proxy-key proxy-key-type proxy-pass "
    "proxy-pinnedpubkey proxy-service-name proxy-tls13-ciphers proxy-tlsauthtype "
    "proxy-tlspassword proxy-tlsuser proxy-user pubkey quote random-file range "
    "rate referer request request-target retry retry-delay retry-max-time "
    "sasl-authzid service-name sigalgs socks5-gssapi-service speed-limit "
    "speed-time ssl-sessions stderr telnet-option tftp-blksize time-cond tls-max tls13-ciphers "
    "tlsauthtype tlspassword tlsuser trace trace-ascii trace-config unix-socket "
    "upload-file upload-flags url-query user user-agent variable vlan-priority "
    "write-out"
)
#: Long options that take no value. One whose name starts a value option's name
#: (``--netrc``, ``--ftp-ssl``) must not swallow the URL that follows it.
_CURL_FLAGS = frozenset(
    (
        "anyauth append basic ca-native cert-status compressed compressed-ssh "
        "create-dirs crlf digest disable disable-eprt disable-epsv "
        "disallow-username-in-url doh-cert-status doh-insecure dump-ca-embed fail "
        "fail-early fail-with-body false-start follow form-escape ftp-create-dirs "
        "ftp-pasv ftp-pret ftp-skip-pasv-ip ftp-ssl ftp-ssl-ccc ftp-ssl-control "
        "ftp-ssl-reqd get globoff haproxy-protocol head http0.9 http1.0 http1.1 "
        "http2 http2-prior-knowledge http3 http3-only ignore-content-length include "
        "insecure ipv4 ipv6 junk-session-cookies list-only location "
        "location-trusted mail-rcpt-allowfails manual metalink mptcp negotiate "
        "netrc netrc-optional next no-alpn no-buffer no-clobber no-keepalive no-npn "
        "no-progress-meter no-sessionid ntlm ntlm-wb out-null parallel "
        "parallel-immediate path-as-is post301 post302 post303 progress-bar "
        "proxy-anyauth proxy-basic proxy-ca-native proxy-digest proxy-http2 "
        "proxy-http3 proxy-insecure proxy-negotiate proxy-ntlm "
        "proxy-ssl-allow-beast proxy-ssl-auto-client-cert proxy-tlsv1 proxytunnel "
        "raw remote-header-name remote-name remote-name-all remote-time "
        "remove-on-error retry-all-errors retry-connrefused sasl-ir show-error "
        "show-headers silent skip-existing socks5-basic socks5-gssapi "
        "socks5-gssapi-nec ssl ssl-allow-beast ssl-auto-client-cert ssl-no-revoke "
        "ssl-reqd ssl-revoke-best-effort sslv2 sslv3 styled-output "
        "suppress-connect-headers tcp-fastopen tcp-nodelay tftp-no-options "
        "tls-earlydata tlsv1 tlsv1.0 tlsv1.1 tlsv1.2 tlsv1.3 tr-encoding trace-ids "
        "trace-time use-ascii verbose version xattr"
    ).split()
)

_WGET_HOST_SHORT = "B"
_WGET_OTHER_SHORT = "oaeitOTwQPUlARDIX"

_WGET_HOST_LONG = "base"
_WGET_OTHER_LONG = (
    "accept accept-regex append-output backups bind-address body-data body-file "
    "ca-certificate ca-directory certificate certificate-type config "
    "connect-timeout crl-file cut-dirs default-page directory-prefix dns-timeout "
    "domains egd-file exclude-directories exclude-domains execute follow-tags "
    "ftp-password ftp-user header http-password http-user ignore-tags "
    "include-directories input-file level limit-rate load-cookies local-encoding "
    "max-redirect method output-document output-file password post-data "
    "post-file prefer-family private-key private-key-type progress "
    "proxy-password proxy-user quota random-file read-timeout "
    "referer regex-type reject reject-regex remote-encoding report-speed "
    "restrict-file-names save-cookies secure-protocol timeout tries user "
    "user-agent wait waitretry warc-file"
)
_WGET_FLAGS = frozenset(
    (
        "adjust-extension ask-password auth-no-challenge background "
        "backup-converted cache check-certificate content-disposition "
        "content-on-error continue convert-links cookies debug delete-after "
        "directories dns-cache follow-ftp force-directories force-html glob help "
        "host-directories hsts http-keep-alive https-only ignore-case ignore-length "
        "inet4-only inet6-only iri keep-session-cookies mirror netrc no-cache "
        "no-check-certificate no-clobber no-config no-cookies no-directories "
        "no-dns-cache no-glob no-host-directories no-hsts no-http-keep-alive "
        "no-iri no-netrc no-parent no-passive-ftp no-proxy no-remove-listing "
        "no-verbose page-requisites parent passive-ftp preserve-permissions "
        "protocol-directories proxy quiet random-wait recursive relative "
        "remove-listing retr-symlinks retry-connrefused save-headers "
        "server-response show-progress span-hosts spider strict-comments "
        "timestamping trust-server-names unlink verbose version xattr"
    ).split()
)

_PS_HOST = "uri proxy"
_PS_OTHER = (
    "authentication body certificate certificatethumbprint "
    "connectiontimeoutseconds contenttype credential custommethod erroraction "
    "errorvariable form headers httpversion infile informationaction "
    "informationvariable maximumfollowrellink maximumredirection "
    "maximumretrycount method operationtimeoutseconds outbuffer outfile "
    "outvariable pipelinevariable proxycredential responseheadersvariable "
    "retryintervalsec sessionvariable sslprotocol statuscodevariable timeoutsec "
    "token transferencoding unixsocket useragent warningaction warningvariable "
    "websession"
)


def _table(host: str, other: str) -> Dict[str, str]:
    return {
        **{name: OTHER for name in other.split()},
        **dict.fromkeys(host.split(), HOST),
    }


_CURL_LONG = _table(_CURL_HOST_LONG, _CURL_OTHER_LONG)
_WGET_LONG = _table(_WGET_HOST_LONG, _WGET_OTHER_LONG)
_PS_PARAMS = _table(_PS_HOST, _PS_OTHER)

_POWERSHELL = frozenset({"invoke-webrequest", "iwr", "invoke-restmethod", "irm"})

#: ``host:port:addr[,addr]`` and ``host:port:host2:port2``, each host maybe bracketed.
_COLON_FIELD = re.compile(r"\[[^\]]*\]|[^:]*")
#: ``-e http_proxy=10.0.0.1:8080``: a wgetrc line that sets a proxy.
_WGETRC_PROXY = re.compile(r"^\s*(?:https?|ftp)_proxy\s*=\s*(.+)$", re.IGNORECASE)


def _colon_fields(value: str) -> List[str]:
    fields, rest = [], value
    while True:
        field = _COLON_FIELD.match(rest).group(0)
        fields.append(field)
        rest = rest[len(field) :]
        if not rest.startswith(":"):
            return fields
        rest = rest[1:]


def _host_values(name: str, value: str) -> List[str]:
    """The addresses inside the value of host option *name*."""
    if name == "resolve":
        fields = _colon_fields(value.lstrip("+"))
        return fields[2].split(",") if len(fields) > 2 else []
    if name == "connect-to":
        fields = _colon_fields(value)
        return [fields[2]] if len(fields) > 2 and fields[2] else []
    if name == "dns-servers":
        return value.split(",")
    return [value]


def _short_cluster(token: str, host: str, other: str) -> "tuple[str, str, str]":
    """``(letter, kind, attached value)`` of the value option in a ``-abc`` cluster.

    Letters are read left to right; the first that takes a value ends the
    cluster, with the rest of the token as its value, or the next word when
    nothing is attached. All empty when no letter takes a value.
    """
    for at, letter in enumerate(token[1:], start=1):
        kind = HOST if letter in host else OTHER if letter in other else ""
        if kind:
            return letter, kind, token[at + 1 :]
    return "", "", ""


def _long_option(
    name: str, valued: Dict[str, str], flags: "frozenset[str]"
) -> "tuple[str, str]":
    """``(kind, full name)`` of ``--name``; kind is HOST, OTHER, or "" for no value.

    A shortened name is read by prefix, as getopt and older curl builds read
    it. One that could be several options makes the program stop, so the
    choice only has to be safe: a host if any could be, and a value that is
    skipped only when every candidate takes one.
    """
    if name in valued:
        return valued[name], name
    if name in flags:
        return "", name
    candidates = {
        known: kind for known, kind in valued.items() if known.startswith(name)
    }
    hosts = [known for known, kind in candidates.items() if kind == HOST]
    if hosts:
        return HOST, hosts[0] if len(hosts) == 1 else name
    if candidates and not any(flag.startswith(name) for flag in flags):
        return OTHER, next(iter(candidates)) if len(candidates) == 1 else name
    return "", name


def _posix_destinations(argv: List[str], wget: bool) -> List[str]:
    long = _WGET_LONG if wget else _CURL_LONG
    flags = _WGET_FLAGS if wget else _CURL_FLAGS
    host_short = _WGET_HOST_SHORT if wget else _CURL_HOST_SHORT
    other_short = _WGET_OTHER_SHORT if wget else _CURL_OTHER_SHORT

    def value_hosts(name: str, kind: str, value: str) -> List[str]:
        if kind == HOST:
            return _host_values(name, value)
        if wget and name in ("e", "execute"):
            proxy = _WGETRC_PROXY.match(value)
            return [proxy.group(1)] if proxy else []
        return []

    found: List[str] = []
    pending = pending_kind = ""  # the option whose value the next word is
    only_operands = False
    for token in argv:
        if pending_kind:
            found.extend(value_hosts(pending, pending_kind, token))
            pending = pending_kind = ""
            continue
        if only_operands or not token.startswith("-") or token == "-":
            found.append(token)
            continue
        if token == "--":
            only_operands = True
            continue
        if token.startswith("--"):
            name, joined, value = token[2:].partition("=")
            name = name.lower()
            if not wget and name.startswith("expand-"):
                name = name[len("expand-") :]
            kind, name = _long_option(name, long, flags)
            if not kind:
                # An option this table does not know: only a full URL in it counts.
                if "://" in value:
                    found.append(value)
            elif not joined:
                pending, pending_kind = name, kind
            else:
                found.extend(value_hosts(name, kind, value))
            continue
        letter, kind, attached = _short_cluster(token, host_short, other_short)
        if kind and not attached:
            pending, pending_kind = letter, kind
        elif kind:
            found.extend(value_hosts(letter, kind, attached))
    return found


def _powershell_kind(name: str) -> str:
    """How PowerShell reads ``-name``, which may be any unambiguous prefix."""
    matches = {kind for param, kind in _PS_PARAMS.items() if param.startswith(name)}
    return HOST if HOST in matches else OTHER if matches else ""


def _powershell_destinations(argv: List[str]) -> List[str]:
    found: List[str] = []
    pending_kind = ""
    for token in argv:
        if pending_kind:
            if pending_kind == HOST:
                found.append(token)
            pending_kind = ""
            continue
        if not token.startswith("-") or len(token) < 2:
            found.append(token)
            continue
        name, joined, value = token[1:].partition(":")
        kind = _powershell_kind(name.lower())
        if not joined:
            pending_kind = kind
        elif kind == HOST or (not kind and "://" in value):
            found.append(value)
    return found


def destinations(program: str, argv: List[str]) -> List[str]:
    """The words of *argv* (the arguments after *program*) it connects to.

    URL operands and the values of options that name a host (a proxy, a
    ``--resolve`` address). The value of any other option is left out: an
    output file, a request body, a header, a timeout.
    """
    if program in _POWERSHELL:
        return _powershell_destinations(argv)
    return _posix_destinations(argv, wget=program == "wget")


__all__ = ["destinations"]
