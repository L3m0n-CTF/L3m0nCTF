"""The platform VM in a fleet: only the central router, and a web address for every challenge instance.

With a separate challenge VM (deploy/challenge-host/) the platform VM must NOT run an instancer or a Docker-socket proxy of its own (a socket on the machine that
holds the database and the flag secret is exactly what the separate machine is for). `compose.instancer-central.yml` is the overlay for that: the router (hop 1,
which forwards to each challenge host's router), CTFd's per-host wiring, and an nginx server block for the instances' web addresses `<32 hex>.<instance domain>`.

The compose tests render the real files with `docker compose config` (client side; no daemon needed). The nginx syntax test starts the pinned nginx image and
skips when Docker cannot run.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_DIR = ROOT / "deploy" / "compose"
INSTANCES_CONF = ROOT / "deploy" / "nginx" / "instances" / "instances.conf"
PROD_TEMPLATE = ROOT / "deploy" / "nginx" / "templates-production" / "platform.conf.template"
DEV_TEMPLATE = ROOT / "deploy" / "nginx" / "templates" / "platform.conf.template"


def render(overlays: list[str], env_name: str = "production", extra_env: dict | None = None) -> dict:
    files = ["-f", str(COMPOSE_DIR / "compose.base.yml"), "-f", str(COMPOSE_DIR / f"compose.{env_name}.yml")]
    for overlay in overlays:
        files += ["-f", str(COMPOSE_DIR / f"compose.{overlay}.yml")]
    env = {k: v for k, v in os.environ.items() if not k.startswith("L3MON_") and k not in ("INSTANCE_DOMAIN", "INSTANCER_INSTANCE_POOL")}
    env.update(extra_env or {})
    res = subprocess.run(["docker", "compose", *files, "config", "--format", "json"], cwd=str(ROOT), capture_output=True, text=True, env=env, timeout=90)
    assert res.returncode == 0, f"docker compose config failed: {res.stderr}\n{res.stdout}"
    return json.loads(res.stdout)


@pytest.fixture
def fleet(tmp_path):
    hosts = tmp_path / "hosts.json"
    inst, rout = tmp_path / "instancer-tokens", tmp_path / "router-tokens"
    hosts.write_text("[]", encoding="utf-8")
    inst.mkdir()
    rout.mkdir()
    return {"hosts": hosts, "inst": inst, "rout": rout,
            "env": {"INSTANCE_DOMAIN": "l3m0nctf.xyz", "L3MON_HOSTS_FILE": str(hosts), "L3MON_INSTANCER_TOKENS_DIR": str(inst), "L3MON_ROUTER_TOKENS_DIR": str(rout)}}


def mounts(service: dict) -> dict:
    return {v.get("target"): v for v in service.get("volumes", [])}


# ------------------------------------------------------------------ the overlay

def test_the_platform_runs_only_the_router_and_never_a_docker_socket(fleet):
    services = render(["instancer-central"], extra_env=fleet["env"])["services"]
    assert "router" in services
    assert "instancer" not in services and "instancer-socket-proxy" not in services, "the instancer runs on the challenge VM, not here"
    for name, svc in services.items():
        for vol in svc.get("volumes", []):
            assert "docker.sock" not in str(vol.get("source", "")) + str(vol.get("target", "")), f"{name} mounts the Docker socket"


def test_the_router_publishes_no_port_and_knows_the_instance_domain(fleet):
    router = render(["instancer-central"], extra_env=fleet["env"])["services"]["router"]
    assert not router.get("ports"), "only nginx, on the private network, talks to the central router; players reach raw TCP challenges on the challenge host itself"
    assert router["environment"]["INSTANCE_DOMAIN"] == "l3m0nctf.xyz"
    assert router["environment"]["ROUTER_HOSTS_PATH"] == "/etc/l3mon/hosts.json"
    assert router.get("read_only") is True and router.get("cap_drop") == ["ALL"], "hardened like every other service"


def test_hosts_and_the_two_token_folders_go_where_least_privilege_says(fleet):
    services = render(["instancer-central"], extra_env=fleet["env"])["services"]
    ctfd_m, router_m = mounts(services["ctfd"]), mounts(services["router"])
    assert Path(ctfd_m["/etc/l3mon/hosts.json"]["source"]).resolve() == fleet["hosts"].resolve()
    assert Path(router_m["/etc/l3mon/hosts.json"]["source"]).resolve() == fleet["hosts"].resolve()
    assert Path(ctfd_m["/run/host-secrets"]["source"]).resolve() == fleet["inst"].resolve(), "CTFd gets the INSTANCER tokens only"
    assert Path(router_m["/run/host-secrets"]["source"]).resolve() == fleet["rout"].resolve(), "the router gets the ROUTER tokens only"
    assert all(v.get("read_only") for m in (ctfd_m, router_m) for t, v in m.items() if t in ("/etc/l3mon/hosts.json", "/run/host-secrets"))
    assert services["ctfd"]["environment"]["L3MON_HOSTS_PATH"] == "/etc/l3mon/hosts.json"
    assert services["ctfd"]["environment"]["L3MON_HOST_SECRETS_DIR"] == "/run/host-secrets"


def test_ctfd_derives_the_flags_so_it_alone_gets_the_flag_secret(fleet):
    services = render(["instancer-central"], extra_env=fleet["env"])["services"]

    def names(svc):
        return {s.get("source") if isinstance(s, dict) else s for s in svc.get("secrets", [])}

    assert "FLAG_HMAC_SECRET" in names(services["ctfd"])
    assert not names(services["router"]) & {"FLAG_HMAC_SECRET", "INSTANCER_TOKEN", "PRESET_ADMIN_TOKEN", "SECRET_KEY", "DATABASE_PASSWORD"}


def test_nginx_reaches_the_router_and_serves_the_instance_block(fleet):
    nginx = render(["instancer-central"], extra_env=fleet["env"])["services"]["nginx"]
    assert {"edge", "instancer"} <= set(nginx["networks"])
    block = mounts(nginx)["/etc/nginx/instances.d/instances.conf"]
    assert Path(block["source"]).resolve() == INSTANCES_CONF.resolve() and block.get("read_only")
    assert "router" in nginx["depends_on"], "nginx must not start before the router exists (it resolves the name at start)"


@pytest.mark.parametrize("missing", ["INSTANCE_DOMAIN", "L3MON_HOSTS_FILE", "L3MON_INSTANCER_TOKENS_DIR", "L3MON_ROUTER_TOKENS_DIR"])
def test_the_overlay_fails_loudly_without_what_it_needs(fleet, missing):
    env = dict(fleet["env"])
    del env[missing]
    files = []
    for name in ("compose.base.yml", "compose.production.yml", "compose.instancer-central.yml"):
        files += ["-f", str(COMPOSE_DIR / name)]
    clean = {k: v for k, v in os.environ.items() if not k.startswith("L3MON_") and k != "INSTANCE_DOMAIN"}
    res = subprocess.run(["docker", "compose", *files, "config", "--format", "json"], cwd=str(ROOT), capture_output=True, text=True, env={**clean, **env}, timeout=90)
    assert res.returncode != 0 and missing in res.stderr + res.stdout


def test_a_platform_with_one_instancer_gets_no_instance_block_and_no_fleet_files():
    services = render(["instancer", "instancer-production"], extra_env={"INSTANCE_DOMAIN": "l3m0nctf.xyz", "INSTANCER_INSTANCE_POOL": "10.200.0.0/16"})["services"]
    assert "/etc/nginx/instances.d/instances.conf" not in mounts(services["nginx"]), "the instance block is only there with the central overlay"


# ------------------------------------------------------------------ the nginx block

def text(path: Path) -> str:
    return "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#"))


def test_the_instance_block_answers_only_the_32_hex_names_and_only_with_cloudflares_certificate():
    conf = text(INSTANCES_CONF)
    # quoted, because nginx needs quotes around a regular expression that contains { }
    assert re.search(r'server_name\s+"~\^\[0-9a-f\]\{32\}\\\.";', conf), "only <32 hex>.<anything> reaches the router; every other name is the platform's or nobody's"
    assert "ssl_verify_client       on;" in conf and "ssl_client_certificate  /etc/nginx/tls/cloudflare-origin-pull-ca.pem;" in conf
    assert "listen 8443 ssl;" in conf


def test_the_instance_block_goes_to_the_router_and_never_to_ctfd():
    conf = text(INSTANCES_CONF)
    assert "upstream instance_router" in conf and re.search(r"server\s+router:8090;", conf)
    assert "proxy_pass http://instance_router;" in conf
    assert "proxy_set_header Host $host;" in conf, "the router reads <id>.<domain> from the Host header"
    for forbidden in ("ctfd_app", "platform-server.conf", "proxy.conf"):
        assert forbidden not in conf, f"the instance block must not share the platform's rules or upstream ({forbidden})"


def test_a_challenge_cannot_set_a_cookie_for_the_whole_domain():
    """A web challenge keeps its own cookies (and may use HTTP authentication), so nothing is stripped from the request. What is closed is the other direction:
    a challenge that sets `Domain=l3m0nctf.xyz` would plant a cookie that the browser also sends to the platform (cookie shadowing). Every Domain attribute is
    rewritten to the instance's own host name, which makes the cookie host-only."""
    conf = text(INSTANCES_CONF)
    assert "proxy_cookie_domain ~^.+$ $host;" in conf
    assert 'proxy_set_header Cookie ""' not in conf and 'proxy_set_header Authorization ""' not in conf


def tls_lines(block: str) -> list[str]:
    return sorted(re.sub(r"\s+", " ", line.strip()) for line in block.splitlines() if re.match(r"\s*ssl_", line))


def test_the_instance_block_uses_the_same_tls_settings_as_the_platform_block():
    platform = text(PROD_TEMPLATE).split("server_name ${PLATFORM_HOST}", 1)[1].split("include /etc/nginx/snippets/platform-server.conf;", 1)[0]
    assert tls_lines(platform) == tls_lines(text(INSTANCES_CONF)), "the two blocks must not drift apart (certificate, client certificate check, protocols)"


def test_only_the_production_template_has_the_optional_include_and_dev_has_none():
    assert "include /etc/nginx/instances.d/*.conf;" in text(PROD_TEMPLATE)
    assert "instances.d" not in text(DEV_TEMPLATE)


# ------------------------------------------------------------------ nginx itself accepts the configuration

def docker_works() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, timeout=60).returncode == 0


@pytest.mark.skipif(not docker_works(), reason="needs a running Docker (CI has one)")
@pytest.mark.parametrize("with_block", [False, True])
def test_nginx_accepts_the_production_configuration_with_and_without_the_instance_block(with_block):
    """`nginx -t` on the real production template, snippets and nginx.conf (throwaway certificates), with the instance block mounted or not."""
    image = re.search(r"image:\s*(nginxinc/nginx-unprivileged:\S+)", (COMPOSE_DIR / "compose.base.yml").read_text(encoding="utf-8")).group(1)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "tls").mkdir()
        run = lambda *a: subprocess.run(a, check=True, capture_output=True, cwd=tmp)  # noqa: E731
        run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "tls/origin.key", "-out", "tls/origin.pem", "-days", "1", "-subj", "/CN=test")
        shutil.copy(tmp / "tls" / "origin.pem", tmp / "tls" / "cloudflare-origin-pull-ca.pem")
        # nginx runs as an unprivileged user inside the container: the throwaway files (not secrets) must be readable by it
        (tmp / "tls").chmod(0o755)
        for item in (tmp / "tls").iterdir():
            item.chmod(0o644)
        rendered = PROD_TEMPLATE.read_text(encoding="utf-8").replace("${PLATFORM_HOST}", "play.example.test").replace("${EXTRA_HOST}", "crew.example.test")
        (tmp / "default.conf").write_text(rendered, encoding="utf-8")
        mount = ["-v", f"{INSTANCES_CONF}:/etc/nginx/instances.d/instances.conf:ro"] if with_block else []
        res = subprocess.run(
            ["docker", "run", "--rm", "--add-host", "ctfd:127.0.0.1", "--add-host", "router:127.0.0.1",
             "-v", f"{ROOT / 'deploy' / 'nginx' / 'nginx.conf'}:/etc/nginx/nginx.conf:ro", "-v", f"{ROOT / 'deploy' / 'nginx' / 'snippets'}:/etc/nginx/snippets:ro",
             "-v", f"{tmp / 'default.conf'}:/etc/nginx/conf.d/default.conf:ro", "-v", f"{tmp / 'tls'}:/etc/nginx/tls:ro", *mount,
             "--entrypoint", "nginx", image, "-t"],
            capture_output=True, text=True, timeout=120)
        assert res.returncode == 0, res.stderr
        assert "syntax is ok" in res.stderr
