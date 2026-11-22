"""The address a player is given for a web instance must open: https on a real domain, http only where the router itself is reached (development).

Found by the first platform-driven test round (22 Nov 2026): the agent put `http://<id>.l3m0nctf.xyz/` in the answer to every start, and the platform showed it
to the player as the link. Cloudflare in front of the platform answers on 443 only (a request to port 80 of `play.l3m0nctf.xyz`, or of an instance name, times
out), so the link a player clicked led nowhere; the same address with `https://` served the challenge. The scheme is now https for a real domain, http for a
development address (a port, or localhost), and `INSTANCE_URL_SCHEME` can force either.
"""
import pytest

from services.instancer.manager import InstanceManager
from services.instancer.manifest import load_manifest_from_string
from services.instancer.tests.test_instancer_core import FLAG_EXAMPLE, MANIFEST_TOML, MockDockerClient
from services.instancer.urls import instance_url_scheme


@pytest.mark.parametrize("domain", ["l3m0nctf.xyz", "inst.example.com", "inst.test", "x.y.z.example.org"])
def test_a_real_domain_is_https(domain):
    assert instance_url_scheme(domain) == "https"


@pytest.mark.parametrize("domain", ["localhost", "localhost:8090", "127.0.0.1:8090", "inst.localhost", "inst.example.com:8443", "[::1]:8090"])
def test_a_development_address_is_http(domain):
    assert instance_url_scheme(domain) == "http"


def test_the_setting_wins_over_the_guess():
    assert instance_url_scheme("l3m0nctf.xyz", "http") == "http"
    assert instance_url_scheme("localhost:8090", "https") == "https"
    assert instance_url_scheme("l3m0nctf.xyz", "HTTPS ") == "https"


@pytest.mark.parametrize("bad", ["ftp", "http://", "htps", "1"])
def test_a_scheme_that_is_not_http_or_https_is_refused(bad):
    with pytest.raises(ValueError):
        instance_url_scheme("l3m0nctf.xyz", bad)


def test_an_empty_setting_means_guess():
    assert instance_url_scheme("l3m0nctf.xyz", "") == "https"
    assert instance_url_scheme("localhost:8090", None) == "http"


def _start(domain, scheme=None):
    manifest = load_manifest_from_string(MANIFEST_TOML, dev_mode=False)
    kwargs = {} if scheme is None else {"url_scheme": scheme}
    mgr = InstanceManager(manifest=manifest, docker_client=MockDockerClient(), instance_domain=domain, time_fn=lambda: 1700000000, **kwargs)
    inst, _ = mgr.start_instance(team_id=7, challenge_id=42, slug="demo-web", flag=FLAG_EXAMPLE)
    return inst, mgr


def test_the_address_the_agent_hands_out_is_https_on_a_real_domain():
    inst, mgr = _start("l3m0nctf.xyz")
    assert inst["url"] == f"https://{inst['id']}.l3m0nctf.xyz/"
    # every other way the same instance is read back carries the same address
    assert mgr.get_instance(inst["id"])["url"] == inst["url"]
    assert [i["url"] for i in mgr.list_instances()] == [inst["url"]]


def test_the_address_is_http_where_the_router_is_reached_directly():
    inst, _ = _start("localhost:8090")
    assert inst["url"] == f"http://{inst['id']}.localhost:8090/"


def test_the_manager_takes_the_scheme_as_a_setting():
    inst, _ = _start("l3m0nctf.xyz", "http")
    assert inst["url"].startswith("http://")
