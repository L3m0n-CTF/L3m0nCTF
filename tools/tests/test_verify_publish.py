"""tools/ops/verify-publish.sh: a new build goes INTO the folder nginx serves (a bind mount follows the folder, so it is never replaced), files the
new build dropped are removed, and anything that is not a verification build is refused before a file is touched."""
from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from l3mon.verify import issue, site, store

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "ops" / "verify-publish.sh"
GIT_BASH = Path("C:/Program Files/Git/bin/bash.exe")
BASH = str(GIT_BASH) if GIT_BASH.exists() else shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None or (os.name == "nt" and not GIT_BASH.exists()), reason="needs bash (Git Bash on Windows)")


def build(tmp_path: Path, names: list[str], tag: str) -> Path:
    db = tmp_path / f"{tag}.sqlite"
    conn = store.connect(db, create=True)
    issue.issue(conn, issue_date="2026-11-30", org=names)
    conn.close()
    out = tmp_path / f"site-{tag}"
    site.build(db, out, base_url="https://verify.example.test")
    archive = tmp_path / f"{tag}.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        for p in sorted(out.rglob("*")):
            tar.add(p, arcname=str(p.relative_to(out)), recursive=False)
    return archive


def shell_path(p: Path) -> str:
    """Git Bash's form of a Windows path (GNU tar would read C:/... as a remote host "C"); on Linux the path as it is."""
    s = Path(p).resolve().as_posix()
    if os.name == "nt" and len(s) > 1 and s[1] == ":":
        s = f"/{s[0].lower()}{s[2:]}"
    return s


def publish(archive: Path, dest: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "L3MON_VERIFY_SITE_DIR"}
    return subprocess.run([BASH, shell_path(SCRIPT), shell_path(archive), shell_path(dest)], capture_output=True, text=True, timeout=120, env=env)


def files(d: Path) -> set[str]:
    return {p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file()}


def test_a_new_build_replaces_the_contents_but_keeps_the_folder(tmp_path):
    dest = tmp_path / "served"
    first, second = build(tmp_path, ["Jane Doe", "Alex Example"], "a"), build(tmp_path, ["Jane Doe"], "b")
    res = publish(first, dest)
    assert res.returncode == 0, res.stderr
    inode = dest.stat().st_ino
    a = files(dest)
    assert len([f for f in a if f.startswith("c/")]) == 2
    res = publish(second, dest)
    assert res.returncode == 0, res.stderr
    assert dest.stat().st_ino == inode, "the served folder itself must stay (Docker's bind mount follows it)"
    b = files(dest)
    with tarfile.open(second) as tar:
        assert b == {m.name for m in tar.getmembers() if m.isfile()}
    assert "1 old file(s) removed" in res.stdout or "old file(s) removed" in res.stdout


@pytest.mark.parametrize("spelling", ["{d}/", "{d}//", "{d}/./"])
def test_a_trailing_slash_or_odd_spelling_of_the_folder_never_empties_the_site(tmp_path, spelling):
    dest = tmp_path / "served"
    archive = build(tmp_path, ["Jane Doe"], "a")
    assert publish(archive, dest).returncode == 0
    before = files(dest)
    env = {k: v for k, v in os.environ.items() if k != "L3MON_VERIFY_SITE_DIR"}
    res = subprocess.run([BASH, shell_path(SCRIPT), shell_path(archive), spelling.format(d=shell_path(dest))], capture_output=True, text=True, timeout=120, env=env)
    assert res.returncode == 0, res.stderr
    assert files(dest) == before and "0 old file(s) removed" in res.stdout


def test_an_archive_that_is_not_a_build_is_refused_before_anything_changes(tmp_path):
    dest = tmp_path / "served"
    publish(build(tmp_path, ["Jane Doe"], "a"), dest)
    before = files(dest)
    junk = tmp_path / "junk.tgz"
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "index.html").write_text("hi", encoding="utf-8")
    with tarfile.open(junk, "w:gz") as tar:
        tar.add(tmp_path / "x" / "index.html", arcname="index.html")
    res = publish(junk, dest)
    assert res.returncode != 0 and "not a verification build" in res.stderr
    assert files(dest) == before


def test_a_folder_holding_something_else_is_never_touched(tmp_path):
    dest = tmp_path / "precious"
    dest.mkdir()
    (dest / "mine.txt").write_text("keep", encoding="utf-8")
    res = publish(build(tmp_path, ["Jane Doe"], "a"), dest)
    assert res.returncode != 0 and "refusing" in res.stderr
    assert files(dest) == {"mine.txt"}
