"""The beginner site on the platform (docs/runbooks/21-beginner-section.md): the image's Dockerfile, the compose overlay, the nginx block, and tools/ops/beginner.sh.

What is pinned, in plain words: the image can hold ONLY the four things its COPY lines name (so the author's flags export, tests and build leftovers can never get in), it runs as a
normal user; the overlay publishes no port, gives the container no way out and no capability, and does not make nginx wait for it or depend on it, so a broken beginner container
can never stop the platform; the nginx block looks its upstream up at request time (an `upstream` block would stop nginx from starting without the container), has the same TLS
rules as the platform's block, and real nginx accepts the production configuration with it; `pack` keeps the flags export and everything but the four allowed things out and refuses
to overwrite; `import` copies the flags file privately, removes it, and applies only when told to.

The compose tests render the real files with `docker compose config`; the nginx test starts the pinned nginx image. Both skip where Docker cannot run (CI has it).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_DIR = ROOT / "deploy" / "compose"
CONF = ROOT / "deploy" / "nginx" / "beginner" / "beginner.conf"
DOCKERFILE = ROOT / "deploy" / "beginner" / "Dockerfile"
TOOL = ROOT / "tools" / "ops" / "beginner.sh"
PROD_TEMPLATE = ROOT / "deploy" / "nginx" / "templates-production" / "platform.conf.template"
GIT_BASH = "C:/Program Files/Git/bin/bash.exe"
BASH = os.environ.get("L3MON_BASH") or (GIT_BASH if sys.platform == "win32" and os.path.exists(GIT_BASH) else "bash")


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def docker_works() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, timeout=60).returncode == 0


needs_docker = pytest.mark.skipif(not docker_works(), reason="needs a running Docker (CI has one)")


def render(extra_env: dict | None = None, overlays=("beginner",), expect_ok=True):
    files = ["-f", str(COMPOSE_DIR / "compose.base.yml"), "-f", str(COMPOSE_DIR / "compose.production.yml")]
    for overlay in overlays:
        files += ["-f", str(COMPOSE_DIR / f"compose.{overlay}.yml")]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("L3MON_", "BEGINNER_"))}
    env.update(extra_env or {})
    res = subprocess.run(["docker", "compose", *files, "config", "--format", "json"], cwd=str(ROOT), capture_output=True, text=True, env=env, timeout=90)
    if not expect_ok:
        return res
    assert res.returncode == 0, f"docker compose config failed: {res.stderr}\n{res.stdout}"
    return json.loads(res.stdout)


GOOD = {"BEGINNER_TAG": "test1", "BEGINNER_SECRET_KEY": "a-long-random-string-for-the-test-only"}


# ------------------------------------------------------------------ the image

def test_the_image_can_hold_only_the_four_things_its_copy_lines_name():
    lines = [l.strip() for l in text(DOCKERFILE).splitlines() if l.strip().startswith("COPY")]
    sources = sorted(re.sub(r"--\S+\s*", "", l)[len("COPY "):].rsplit(" ", 1)[0].strip() for l in lines)
    assert sources == ["app.py challenges.py", "static", "templates"], sources
    body = text(DOCKERFILE)
    assert not re.search(r"COPY\s+(--\S+\s+)*\.\s", body), "never copy the whole folder: it holds the flags export and the tests"
    for word in ("flags", "tests", "build_files", "export_flags", "requirements.txt"):
        assert not re.search(rf"COPY[^\n]*\b{word}\b", body), word


def test_the_image_runs_as_a_normal_user_on_a_pinned_base_shared_with_the_instancer():
    body = text(DOCKERFILE)
    assert re.search(r"^USER 10003:10003$", body, re.M) and "10003" in body
    base = re.search(r"^FROM (python:\S+@sha256:[0-9a-f]{64})$", body, re.M)
    assert base, "the base image is pinned by digest"
    assert base.group(1) in text(ROOT / "services" / "instancer" / "Dockerfile"), "the same base (and so the same scanned layers) as the instancer's image"
    assert "waitress-serve" in body and "--threads=8" in body and "HEALTHCHECK" in body


def test_the_dependencies_are_pinned_exactly():
    pins = re.findall(r"\b([a-z0-9]+)==([0-9][0-9.]*)", text(DOCKERFILE))
    names = {n for n, _ in pins}
    assert {"flask", "waitress", "werkzeug", "jinja2", "markupsafe", "itsdangerous", "click", "blinker"} <= names
    assert "pillow" not in names and "scapy" not in names and "ziglang" not in names, "the build-only tools of the author do not go into the running image"


# ------------------------------------------------------------------ the overlay

@needs_docker
def test_the_beginner_container_has_no_port_no_way_out_and_no_privilege():
    svc = render(GOOD)["services"]["beginner"]
    assert svc["image"] == "l3mon/beginner:test1" and "ports" not in svc
    assert svc["read_only"] is True and svc["cap_drop"] == ["ALL"] and svc["user"] == "10003:10003" and "no-new-privileges:true" in svc["security_opt"]
    assert svc["mem_limit"] and svc["pids_limit"] and svc["restart"] == "unless-stopped"
    assert list(svc["networks"]) == ["beginner"], "only its own private network"
    assert svc["environment"]["BEGINNER_SECRET_KEY"] == GOOD["BEGINNER_SECRET_KEY"]
    assert not any("docker.sock" in str(v) for v in svc.get("volumes", [])) and not svc.get("volumes"), "it mounts nothing"


@needs_docker
def test_the_private_network_has_no_way_to_the_internet_and_nginx_joins_it():
    cfg = render(GOOD)
    assert cfg["networks"]["beginner"]["internal"] is True and cfg["networks"]["beginner"]["name"] == "l3mon_beginner"
    nginx = cfg["services"]["nginx"]
    assert set(nginx["networks"]) >= {"edge", "beginner"}
    mounts = {v["target"]: v for v in nginx["volumes"]}
    assert mounts["/etc/nginx/instances.d/beginner.conf"]["source"].endswith("beginner.conf") and mounts["/etc/nginx/instances.d/beginner.conf"]["read_only"] is True


@needs_docker
def test_a_broken_beginner_container_can_never_stop_nginx_or_the_platform():
    nginx = render(GOOD)["services"]["nginx"]
    assert "beginner" not in (nginx.get("depends_on") or {}), "nginx must not wait for, or depend on, the beginner container"
    plain = render({}, overlays=(), expect_ok=True)["services"]
    assert "beginner" not in plain, "without the overlay nothing about it exists"


@needs_docker
@pytest.mark.parametrize("missing", ["BEGINNER_TAG", "BEGINNER_SECRET_KEY"])
def test_the_overlay_refuses_to_start_without_its_tag_and_its_secret(missing):
    env = {k: v for k, v in GOOD.items() if k != missing}
    res = render(env, expect_ok=False)
    assert res.returncode != 0 and missing in res.stderr


@needs_docker
def test_the_other_services_are_unchanged_by_the_overlay():
    plain, with_overlay = render({}, overlays=())["services"], render(GOOD)["services"]
    for name in plain:
        if name == "nginx":
            continue
        assert plain[name] == with_overlay[name], f"the overlay changed {name}"


# ------------------------------------------------------------------ the nginx block

def test_the_block_looks_its_upstream_up_at_request_time_so_a_missing_container_never_stops_nginx():
    conf = text(CONF)
    assert not re.search(r"^\s*upstream\s+\w+\s*\{", conf, re.M), "an upstream block is resolved when nginx starts: a missing container would stop nginx (and the platform with it)"
    assert "resolver 127.0.0.11" in conf and "set $beginner_upstream http://beginner:8000;" in conf
    assert conf.count("proxy_pass $beginner_upstream;") == 2
    assert 'server_name "~^beginner\\.";' in conf
    for forbidden in ("ctfd_app", "platform-server.conf", "proxy.conf", "instance_router"):
        assert forbidden not in conf, f"the beginner block must not share the platform's rules or upstreams ({forbidden})"


def tls_lines(block: str) -> list[str]:
    return sorted(re.sub(r"\s+", " ", line.strip()) for line in block.splitlines() if re.match(r"\s*ssl_", line))


def test_the_block_uses_the_same_tls_rules_as_the_platform_block_and_only_cloudflare_may_connect():
    platform = text(PROD_TEMPLATE).split("server_name ${PLATFORM_HOST}", 1)[1].split("include /etc/nginx/snippets/platform-server.conf;", 1)[0]
    assert tls_lines(platform) == tls_lines(text(CONF)), "the blocks must not drift apart (certificate, client certificate check, protocols)"
    assert "ssl_verify_client       on;" in text(CONF) and "http2" not in text(CONF)


def test_the_block_has_the_security_headers_and_the_limits_and_a_stricter_flag_checker():
    conf = text(CONF)
    assert "include /etc/nginx/snippets/security-headers.conf;" in conf
    assert "limit_req zone=general" in conf and "limit_conn perip" in conf and "client_max_body_size 16k;" in conf
    assert re.search(r"location = /api/flag \{\s*limit_req zone=flags", conf), "the lesson's flag checker is limited like the platform's flag submissions"
    zones = text(ROOT / "deploy" / "nginx" / "nginx.conf")
    for zone in ("general", "flags", "perip"):
        assert f"zone={zone}:" in zones, f"the zone {zone} must exist in nginx.conf"


@needs_docker
def test_nginx_accepts_the_production_configuration_with_the_beginner_block_and_no_beginner_container():
    """`nginx -t` on the real production template with the block mounted and NO host called `beginner` anywhere: it must still be valid and start."""
    image = re.search(r"image:\s*(nginxinc/nginx-unprivileged:\S+)", (COMPOSE_DIR / "compose.base.yml").read_text(encoding="utf-8")).group(1)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "tls").mkdir()
        run = lambda *a: subprocess.run(a, check=True, capture_output=True, cwd=tmp)  # noqa: E731
        run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "tls/origin.key", "-out", "tls/origin.pem", "-days", "1", "-subj", "/CN=test")
        shutil.copy(tmp / "tls" / "origin.pem", tmp / "tls" / "cloudflare-origin-pull-ca.pem")
        (tmp / "tls").chmod(0o755)
        for item in (tmp / "tls").iterdir():
            item.chmod(0o644)
        rendered = PROD_TEMPLATE.read_text(encoding="utf-8").replace("${PLATFORM_HOST}", "play.example.test").replace("${EXTRA_HOST}", "crew.example.test")
        (tmp / "default.conf").write_text(rendered, encoding="utf-8")
        res = subprocess.run(
            ["docker", "run", "--rm", "--add-host", "ctfd:127.0.0.1",
             "-v", f"{ROOT / 'deploy' / 'nginx' / 'nginx.conf'}:/etc/nginx/nginx.conf:ro", "-v", f"{ROOT / 'deploy' / 'nginx' / 'snippets'}:/etc/nginx/snippets:ro",
             "-v", f"{tmp / 'default.conf'}:/etc/nginx/conf.d/default.conf:ro", "-v", f"{tmp / 'tls'}:/etc/nginx/tls:ro",
             "-v", f"{CONF}:/etc/nginx/instances.d/beginner.conf:ro",
             "--entrypoint", "nginx", image, "-t"],
            capture_output=True, text=True, timeout=120)
        assert res.returncode == 0, res.stderr
        assert "syntax is ok" in res.stderr


# ------------------------------------------------------------------ tools/ops/beginner.sh

def make_source(folder: Path, junk=True):
    (folder / "templates" / "guides").mkdir(parents=True)
    (folder / "static" / "files" / "x").mkdir(parents=True)
    (folder / "app.py").write_text("app = None\n", encoding="utf-8")
    (folder / "challenges.py").write_text("FLAGS = {}\n", encoding="utf-8")
    (folder / "templates" / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    (folder / "static" / "files" / "x" / "download.bin").write_bytes(b"\x00\x01")
    if junk:
        for name in ("flags", "tests", "build", "__pycache__", ".git"):
            (folder / name).mkdir()
            (folder / name / "secret.txt").write_text("must stay out", encoding="utf-8")
        (folder / "flags" / "beginner_flags.json").write_text("{}", encoding="utf-8")
        (folder / "requirements.txt").write_text("flask\n", encoding="utf-8")
        (folder / "build_files.py").write_text("x = 1\n", encoding="utf-8")
        (folder / "templates" / "stale.pyc").write_bytes(b"\x00")
        (folder / "__pycache__" / "challenges.cpython-314.pyc").write_bytes(b"\x00")


def pack(folder: Path, out: Path):
    return subprocess.run([BASH, str(TOOL), "pack", folder.as_posix(), out.as_posix()], capture_output=True, text=True)


def test_pack_keeps_only_the_four_allowed_things_whatever_else_the_folder_holds(tmp_path):
    make_source(tmp_path / "src")
    out = tmp_path / "out.tgz"
    done = pack(tmp_path / "src", out)
    assert done.returncode == 0, done.stdout + done.stderr
    with tarfile.open(out) as tar:
        names = [m.name for m in tar.getmembers() if m.isfile()]
    assert sorted(names) == ["app.py", "challenges.py", "static/files/x/download.bin", "templates/index.html"], names
    assert "sha256" in done.stdout and "keep it out of every repository" in done.stdout


def test_pack_refuses_what_is_not_the_beginners_section_and_never_overwrites(tmp_path):
    (tmp_path / "empty").mkdir()
    assert pack(tmp_path / "empty", tmp_path / "a.tgz").returncode == 2
    make_source(tmp_path / "src", junk=False)
    out = tmp_path / "out.tgz"
    out.write_bytes(b"precious")
    done = pack(tmp_path / "src", out)
    assert done.returncode == 2 and out.read_bytes() == b"precious", "an existing file is never overwritten"
    assert subprocess.run([BASH, str(TOOL), "pack", (tmp_path / "src").as_posix(), (tmp_path / "x.zip").as_posix()], capture_output=True).returncode == 2, "the archive is a .tgz"


FAKE_GCLOUD = """#!/usr/bin/env bash
echo "gcloud $*" >> "$FAKE_LOG"
exit 0
"""


@pytest.fixture
def fake(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gcloud").write_text(FAKE_GCLOUD, encoding="utf-8", newline="\n")
    os.chmod(bindir / "gcloud", 0o755)
    log = tmp_path / "calls.log"
    log.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env["L3MON_GCLOUD"] = (bindir / "gcloud").as_posix()
    env["FAKE_LOG"] = log.as_posix()
    env["CLOUDSDK_CORE_PROJECT"] = "a-test-project"
    return env, log


def run_import(env, *args):
    return subprocess.run([BASH, str(TOOL), "import", *args], capture_output=True, text=True, env=env)


def test_import_is_a_dry_run_unless_told_to_apply_and_copies_the_file_privately(fake, tmp_path):
    env, log = fake
    flags = tmp_path / "beginner_flags.json"
    flags.write_text('{"challenges": []}', encoding="utf-8")
    done = run_import(env, flags.as_posix())
    assert done.returncode == 0, done.stderr
    scp, ssh = log.read_text(encoding="utf-8").splitlines()
    assert "compute scp" in scp and "--tunnel-through-iap" in scp and "l3mon-app:beginner_flags.json" in scp
    assert "chmod 600 ~/beginner_flags.json" in ssh and "rm -f ~/beginner_flags.json" in ssh and "l3mon_release.beginner import main" in ssh
    assert "--apply" not in ssh, "without --apply the platform is only asked what it would do"
    log.write_text("", encoding="utf-8")
    assert run_import(env, "--apply", flags.as_posix()).returncode == 0
    assert "--apply < ~/beginner_flags.json" in log.read_text(encoding="utf-8").splitlines()[1]
    assert "l3mon-chal" not in log.read_text(encoding="utf-8"), "the challenge VM is never involved"


def test_import_refuses_a_missing_file_and_a_missing_project_and_sends_nothing(fake, tmp_path):
    env, log = fake
    assert run_import(env, (tmp_path / "nope.json").as_posix()).returncode == 2
    flags = tmp_path / "f.json"
    flags.write_text("{}", encoding="utf-8")
    env2 = {k: v for k, v in env.items() if k != "CLOUDSDK_CORE_PROJECT"}
    done = run_import(env2, flags.as_posix())
    assert done.returncode != 0 and "CLOUDSDK_CORE_PROJECT" in done.stderr
    assert log.read_text(encoding="utf-8") == ""


def test_the_script_is_valid_shell_and_executable_in_git():
    assert subprocess.run([BASH, "-n", str(TOOL)], capture_output=True).returncode == 0
    out = subprocess.run(["git", "ls-files", "-s", "tools/ops/beginner.sh"], cwd=ROOT, capture_output=True, text=True).stdout
    assert not out or out.startswith("100755"), "executable in git: " + out


def test_the_runbook_names_the_commands_the_decisions_and_the_way_back():
    runbook = text(ROOT / "docs" / "runbooks" / "21-beginner-section.md")
    for needle in ("beginner.sh pack", "beginner.sh import", "--apply", "L3MON_OVERLAYS", "deploy/beginner/Dockerfile", "Take it back", "channel bonus", "When the site goes up"):
        assert needle in runbook, needle
    assert not re.search(r"L3m0n\{[0-9a-f]", runbook), "a runbook never holds a flag"
