"""The connection between Cloudflare and our nginx: HTTP/2 off, TLS 1.2 only, and honest reasons for the advisories that remain.

19 Nov 2026: the scan of our pinned nginx image (nginx 1.30.6) listed six new advisories, fixed in 1.30.7, and the image flavour we use
(nginxinc/nginx-unprivileged) has no 1.30.7 build yet. Three of them need a feature that we do not switch on (HTTP/3, the mail proxy, a proxy whose target name
holds variables together with SSI or mirror). The other three need HTTP/2 (two, one of them CRITICAL: a flow-control overflow and a use-after-free) or TLS 1.3 on
the listener (a HIGH denial of service). Cloudflare really does speak HTTP/2 to us (every request in the production log arrived as HTTP/2.0), so those three were
reachable in principle. They are closed by configuration: nginx speaks HTTP/1.1 and TLS 1.2 to Cloudflare, which negotiates down by itself.

These tests keep that configuration, and keep the list of accepted advisories explained.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROD = ROOT / "deploy" / "nginx" / "templates-production" / "platform.conf.template"
INSTANCES = ROOT / "deploy" / "nginx" / "instances" / "instances.conf"
NGINX_CONF = ROOT / "deploy" / "nginx" / "nginx.conf"
ACCEPTED = ROOT / "deploy" / "compose" / "accepted-advisories.txt"
PAGE = ROOT / "docs" / "security" / "dependency-bumps.md"

# the six nginx advisories of 19 Nov 2026 and what each needs (so that nobody accepts one without a reason)
NGINX_ADVISORIES = {
    "CVE-2026-121810": "HTTP/3",
    "CVE-2026-123007": "HTTP/2",
    "CVE-2026-123633": "HTTP/2",
    "CVE-2026-113425": "mail proxy",
    "CVE-2026-118899": "TLS 1.3",
    "CVE-2026-123634": "SSI or mirror",
}


def code(path: Path) -> str:
    return "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#"))


@pytest.mark.parametrize("path", [PROD, INSTANCES], ids=["platform block", "instance block"])
def test_http2_is_off_between_cloudflare_and_nginx(path):
    assert not re.search(r"\bhttp2\s+on\b", code(path)), "HTTP/2 stays off until the nginx image has 1.30.7 (the flow-control and use-after-free advisories)"
    assert not re.search(r"listen[^;]*\bhttp2\b", code(path)), "the old way of switching HTTP/2 on (listen ... http2) is no more allowed than `http2 on`"


@pytest.mark.parametrize("path", [PROD, INSTANCES], ids=["platform block", "instance block"])
def test_only_tls_12_is_offered_to_cloudflare(path):
    lines = [re.sub(r"\s+", " ", m).strip() for m in re.findall(r"^\s*ssl_protocols[^;]*;", code(path), re.MULTILINE)]
    assert lines == ["ssl_protocols TLSv1.2;"], lines


def test_the_default_listener_has_the_same_rule_because_every_connection_meets_it_first():
    conf = code(NGINX_CONF)
    http = conf.split("http {", 1)[1]
    assert re.search(r"^\s*ssl_protocols\s+TLSv1\.2;", http, re.MULTILINE), "set ssl_protocols in the http block so that the default server (which answers before the name is known) inherits it"
    assert "TLSv1.3" not in conf


def test_each_of_the_six_advisories_is_accepted_and_explained_with_what_it_needs():
    accepted = {line.strip() for line in ACCEPTED.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")}
    page = PAGE.read_text(encoding="utf-8")
    for cve, needs in NGINX_ADVISORIES.items():
        assert cve in accepted, f"{cve} is not on the reviewed list (the scan would fail)"
        paragraph = next((p for p in page.split("\n") if cve in p), "")
        assert needs.lower() in paragraph.lower(), f"{cve}: the page must say what it needs ({needs}) and why that is not reachable"


def test_the_page_says_when_the_mitigation_ends():
    page = PAGE.read_text(encoding="utf-8")
    assert "1.30.7" in page and "http2" in page.lower() and "TLS 1.2" in page
    assert "remove these identifiers" in page.lower() or "remove the identifiers" in page.lower()
