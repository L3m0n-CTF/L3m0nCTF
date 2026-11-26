"""tools/ops/pull-backups.sh: the copy of the encrypted backups that lives on the owner's own computer (the off-machine copy, owner's decision of 26 Nov 2026).

Nothing real is touched: a fake `gcloud` first on PATH answers the listing from a folder that stands for the VM's backup folder, and copies files out of it. What is pinned:
only what is missing is copied; every copy is checked against its checksum and its encryption header (a damaged or foreign file is reported and the exit code is 1);
--since skips older backups; --identity opens the newest copy for real with `age` (skipped where age is not installed); old copies are pruned; the tool needs the project id
and refuses a bad option; and it never runs anything on the VM except a listing.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "ops" / "pull-backups.sh"
GIT_BASH = "C:/Program Files/Git/bin/bash.exe"
BASH = os.environ.get("L3MON_BASH") or (GIT_BASH if sys.platform == "win32" and os.path.exists(GIT_BASH) else "bash")

FAKE_GCLOUD = """#!/usr/bin/env bash
# a fake of gcloud: `compute ssh ... --command "ls -1 DIR"` lists $FAKE_REMOTE/DIR; `compute scp ... VM:PATH ... DEST/` copies from $FAKE_REMOTE
echo "gcloud $*" >> "$FAKE_LOG"
case "$*" in
  *"compute ssh"*)
    all="$*"; cmd="${all##*--command }"
    case "$cmd" in
      "cd "*"&& ls -1 && for f in backup-*.tar.age.sha256; do printf "*) # the one read-only listing the tool makes: the names and every checksum, in the folder given by `cd`
        dir="${cmd#cd }"; dir="${dir%% &&*}"
        ( cd "$FAKE_REMOTE/$dir" && ls -1 && for f in backup-*.tar.age.sha256; do printf 'SUM %s %s\n' "$f" "$(cat "$f")"; done ) ;;
      *) echo "fake gcloud: refusing to run '$cmd' on the VM" >&2; exit 9 ;;
    esac ;;
  *"compute scp"*)
    args=("$@"); dest="${args[-1]}"
    for a in "${args[@]}"; do
      case "$a" in l3mon-app:*) cp "$FAKE_REMOTE/${a#l3mon-app:}" "$dest" ;; esac
    done ;;
esac
exit 0
"""


def stamp(n: int) -> str:
    return f"202611{26 + n // 48:02d}T{(n % 48) // 2:02d}{30 * (n % 2):02d}00Z"


def make_remote(folder: Path, count: int, damage: dict | None = None, content=None) -> list[str]:
    """`count` pretend backups: a file that starts like an age file, and a checksum file beside it. `damage` maps a name to 'sum' or 'header'."""
    d = folder / "backups" / "sink" / "full"
    d.mkdir(parents=True)
    names = []
    for n in range(count):
        name = f"backup-{stamp(n)}.tar.age"
        body = content(n) if content else b"age-encryption.org/v1\n-> X25519 fake\n" + os.urandom(64) + bytes([n])
        if damage and damage.get(name) == "header":
            body = b"this is not an age file at all, however long it is........"
        (d / name).write_bytes(body)
        digest = hashlib.sha256(body).hexdigest()
        if damage and damage.get(name) == "sum":
            digest = "0" * 64
        (d / f"{name}.sha256").write_text(f"{digest}  {name}\n", encoding="utf-8", newline="\n")
        names.append(name)
    (d / "notes.txt").write_text("not a backup", encoding="utf-8")
    return names


@pytest.fixture
def env(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gcloud").write_text(FAKE_GCLOUD, encoding="utf-8", newline="\n")
    os.chmod(bindir / "gcloud", 0o755)
    remote = tmp_path / "vm"
    remote.mkdir()
    log = tmp_path / "calls.log"
    log.write_text("", encoding="utf-8")
    e = dict(os.environ)
    e["L3MON_GCLOUD"] = (bindir / "gcloud").as_posix()  # Git Bash puts its own tools ahead of the inherited PATH
    e["FAKE_REMOTE"] = remote.as_posix()
    e["FAKE_LOG"] = log.as_posix()
    e["CLOUDSDK_CORE_PROJECT"] = "a-test-project"
    e["HOME"] = (tmp_path / "home").as_posix()
    return SimpleNs(env=e, remote=remote, log=log, dest=tmp_path / "copies", tmp=tmp_path)


class SimpleNs:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def run(ctx, *args):
    return subprocess.run([BASH, str(TOOL), "--dest", ctx.dest.as_posix(), *args], capture_output=True, text=True, env=ctx.env)


def test_it_copies_what_is_missing_checks_it_and_exits_clean(env):
    names = make_remote(env.remote, 6)
    done = run(env)
    assert done.returncode == 0, done.stdout + done.stderr
    assert sorted(p.name for p in env.dest.glob("backup-*.tar.age")) == names
    assert len(list(env.dest.glob("*.sha256"))) == 6
    assert "6 wanted, 6 not here yet" in done.stdout and "6 good, 0 bad" in done.stdout
    assert not (env.dest / "notes.txt").exists(), "only backups are copied"


def test_a_second_run_copies_only_the_new_ones(env):
    names = make_remote(env.remote, 4)
    assert run(env).returncode == 0
    folder = env.remote / "backups" / "sink" / "full"
    extra = f"backup-{stamp(9)}.tar.age"
    body = b"age-encryption.org/v1\n-> X25519 fake\nnew"
    (folder / extra).write_bytes(body)
    (folder / f"{extra}.sha256").write_text(f"{hashlib.sha256(body).hexdigest()}  {extra}\n", encoding="utf-8")
    env.log.write_text("", encoding="utf-8")
    done = run(env)
    assert done.returncode == 0 and "5 wanted, 1 not here yet" in done.stdout, done.stdout
    scp = [line for line in env.log.read_text(encoding="utf-8").splitlines() if "compute scp" in line]
    assert len(scp) == 1 and extra in scp[0] and names[0] not in scp[0], "nothing already here is copied again, and one file goes per call"


@pytest.mark.parametrize("kind", ["sum", "header"])
def test_a_damaged_or_foreign_copy_is_reported_and_the_exit_code_says_so(env, kind):
    names = make_remote(env.remote, 3, damage={None: None, "backup-" + stamp(1) + ".tar.age": kind})
    done = run(env)
    assert done.returncode == 1 and "2 good, 1 bad" in done.stdout
    assert "BAD" in done.stderr and names[1] in done.stderr


def test_newest_limits_the_copies_and_since_skips_the_older(env):
    names = make_remote(env.remote, 10)
    assert run(env, "--newest", "3").returncode == 0
    assert sorted(p.name for p in env.dest.glob("backup-*.tar.age")) == names[-3:]
    done = run(env, "--since", stamp(8))
    assert done.returncode == 0 and "2 wanted" in done.stdout, done.stdout
    assert run(env, "--since", "20991231T000000Z").returncode == 1, "no backup yet at that time"


def test_the_oldest_copies_are_pruned_beyond_keep(env):
    names = make_remote(env.remote, 8)
    done = run(env, "--keep", "5")
    assert done.returncode == 0 and "deleted 3 old copies" in done.stdout, done.stdout
    assert sorted(p.name for p in env.dest.glob("backup-*.tar.age")) == names[-5:]
    assert len(list(env.dest.glob("*.sha256"))) == 5


def test_it_only_ever_lists_on_the_vm(env):
    make_remote(env.remote, 2)
    assert run(env).returncode == 0
    ssh = [line for line in env.log.read_text(encoding="utf-8").splitlines() if "compute ssh" in line]
    assert len(ssh) == 1 and "--command cd backups/sink/full && ls -1" in ssh[0] and not any(w in ssh[0] for w in (" rm ", " mv ", " > ", "sudo")), ssh
    scp = [line for line in env.log.read_text(encoding="utf-8").splitlines() if "compute scp" in line]
    assert len(scp) == 2 and all(line.count("l3mon-app:") == 1 for line in scp), "one remote source per copy call (PuTTY takes one)"


def test_the_project_id_and_the_options_are_required_and_checked(env):
    make_remote(env.remote, 1)
    env.env.pop("CLOUDSDK_CORE_PROJECT")
    done = run(env)
    assert done.returncode != 0 and "CLOUDSDK_CORE_PROJECT" in done.stderr
    env.env["CLOUDSDK_CORE_PROJECT"] = "p"
    for bad in (["--newest", "0"], ["--newest", "x"], ["--keep", "-1"], ["--since", "yesterday"], ["--bogus"], ["--identity", "/no/such/key"]):
        assert run(env, *bad).returncode == 2, bad
    assert env.log.read_text(encoding="utf-8") == "", "a refused option sends nothing to the VM"


def test_the_remote_folder_must_be_a_plain_relative_path(env):
    make_remote(env.remote, 1)
    for bad in ("/etc", "../x", "a b", "a;b"):
        env.env["L3MON_BACKUP_REMOTE_DIR"] = bad
        assert run(env).returncode == 2, bad


@pytest.mark.skipif(shutil.which("age") is None or shutil.which("age-keygen") is None, reason="needs the age tool")
def test_identity_opens_the_newest_copy_for_real_and_a_wrong_key_is_a_problem(env):
    keys = env.tmp / "keys"
    keys.mkdir()
    mine, other = keys / "mine.key", keys / "other.key"
    for k in (mine, other):
        subprocess.run(["age-keygen", "-o", str(k)], capture_output=True, check=True)
    public = [line.split()[-1] for line in subprocess.run(["age-keygen", "-y", str(mine)], capture_output=True, text=True, check=True).stdout.splitlines() if line.startswith("age1")][0]
    payload = env.tmp / "payload"
    payload.mkdir()
    (payload / "db.sql").write_text("select 1;", encoding="utf-8")
    tar = env.tmp / "p.tar"
    subprocess.run(["tar", "-cf", str(tar), "-C", str(payload), "db.sql"], check=True)
    sealed = subprocess.run(["age", "-r", public, str(tar)], capture_output=True, check=True).stdout
    make_remote(env.remote, 2, content=lambda n: sealed)
    done = run(env, "--identity", str(mine))
    assert done.returncode == 0 and "opens with this computer's key" in done.stdout and "db.sql" in done.stdout, done.stdout + done.stderr
    done = run(env, "--identity", str(other))
    assert done.returncode == 1 and "does NOT open" in done.stderr


def test_the_script_is_valid_shell_and_executable_in_git():
    assert subprocess.run([BASH, "-n", str(TOOL)], capture_output=True).returncode == 0
    out = subprocess.run(["git", "ls-files", "-s", "tools/ops/pull-backups.sh"], cwd=ROOT, capture_output=True, text=True).stdout
    assert not out or out.startswith("100755"), "executable in git: " + out
