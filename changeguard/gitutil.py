"""Thin, dependency-free wrapper around the git CLI."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field

FIX_RE = re.compile(r"\b(fix(e[sd])?|bug(fix)?|defect|hotfix|regression|crash)\b", re.I)


class GitError(RuntimeError):
    pass


def git(repo, *args, check=True, input=None) -> str:
    p = subprocess.run(
        ["git", "-c", "core.quotepath=false", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", input=input,
    )
    if check and p.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout


def is_fix_subject(subject: str) -> bool:
    return bool(FIX_RE.search(subject))


@dataclass
class Commit:
    sha: str
    parents: list
    author: str
    ts: int
    subject: str
    files: dict = field(default_factory=dict)  # path -> (added, deleted)


def _parse_numstat(lines) -> dict:
    files = {}
    for ln in lines:
        if not ln.strip():
            continue
        parts = ln.split("\t", 2)
        if len(parts) != 3:
            continue
        a, d, path = parts
        files[path] = (int(a) if a != "-" else 0, int(d) if d != "-" else 0)
    return files


def read_commits(repo, rev="HEAD") -> list:
    """Non-merge commits reachable from rev, oldest first, with per-file line counts."""
    out = git(repo, "log", "--reverse", "--no-merges", "--no-renames", "--numstat",
              "--format=%x1e%H%x1f%P%x1f%ae%x1f%at%x1f%s", rev)
    commits = []
    for rec in out.split("\x1e"):
        if not rec.strip():
            continue
        lines = rec.split("\n")
        sha, parents, email, at, subject = lines[0].split("\x1f", 4)
        commits.append(Commit(sha, parents.split(), email.lower(), int(at), subject,
                              _parse_numstat(lines[1:])))
    return commits


def numstat_between(repo, a, b) -> dict:
    return _parse_numstat(git(repo, "diff", "--numstat", "--no-renames", a, b).split("\n"))


def merge_base(repo, a, b) -> str:
    return git(repo, "merge-base", a, b).strip()


def ls_tree(repo, rev) -> list:
    return [p for p in git(repo, "ls-tree", "-r", "--name-only", rev).split("\n") if p]


def read_blobs(repo, rev, paths) -> dict:
    if not paths:
        return {}
    inp = "".join(f"{rev}:{p}\n" for p in paths).encode()
    proc = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch"], input=inp,
                          capture_output=True)
    data, pos, out = proc.stdout, 0, {}
    for p in paths:
        nl = data.index(b"\n", pos)
        header = data[pos:nl].decode("utf-8", "replace")
        pos = nl + 1
        if header.endswith("missing"):
            continue
        size = int(header.split()[2])
        out[p] = data[pos:pos + size].decode("utf-8", "replace")
        pos += size + 1
    return out


def old_side_hunks(repo, a, b) -> list:
    """(path, start, length) of lines removed or modified between a and b, in a's numbering."""
    out = git(repo, "diff", "-U0", "--no-renames", a, b)
    path, header, res = None, False, []
    for ln in out.split("\n"):
        if ln.startswith("diff --git"):
            header, path = True, None
        elif header and ln.startswith("--- "):
            path = ln[6:].split("\t")[0] if ln.startswith("--- a/") else None
        elif ln.startswith("@@"):
            header = False
            m = re.match(r"@@ -(\d+)(?:,(\d+))? \+", ln)
            if m and path:
                length = int(m.group(2)) if m.group(2) is not None else 1
                if length > 0:
                    res.append((path, int(m.group(1)), length))
    return res


def blame_range(repo, rev, path, start, length) -> list:
    out = git(repo, "blame", "--porcelain", "-L", f"{start},+{length}", rev, "--", path,
              check=False)
    return [m.group(1) for ln in out.split("\n")
            if (m := re.match(r"^([0-9a-f]{40}) \d+ \d+", ln))]
