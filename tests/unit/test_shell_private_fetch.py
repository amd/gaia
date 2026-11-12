# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A shell command that reaches a local or private address says so on its card.

The web tools refuse loopback and private-network addresses outright. The shell
tool runs whatever the user approves, so it cannot refuse ``curl`` to them, but
the approval card has to say that this is somewhere the web tools would not go,
and that approval covers this one command, never an "always".
"""

from __future__ import annotations

import pytest

from gaia.agents.tools.shell_tools import (
    TIER_CONFIRM_ONCE,
    ShellToolsMixin,
    private_destination,
)


class _Host(ShellToolsMixin):
    debug = False

    def __init__(self):
        super().__init__()

        class _Console:
            auto_approve_gated_tools = False
            full_access = False

        self.console = _Console()


@pytest.mark.parametrize(
    "command, host",
    [
        ("curl -s http://127.0.0.1:8765/article.html", "127.0.0.1"),
        ("curl -L http://localhost:3000/", "localhost"),
        ("curl http://[::1]:8080/x", "[::1]"),
        ("curl -s http://192.168.1.20:8000/page", "192.168.1.20"),
        ("curl http://10.0.0.5/admin", "10.0.0.5"),
        ("curl http://169.254.169.254/latest/meta-data/", "169.254.169.254"),
        ("curl 127.0.0.1:9000/page", "127.0.0.1"),
        ("curl http://2130706433/", "2130706433"),
        ("curl http://router.lan/", "router.lan"),
        ("curl -s http://0177.0.0.1:8123/x", "0177.0.0.1"),
        ("curl -s http://017700000001:8123/x", "017700000001"),
        ("curl -s http://0x7f.0.0.1:8123/x", "0x7f.0.0.1"),
        ("curl -s http://127.1:8123/x", "127.1"),
        ("curl -s http://127.0.1:8123/x", "127.0.1"),
        ("curl -s 2130706433:8123/x", "2130706433"),
        ("curl -s 2130706433", "2130706433"),
        ("curl 0x7f000001/x", "0x7f000001"),
        ("curl -s 127.1", "127.1"),
        ("curl 0:8123/x", "0"),
        ("curl 0", "0"),
        ("curl --retry 3 192.168.1", "192.168.1"),
        ("wget -q 0300.0250.1.1/index.html", "0300.0250.1.1"),
        ("curl user:pw@10.1:8080/x", "10.1"),
        ("curl -s http://127.0.0.1.nip.io:8123/x", "127.0.0.1.nip.io"),
        ("curl -s http://localtest.me:8123/x", "localtest.me"),
        ("curl app.localtest.me:8123/x", "app.localtest.me"),
        ("curl app-192-168-1-9.sslip.io/x", "app-192-168-1-9.sslip.io"),
        ("curl http://api.localhost:3000/", "api.localhost"),
        ("Invoke-WebRequest -Uri 127.1 -TimeoutSec 30", "127.1"),
        (
            'powershell -Command "Invoke-WebRequest -Uri http://127.0.0.1:8765/x"',
            "127.0.0.1",
        ),
        (
            "python -c \"import urllib.request; urllib.request.urlopen('http://10.1.2.3/')\"",
            "10.1.2.3",
        ),
    ],
)
def test_a_command_reaching_a_private_address_is_asked_once_with_a_note(command, host):
    error, _ = _Host()._line_verdict(command)

    assert error is not None, command
    assert error["tier"] == TIER_CONFIRM_ONCE
    assert host in error["risk_note"]
    assert "web tools" in error["risk_note"]


@pytest.mark.parametrize(
    "command",
    [
        "curl -s https://example.com/page",
        "curl --retry 3 https://8.8.8.8/",
        "git status",
        "ls docs/127.0.0.1",
        'grep "http://localhost:3000" notes.md',
        "echo http://127.0.0.1:8000",
        "echo 127 apples",
        "echo 127.1",
        "pip install foo==1.2.3",
        "pip install 10.2.3",
        "curl -s https://example.com:8123/x",
        "curl example.com:8123/x",
        "curl --retry 3 --max-time 10.5 https://example.com/",
        "curl -m 10 --connect-timeout 0 https://example.com/",
        "curl --limit-rate 2130706433 https://example.com/",
        "wget -t 3 -T 10 https://example.com/file.zip",
        "curl -o v10.0.1.tar.gz https://example.com/v10.0.1.tar.gz",
        "curl 8.8.8.8.nip.io/x",
        "curl https://nip.io/",
        "curl 1.1:80/x",
        "Invoke-WebRequest -Uri https://example.com -TimeoutSec 10",
    ],
)
def test_a_public_or_unrelated_command_gets_no_private_note(command):
    error, _ = _Host()._line_verdict(command)

    assert "web tools" not in str((error or {}).get("risk_note") or "")


#: 127.0.0.1 in full-width digits, with full-width and with ideographic full stops.
WIDE_LOOPBACK = "\uff11\uff12\uff17\uff0e\uff10\uff0e\uff10\uff0e\uff11"
IDEOGRAPHIC_LOOPBACK = "\uff11\uff12\uff17\u3002\uff10\u3002\uff10\u3002\uff11"


@pytest.mark.parametrize(
    "command, host",
    [
        # An option whose value is where the program connects.
        ("curl -s -x 127.0.0.1:8080 https://example.com/", "127.0.0.1"),
        ("curl -sx 10.1:3128 https://example.com/", "10.1"),
        ("curl -x10.0.0.5:3128 https://example.com/", "10.0.0.5"),
        ("curl --proxy http://localhost:8080 https://example.com/", "localhost"),
        ("curl --preproxy 192.168.1.9:1080 https://example.com/", "192.168.1.9"),
        ("curl --socks5-hostname 127.1:9050 https://example.com/", "127.1"),
        ("curl --url 127.0.0.1:9000/x", "127.0.0.1"),
        (
            "curl --doh-url https://10.0.0.53/dns-query https://example.com/",
            "10.0.0.53",
        ),
        ("curl --dns-servers 8.8.8.8,192.168.1.1 https://example.com/", "192.168.1.1"),
        (
            "curl --resolve example.com:443:127.0.0.1 https://example.com/",
            "127.0.0.1",
        ),
        ("curl --resolve example.com:443:[::1] https://example.com/", "[::1]"),
        (
            "curl --connect-to example.com:443:10.0.0.7:8443 https://example.com/",
            "10.0.0.7",
        ),
        ("wget -e http_proxy=10.0.0.1:8080 http://example.com/", "10.0.0.1"),
        ("wget --execute=https_proxy=localhost:8080 https://example.com/", "localhost"),
        ("wget -B http://127.0.0.1/ -i links.html", "127.0.0.1"),
        ("wget --base=http://192.168.0.2/ -i links.html", "192.168.0.2"),
        ("iwr https://example.com -Proxy http://127.0.0.1:8888", "127.0.0.1"),
        ("Invoke-WebRequest -Uri:http://10.0.0.5/x -OutFile page.html", "10.0.0.5"),
        ("Invoke-RestMethod -Method Post -Uri 127.1:5000/api -Body 8.8.8.8", "127.1"),
        # After `--` everything is an address.
        ("curl -s -- 127.0.0.1:8000/x", "127.0.0.1"),
        # A number with a port or path is in URL position.
        ("curl 3:8123/x", "3"),
        # Names that answer with the address written in them.
        ("curl -s http://127.0.0.1.xip.io/x", "127.0.0.1.xip.io"),
        ("curl -s http://127.0.0.1.traefik.me/x", "127.0.0.1.traefik.me"),
        ("curl app-10-0-0-1.traefik.me/x", "app-10-0-0-1.traefik.me"),
        # Digits a resolver folds to ASCII.
        (f"curl -s http://{WIDE_LOOPBACK}:8123/x", "127.0.0.1"),
        (f"curl -s http://{IDEOGRAPHIC_LOOPBACK}:8123/x", "127.0.0.1"),
        (f"curl {WIDE_LOOPBACK}\uff1a8123\uff0fx", "127.0.0.1"),
        (
            "curl http://\uff4c\uff4f\uff43\uff41\uff4c\uff48\uff4f\uff53\uff54/",
            "localhost",
        ),
        # Programs that fetch only a full URL.
        ("certutil -urlcache -split -f http://127.0.0.1:8000/a.exe a.exe", "127.0.0.1"),
        (
            "bitsadmin /transfer job /download /priority normal "
            "http://192.168.1.4/a.exe C:\\w\\a.exe",
            "192.168.1.4",
        ),
    ],
)
def test_an_argument_the_fetcher_connects_to_gets_the_note(command, host):
    error, _ = _Host()._line_verdict(command)

    assert error is not None, command
    assert error["tier"] == TIER_CONFIRM_ONCE
    assert f"Reaches {host} on" in error["risk_note"]


@pytest.mark.parametrize(
    "command",
    [
        # The value of an option that is not a destination.
        "curl -s -o 10.1 https://example.com/",
        "curl -so 10.1 https://example.com/",
        "curl -o10.1 https://example.com/",
        "curl --output 192.168.1.1 https://example.com/",
        "curl -o backup.local https://example.com/",
        "curl -s --data 127.0.0.1 https://example.com/",
        "curl -d 127.0.0.1 https://example.com/",
        "curl -d ip=http://127.0.0.1/ https://example.com/",
        "curl --data-raw 10.0.0.1 --data-urlencode host=localhost https://example.com/",
        "curl --json 127.0.0.1 https://example.com/",
        'curl -H "X-Forwarded-For: 127.0.0.1" https://example.com/',
        "curl -H Host:localhost https://example.com/",
        'curl -H "Referer: http://localhost/x" https://example.com/',
        "curl -e http://127.0.0.1/ https://example.com/",
        "curl -A 10.0.0.1 https://example.com/",
        "curl -u 10.1:secret https://example.com/",
        "curl -F file=@192.168.1.1 https://example.com/",
        "curl -X 127.0.0.1 https://example.com/",
        "curl -T 10.0.0.1 https://example.com/up",
        "curl -b 127.0.0.1 -c 127.0.0.1 https://example.com/",
        "curl --interface 192.168.1.5 https://example.com/",
        "curl --noproxy localhost,127.0.0.1 https://example.com/",
        "curl --retry 3 --max-time 10.5 -m 10.5 https://example.com/",
        "curl --resolve example.com:443:93.184.216.34 https://example.com/",
        "curl -x proxy.example.com:3128 https://example.com/",
        "wget -O 10.1 https://example.com/",
        "wget --output-document=127.0.0.1 https://example.com/",
        "wget --output-doc 127.0.0.1 https://example.com/",
        "wget --post-data 127.0.0.1 --header 'Host: localhost' https://example.com/",
        "wget -U 10.0.0.1 --referer=http://localhost/ https://example.com/",
        "wget -P 10.1 -e robots=off https://example.com/",
        "wget --bind-address=192.168.1.5 https://example.com/",
        "iwr https://example.com -OutFile 10.1",
        "iwr https://example.com -OutFile:10.1 -UseBasicParsing",
        "Invoke-WebRequest -Uri https://example.com -Body 127.0.0.1 -Method Post",
        "Invoke-WebRequest https://example.com -UserAgent 10.0.0.1 -TimeoutSec 10.5",
        "Invoke-RestMethod https://example.com -ContentType 127.0.0.1",
        # A bare count is not an address.
        "curl 3",
        "curl -s 3",
        "curl 80 https://example.com/",
        "wget 255",
        # A public name that only looks like the local ones.
        "curl 8.8.8.8.xip.io/x",
        "curl https://traefik.me/",
        "curl http://\uff18\uff0e\uff18\uff0e\uff18\uff0e\uff18/",
        "certutil -hashfile 127.0.0.1 SHA256",
    ],
)
def test_an_argument_that_is_not_a_destination_gets_no_note(command):
    error, _ = _Host()._line_verdict(command)

    assert "web tools" not in str((error or {}).get("risk_note") or ""), error


@pytest.mark.parametrize(
    "host, where",
    [
        ("127.0.0.1", "this PC"),
        ("LOCALHOST.", "this PC"),
        ("0.0.0.0", "this PC"),
        ("0x7f000001", "this PC"),
        ("[::ffff:192.168.0.1]", "your local network"),
        ("printer.local", "your local network"),
        ("example.com", ""),
        ("93.184.216.34", ""),
        ("0177.0.0.1", "this PC"),
        ("017700000001", "this PC"),
        ("0x7f.0.0.1", "this PC"),
        ("0X7F.0.0.1", "this PC"),
        ("127.1", "this PC"),
        ("127.0.1", "this PC"),
        ("0", "this PC"),
        ("10.1", "your local network"),
        ("192.168.257", "your local network"),
        ("0xc0.0250.1", "your local network"),
        ("0xa9fea9fe", "your local network"),
        ("010.0.0.1", ""),
        ("8.8", ""),
        ("1.2.3", ""),
        ("256.0.0.1", ""),
        ("127.0.0.256", ""),
        ("127.1.2.3.4", ""),
        ("0x", ""),
        ("089.0.0.1", ""),
        ("4294967296", ""),
        ("localtest.me", "this PC"),
        ("a.b.lvh.me", "this PC"),
        ("notlocaltest.me", ""),
        ("127.0.0.1.nip.io", "this PC"),
        ("app.127.0.0.1.nip.io", "this PC"),
        ("127-0-0-1.nip.io", "this PC"),
        ("app-10-0-0-1.nip.io", "your local network"),
        ("7f000001.nip.io", "this PC"),
        ("app.c0a80101.sslip.io", "your local network"),
        ("192.168.1.250.sslip.io", "your local network"),
        ("--1.sslip.io", "this PC"),
        ("fd00--1.sslip.io", "your local network"),
        ("8.8.8.8.nip.io", ""),
        ("app.nip.io", ""),
        ("sslip.io", ""),
        ("deadbeef.example.com", ""),
        ("999.0.0.1.nip.io", ""),
    ],
)
def test_private_destination(host, where):
    assert private_destination(host) == where


#: Every line of the release test's probe, with whether the note belongs on it.
PROBED = [
    ("curl -s gopher://127.0.0.1:6379/_x", True),
    ("curl -s dict://127.0.0.1:11211/stats", True),
    ("curl -s telnet://127.0.0.1:23", True),
    ("curl -s tftp://10.0.0.5/x", True),
    ("curl -s ldap://127.0.0.1/", True),
    ("curl -s smb://10.0.0.5/share/x", True),
    ("curl -s sftp://10.0.0.5/x", True),
    ("curl -s ws://127.0.0.1:8080/", True),
    ("curl -s HTTP://127.0.0.1:8123/x", True),
    ("curl -s http://127.0.0.1@example.com/", False),
    ("curl -s http://example.com@127.0.0.1/", True),
    ("curl -s 'https://example.com/?next=http://127.0.0.1/'", False),
    ("curl -s https://example.com/#127.0.0.1", False),
    ("curl -s -o 10.1 https://example.com/ -x 10.0.0.5:3128", True),
    ("set HTTP_PROXY=http://127.0.0.1:8080 && curl -s https://example.com/", True),
    ("curl -s --prox 10.0.0.5:3128 https://example.com/", True),
    ("curl -s --resolv example.com:443:127.0.0.1 https://example.com/", True),
    ("curl -s --outp 10.1 https://example.com/", False),
    ("curl -s --dat 127.0.0.1 https://example.com/", False),
    ("curl -s http://2130706433/", True),
    ("curl -s http://017700000001/", True),
    ("curl -s http://[0:0:0:0:0:ffff:127.0.0.1]/", True),
    ("curl -s http://[::ffff:7f00:1]/", True),
    ("curl -s http://[fd00::1]/", True),
    ("curl -s http://[fe80::1%25eth0]/", True),
    ("curl -s http://100.64.0.1/", True),
    ("curl -s http://0.0.0.0:8000/", True),
    ("curl -s http://172.16.5.4/", True),
    ("curl -s http://172.32.5.4/", False),
    ("curl -s http://11.0.0.1/", False),
    ('powershell -Command "irm http://127.0.0.1:8123/x"', True),
    (
        'powershell -Command "Start-BitsTransfer -Source http://10.0.0.5/x -Destination out.bin"',
        True,
    ),
    (
        "python -c \"import urllib.request;urllib.request.urlopen('http://127.0.0.1:8123/x')\"",
        True,
    ),
    ("curl -s -G -d q=1 http://127.0.0.1:8123/x", True),
    ("curl -s -oout http://127.0.0.1:8123/x", True),
    ("curl -s http://127.0.0.1.sslip.io/", True),
    ("curl -s http://localhost./x", True),
    ("curl -s http://LOCALHOST/x", True),
    ("curl -s http://host.docker.internal/x", True),
    ("curl -s http://127.0.0.1:8123/x -o 10.1 --data 10.2", True),
    ("curl -s -H 'Host: 127.0.0.1' https://example.com/", False),
    ("curl -s --request 127.0.0.1 https://example.com/", False),
    ("curl -s -o 127.0.0.1.html https://example.com/", False),
    ("curl --version", False),
    ("curl -s -O https://example.com/10.0.0.1.txt", False),
    ("curl -s --proxy-user 10.0.0.1:x https://example.com/", False),
    ("curl -s --noproxy 127.0.0.1 https://example.com/", False),
    ("curl -s -e 127.0.0.1 https://example.com/", False),
    ("curl -s --local-port 1024 https://example.com/", False),
    ("curl -s -Y 1000 -y 30 https://example.com/", False),
    ("curl -s -C 100 https://example.com/", False),
    ("curl -s -z 20260101 https://example.com/", False),
    ("curl -s --max-filesize 1000 https://example.com/", False),
    ("curl -s --retry-delay 5 --retry-max-time 60 https://example.com/", False),
    ("curl -s --parallel-max 4 https://example.com/", False),
    ("curl -s --keepalive-time 60 https://example.com/", False),
    ("curl -s --expect100-timeout 2 https://example.com/", False),
    ("curl -s --max-redirs 5 https://example.com/", False),
    ("curl -s --tftp-blksize 512 https://example.com/", False),
    ("curl -s --happy-eyeballs-timeout-ms 200 https://example.com/", False),
    ("curl -s --rate 2 https://example.com/", False),
    ("curl -s --speed-limit 100 --speed-time 10 https://example.com/", False),
    ("curl -s --create-file-mode 644 https://example.com/", False),
    ("wget --tries 3 --timeout 10 --wait 2 https://example.com/", False),
    ("wget -l 2 -Q 100 https://example.com/", False),
    ("wget --limit-rate 20 https://example.com/", False),
    ("wget -o 10.1 https://example.com/", False),
    ("wget --bind-address 10.0.0.5 https://example.com/", False),
    (
        'powershell -Command "iwr https://example.com/ -TimeoutSec 10 -MaximumRedirection 3"',
        False,
    ),
    (
        "powershell -Command \"iwr https://example.com/ -Headers @{'X-Forwarded-For'='127.0.0.1'}\"",
        False,
    ),
    ('powershell -Command "iwr https://example.com/ -UserAgent 127.0.0.1"', False),
]

#: The same rule, on lines the probe did not try.
BEYOND_THE_PROBE = [
    # Any scheme that opens a connection.
    ("curl -s gophers://127.0.0.1:70/", True),
    ("curl ldaps://192.168.1.10/dc=example", True),
    ("curl -u me: scp://10.0.0.5/etc/hosts", True),
    ("curl imap://localhost/INBOX", True),
    ("git clone ssh://git@192.168.1.5/repo.git", True),
    ("python -c \"import pysftp; open_url('sftp://10.0.0.5/x')\"", True),
    ("curl sftp://example.com/x", False),
    ("curl dict://dict.org/d:word", False),
    # A file URL opens a file, whatever host it names.
    ("curl file://127.0.0.1/C:/notes.txt", False),
    ("curl file://localhost/etc/hosts", False),
    # Only the URL's own host: not one in its path, query string or fragment.
    ("curl -s 'example.com/?u=http://127.0.0.1/'", False),
    ("curl -s 'https://example.com/go#http://localhost/'", False),
    ("curl -s 'https://example.com?mail=me@127.0.0.1'", False),
    ("curl -s https://example.com/http://10.0.0.5/", False),
    ("wget 'https://example.com/?next=http://localhost/'", False),
    ("iwr 'https://example.com/?next=http://127.0.0.1/'", False),
    ("python -c \"get('https://example.com/?next=http://127.0.0.1/')\"", False),
    ("curl -s 'http://127.0.0.1/?next=https://example.com/'", True),
    ("curl http://a@b@127.0.0.1/", True),
    # IPv6 with a zone, and bracketed with a port and no scheme.
    ("curl -g 'http://[fe80::1%eth0]:8080/'", True),
    ("curl -g [::1]:8080/x", True),
    ("curl -g '[fe80::1%25eth0]/x'", True),
    ("curl -g 'http://[2001:4860:4860::8888%25eth0]/'", False),
    # curl writes a URL glob out into several hosts.
    ("curl 'http://{example.com,127.0.0.1}/x'", True),
    ("curl 'http://192.168.1.[1-20]/'", True),
    ("curl 'http://example.com/{10.0.0.1,10.0.0.2}'", False),
    ("wget 'http://{example.com,127.0.0.1}/x'", False),
    # A shortened option is read as the option it shortens.
    ("curl -s --socks5-h 127.0.0.1:9050 https://example.com/", True),
    ("curl --connect-t example.com:443:10.0.0.7:8443 https://example.com/", True),
    ("curl --user-a 127.0.0.1 https://example.com/", False),
    ("curl --max-t 10.5 --head https://example.com/", False),
    ("curl --expand-output 10.1 https://example.com/", False),
    ("curl --expand-url 'http://127.0.0.1:8000/{{path}}'", True),
    # An option that takes no value does not hide the URL after it.
    ("curl --netrc http://127.0.0.1/", True),
    ("curl --ftp-ssl ftp://10.0.0.5/x", True),
    ("curl --head 127.0.0.1", True),
    ("wget --random-wait http://127.0.0.1/", True),
    ("wget --proxy http://192.168.1.4/", True),
    ("wget --no-verbose https://example.com/", False),
    ("wget --exec http_proxy=10.0.0.1:8080 http://example.com/", True),
]


@pytest.mark.parametrize("command, noted", PROBED + BEYOND_THE_PROBE)
def test_the_note_follows_where_the_command_connects(command, noted):
    error, _ = _Host()._line_verdict(command)
    note = str((error or {}).get("risk_note") or "")

    assert ("Reaches " in note and "web tools" in note) is noted, note
    if noted:
        assert error["tier"] == TIER_CONFIRM_ONCE


@pytest.mark.parametrize(
    "host, where",
    [
        ("[fe80::1%25eth0]", "your local network"),
        ("fe80::1%eth0", "your local network"),
        ("[::1%lo]", "this PC"),
        ("[2001:4860:4860::8888%eth0]", ""),
    ],
)
def test_an_ipv6_zone_does_not_hide_the_address(host, where):
    assert private_destination(host) == where
