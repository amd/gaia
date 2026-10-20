# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Internet off lets a call through only when every destination is this PC.

"This PC" is ``localhost`` exactly or a loopback IP literal. Anything that
merely looks local — a look-alike name, userinfo, a numeric form the resolver
might read differently, a second command on the same line — is refused.
"""

import pytest

from gaia.agents.base.offline import _is_loopback, offline_refusal


def _refused(name, **args):
    result = offline_refusal(name, args)
    return result is not None and result["error_type"] == "internet_off"


class TestLoopbackHost:
    @pytest.mark.parametrize(
        "host",
        ["localhost", "LOCALHOST", "127.0.0.1", "127.255.255.254", "::1", "[::1]"],
    )
    def test_true_loopback(self, host):
        assert _is_loopback(host)

    @pytest.mark.parametrize(
        "host",
        [
            "127.0.0.1.attacker.example",
            "127.evil.example",
            "localhost.attacker.example",
            "localhost.",
            "evil-localhost",
            "0.0.0.0",
            "2130706433",
            "0x7f000001",
            "0177.0.0.1",
            "127.1",
            "128.0.0.1",
            "::",
            "fe80::1",
            "::2",
            "",
            "localhost:80",
        ],
    )
    def test_look_alikes_are_not_loopback(self, host):
        assert not _is_loopback(host)


class TestShellCommands:
    @pytest.mark.parametrize(
        "command",
        [
            # A local URL anywhere on the line must not clear the rest of it.
            "curl http://localhost:8000/ && curl example.com",
            "curl http://127.0.0.1/ ; git push origin main",
            "curl http://localhost/ || pip install requests",
            "curl http://localhost/ | npx some-tool",
            "curl http://localhost/\ngh repo clone a/b",
            "curl http://localhost/ example.com",
            "curl http://localhost/ https://example.com/",
            "curl --url https://example.com http://localhost/",
            "curl --url=https://example.com http://localhost/",
            # Hosts that only look local.
            "curl http://127.0.0.1.attacker.example/",
            "curl http://localhost.attacker.example/",
            "curl http://localhost@example.com/",
            "curl http://127.0.0.1:80@example.com/",
            "curl http://user:pw@example.com/?next=http://localhost/",
            "curl http://example.com#@localhost/",
            "curl http://example.com\\@localhost/",
            "curl 'http://localhost@a@example.com/'",
            "curl http://2130706433/",
            "curl http://0x7f000001/",
            "curl http://0177.0.0.1/",
            "curl http://127.1/",
            "curl http://0.0.0.0:8000/",
            "curl http://[fe80::1]/",
            "curl http://[::ffff:93.184.216.34]/",
            "curl http://%6c%6fcalhost/",
            "curl http://{localhost,example.com}/",
            "curl 127.0.0.1.attacker.example",
            # Options that send a local-looking request elsewhere.
            "curl -x http://proxy.example:8080 http://localhost/",
            "curl --proxy http://proxy.example http://localhost/",
            "curl -sx http://proxy.example http://localhost/",
            "curl --resolve localhost:80:93.184.216.34 http://localhost/",
            "curl --connect-to ::example.com:80 http://localhost/",
            "curl -K urls.txt http://localhost/",
            "wget -i urls.txt http://localhost/",
            "wget -e use_proxy=on http://localhost/",
            "iwr -Proxy http://proxy.example http://localhost/",
            "Invoke-WebRequest -Uri https://example.com -OutFile x",
            "iwr -Uri:https://example.com http://localhost/",
            # Destinations GAIA can't see.
            "http_proxy=http://proxy.example curl http://localhost/",
            "env HTTPS_PROXY=http://p.example curl http://localhost/",
            "curl http://localhost/$(curl example.com)",
            "curl `cat url.txt`",
            "curl ${URL}",
            "echo example.com | xargs curl http://localhost/",
            "find . -name '*.url' -exec curl {} ;",
            "curl",
            # Wrapped and nested.
            'bash -c "curl http://localhost/ && git push"',
            "sh -c 'curl https://example.com'",
            'powershell -Command "iwr https://example.com"',
            'cmd /c "curl http://localhost/ & curl example.com"',
            "sudo curl https://example.com",
            "timeout 5 curl https://example.com",
            "nice -n 10 git push",
            "/usr/bin/curl https://example.com",
            "curl.exe https://example.com",
            # Online commands never cleared by a local URL.
            "git clone http://localhost:3000/repo.git",
            "pip install --index-url http://localhost/simple pkg",
            "ssh localhost",
        ],
    )
    def test_refused(self, command):
        assert _refused("run_shell_command", command=command), command

    @pytest.mark.parametrize(
        "command",
        [
            "curl http://localhost:4316/api/health",
            "curl -s -o out.json http://127.0.0.1:8000/x",
            "curl -sSLo out.txt http://localhost/",
            "curl -X POST -H 'Content-Type: application/json' -d '{}' localhost:8000/api",
            "curl --max-time 5 http://[::1]:8000/",
            "curl http://127.42.0.9/",
            "curl http://localhost/ && curl http://127.0.0.1:9000/",
            "wget -O page.html http://localhost:5000/",
            "iwr http://localhost:8000/ -UseBasicParsing -OutFile x.html",
            "Invoke-RestMethod -Uri http://127.0.0.1:4200/api -Method Get",
            "curl file:///tmp/x",
            "git status && git log --oneline",
            "python -m pytest",
            "echo done",
            "git commit -m 'pull and push later'",
        ],
    )
    def test_local_only_still_runs(self, command):
        assert not _refused("run_shell_command", command=command), command


class TestToolArguments:
    @pytest.mark.parametrize("tool", ["fetch_page", "download_file", "browser_open"])
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1.attacker.example/",
            "http://localhost@example.com/",
            "http://localhost.example.com/",
            "http://0.0.0.0/",
            "http://2130706433/",
            "https://example.com/?u=http://localhost/",
            "localhost:8000",
        ],
    )
    def test_web_tools_to_look_alikes_are_refused(self, tool, url):
        assert _refused(tool, url=url)

    @pytest.mark.parametrize("tool", ["fetch_page", "browser_open"])
    @pytest.mark.parametrize(
        "url", ["http://localhost:5316/", "http://127.0.0.1/", "http://[::1]:80/"]
    )
    def test_web_tools_on_this_pc_run(self, tool, url):
        assert not _refused(tool, url=url)

    def test_search_web_is_refused_even_with_a_local_url(self):
        assert _refused("search_web", query="x", url="http://localhost/")

    def test_a_local_url_argument_does_not_clear_an_online_command(self):
        assert _refused(
            "run_shell_command", command="git push", url="http://localhost/"
        )

    def test_a_local_url_argument_does_not_clear_a_connector(self):
        result = offline_refusal(
            "mcp_x_fetch", {"url": "http://localhost/"}, {"_mcp_server": "x"}
        )
        assert result is not None and "x connector" in result["error"]

    def test_python_request_with_userinfo_is_refused(self):
        code = 'import requests\nrequests.get("http://localhost@example.com/")'
        assert _refused("run_python_code", code=code)

    def test_python_request_to_this_pc_runs(self):
        code = 'import requests\nrequests.get("http://127.0.0.1:8000/")'
        assert not _refused("run_python_code", code=code)

    def test_a_url_written_into_a_file_is_not_a_request(self):
        assert not _refused(
            "write_file", file_path="notes.md", content="see https://example.com"
        )
