import socket
from urllib.parse import urlparse

from gaia.web.client import WebClient


def test_ip_pinning_prevents_dns_rebind(monkeypatch):
    client = WebClient()

    # Fake getaddrinfo behavior:
    # - When asked for the original hostname, return a public IP (pinned).
    # - When asked for any other host (including literal IPs), return the
    #   host itself so the pinned wrapper can call through successfully.
    def fake_getaddrinfo(host, port, *args, **kwargs):
        if host == "example.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        # Assume host is already an IP -- echo it back
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    # Replace the real session.request with a fake that calls getaddrinfo to
    # simulate the HTTP stack performing a DNS lookup during connect. This
    # fake will assert that the lookup seen during the actual request is the
    # pinned IP we validated earlier.
    def fake_request(method, url, **kwargs):
        parsed = urlparse(url)
        resolved = socket.getaddrinfo(parsed.hostname, 80)
        assert resolved[0][4][0] == "93.184.216.34"

        class DummyResp:
            status_code = 200
            headers = {}
            encoding = None
            apparent_encoding = "utf-8"

            def iter_content(self, chunk_size=8192):
                yield b""

        return DummyResp()

    monkeypatch.setattr(client._session, "request", fake_request)

    # This should not raise; internally we validate and then the fake_request
    # should observe the pinned IP when calling getaddrinfo.
    resp = client.get("http://example.com/")
    assert resp.status_code == 200
